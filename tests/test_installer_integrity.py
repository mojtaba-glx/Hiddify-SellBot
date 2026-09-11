"""Stage-8 installer integrity tests.

Everything runs against a TemporaryDirectory with a FAKE token; install.sh
is never executed for real (only `bash -n`), no network, no real .env or
database. install.sh functions are exercised by sourcing a copy with the
final `main "$@"` invocation stripped out.
"""

import os
import re
import sys
import shutil
import stat
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAKE_TOKEN = "123456789:AA_STAGE8_FAKE_TOKEN_NOT_REAL_1234567890"

FORBIDDEN_FRAGMENTS = (FAKE_TOKEN, "AA_STAGE8_FAKE_TOKEN", "123456789:")


def _load_installer_functions(tmp_root: Path):
    """Copy install.sh into a sandbox, strip the final `main "$@"` call, and
    source it so its functions become available without running the CLI."""
    src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
    harness = src.replace('main "$@"', "# main disabled for tests")
    installer_copy = tmp_root / "installer_lib.sh"
    installer_copy.write_text(harness, encoding="utf-8")
    # make the copy's ROOT_DIR point at the sandbox by relocating: we instead
    # keep ROOT_DIR at the real project (needed for Shared.secure_io import)
    # and only sandbox the file paths we pass explicitly.
    return installer_copy


def _bash(script: str, cwd: Path = None, stdin: str = None, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd) if cwd else None,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return proc


class _InstallerHarness(unittest.TestCase):
    """Base: prepares a sandbox .env + a sourced copy of install.sh."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sandbox = Path(self._tmp.name)
        self.installer = _load_installer_functions(self.sandbox)

        self.shared_dir = self.sandbox / "Shared"
        self.shared_dir.mkdir()
        (self.shared_dir / "secure_io.py")  # real import comes from PROJECT_ROOT

        self.env_file = self.sandbox / ".env"
        self.env_file.write_text(
            "# installer test comment\n"
            "\n"
            "OLD_KEY=1\n"
            "ADMIN_BOT_TOKEN=old-placeholder\n"
            "ANOTHER=keep\n",
            encoding="utf-8",
        )
        os.chmod(self.env_file, 0o600)

    def run_set_env_var(self, key: str, value: str):
        """Call the real set_env_var from the sourced installer copy.

        The fake token is piped to the harness via stdin (never embedded in
        the bash -c text or argv). The sourced copy derives ROOT_DIR from its
        own location (sandbox), so we re-export the real project ROOT_DIR —
        set_env_var reads it at call time to import Shared.secure_io.
        """
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
export ROOT_DIR="{PROJECT_ROOT}"
set_env_var "{key}" "$(cat)" "{self.env_file}"
'''
        return _bash(script, cwd=PROJECT_ROOT, stdin=value)

    def run_prompt_secret(self, current: str, typed_input: str, result_file: Path = None):
        """Run prompt_secret_required (pass-by-reference) under a pty so
        `read -r -s -p` behaves like a real terminal (prompts are only
        written to a tty). The result variable is written to result_file
        for inspection; stdout/stderr are returned untouched."""
        import pty
        result_file = result_file or (self.sandbox / "prompt_result")
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
result=""
prompt_secret_required result "ADMIN_BOT_TOKEN" "Admin bot token" "{current}"
printf '%s' "$result" > "{result_file}"
'''
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bash", "-c", script], cwd=str(PROJECT_ROOT),
            stdin=slave_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        os.close(slave_fd)
        try:
            os.write(master_fd, typed_input.encode("utf-8"))
            out, err = proc.communicate(timeout=30)
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
        return types.SimpleNamespace(
            returncode=proc.returncode,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            result_file=result_file,
        )


class SetEnvVarTests(_InstallerHarness):
    def test_01_writes_fake_token_via_installer_path(self):
        proc = self.run_set_env_var("ADMIN_BOT_TOKEN", FAKE_TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.env_file.read_text(encoding="utf-8")
        self.assertIn(f"ADMIN_BOT_TOKEN={FAKE_TOKEN}", content)

    def test_02_preserves_comments_blanks_unrelated_keys(self):
        proc = self.run_set_env_var("ADMIN_BOT_TOKEN", FAKE_TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.env_file.read_text(encoding="utf-8")
        self.assertIn("# installer test comment", content)
        self.assertIn("\n\nOLD_KEY=1\n", content)
        self.assertIn("ANOTHER=keep", content)
        self.assertNotIn("old-placeholder", content)

    def test_03_env_and_lock_mode_600(self):
        proc = self.run_set_env_var("ADMIN_BOT_TOKEN", FAKE_TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        lock = self.sandbox / ".env.lock"
        self.assertTrue(lock.exists())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)

    def test_04_no_fake_token_in_stdout_stderr(self):
        proc = self.run_set_env_var("ADMIN_BOT_TOKEN", FAKE_TOKEN)
        combined = proc.stdout + proc.stderr
        for fragment in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(fragment, combined,
                             f"secret fragment {fragment!r} leaked into process output")

    def test_05_no_sed_i_in_set_env_var(self):
        src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        match = re.search(r"set_env_var\(\) \{.*?\n\}", src, re.S)
        self.assertIsNotNone(match, "set_env_var function not found")
        body = match.group(0)
        self.assertNotIn("sed -i", body)
        # the writer must delegate to the shared atomic utility
        self.assertIn("atomic_update_env", body)

    def test_06_value_never_in_argv(self):
        """Run with a wrapper python3 that records argv and forwards to the
        real interpreter (sys.executable); the value must not appear in any
        argv entry. The token is piped via stdin, never embedded in the
        bash script text."""
        wrapper_dir = self.sandbox / "bin"
        wrapper_dir.mkdir()
        argv_log = self.sandbox / "argv.log"
        wrapper = wrapper_dir / "python3"
        wrapper.write_text(
            f'#!/bin/bash\n'
            f'printf "%s\\0" "$@" >> "{argv_log}"\n'
            f'printf "\\0" >> "{argv_log}"\n'
            f'exec "{sys.executable}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{wrapper_dir}:{env['PATH']}"
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
export ROOT_DIR="{PROJECT_ROOT}"
set_env_var "ADMIN_BOT_TOKEN" "$(cat)" "{self.env_file}"
'''
        proc = subprocess.run(["bash", "-c", script], cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, env=env,
                              input=FAKE_TOKEN, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv_dump = argv_log.read_text(encoding="utf-8", errors="replace")
        for fragment in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(fragment, argv_dump,
                             "secret value reached python argv")

    def test_writer_failure_returns_nonzero_without_secret(self):
        """A validation failure must surface as a non-zero exit and never
        print the secret (configure_env aborts on this)."""
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
export ROOT_DIR="{PROJECT_ROOT}"
set_env_var "BAD KEY" "$(cat)" "{self.env_file}"
'''
        proc = _bash(script, cwd=PROJECT_ROOT, stdin=FAKE_TOKEN)
        self.assertNotEqual(proc.returncode, 0)
        for fragment in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(fragment, proc.stdout + proc.stderr)


class PromptSecretTests(_InstallerHarness):
    def test_07_hidden_read_used_for_token_prompts(self):
        src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        match = re.search(r"prompt_secret_required\(\) \{.*?\n\}", src, re.S)
        self.assertIsNotNone(match, "prompt_secret_required not found")
        body = match.group(0)
        self.assertIn("read -r -s -n 1", body)
        self.assertNotIn("read -rp", body)
        # pass-by-reference contract
        self.assertIn("printf -v", body)
        # newline after entry goes to stderr, not stdout
        self.assertIn("printf '\\n' >&2", body)

    def test_08_current_token_never_shown_in_prompt(self):
        result_file = self.sandbox / "result08"
        proc = self.run_prompt_secret(FAKE_TOKEN, "typed-new-token\n", result_file)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        combined = proc.stdout + proc.stderr
        for fragment in FORBIDDEN_FRAGMENTS:
            self.assertNotIn(fragment, combined,
                             "current token (or fragment) shown in prompt")
        self.assertIn("[configured]", combined)
        # pass-by-reference result: no trailing newline on stdout, exact value
        written = result_file.read_text(encoding="utf-8")
        self.assertEqual(written, "typed-new-token")

    def test_09_empty_input_keeps_previous_value(self):
        result_file = self.sandbox / "result09"
        proc = self.run_prompt_secret(FAKE_TOKEN, "\n", result_file)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # exact, no strip/splitlines tricks: the result file must equal the
        # previous token exactly
        self.assertEqual(result_file.read_text(encoding="utf-8"), FAKE_TOKEN)

    def test_prompt_writes_nothing_to_stdout(self):
        """prompt must not emit the token, the typed value, or any extra
        newline on stdout — stdout stays clean for pass-by-reference."""
        result_file = self.sandbox / "result_clean"
        proc = self.run_prompt_secret("", "brand-new-token\n", result_file)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("brand-new-token", proc.stderr)
        self.assertEqual(result_file.read_text(encoding="utf-8"), "brand-new-token")

    def test_prompt_explains_hidden_input_and_confirms_receipt(self):
        result_file = self.sandbox / "result_guidance"
        proc = self.run_prompt_secret("", "brand-new-token\n", result_file)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("token input is masked", proc.stderr)
        self.assertIn("press Enter", proc.stderr)
        self.assertIn("*", proc.stderr)
        self.assertIn("received securely", proc.stderr)
        self.assertNotIn("brand-new-token", proc.stdout + proc.stderr)

    def test_warning_for_empty_input_goes_to_stderr(self):
        # empty entry with no previous value: the loop re-prompts; feed an
        # empty line then a real value; the warning must appear on stderr.
        result_file = self.sandbox / "result_warn"
        proc = self.run_prompt_secret("", "\nsecond-try\n", result_file)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("cannot be empty", proc.stderr)
        self.assertEqual(result_file.read_text(encoding="utf-8"), "second-try")

    def test_interactive_menu_exposes_config_command(self):
        src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('echo "12) change bot tokens / admin ID"', src)
        self.assertRegex(src, r"(?m)^\s*12\) _run_menu_cmd config ;;")

    def test_core_bot_tokens_must_be_valid_and_distinct(self):
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
ADMIN_BOT_TOKEN="123456789:AA_VALID_ADMIN_TOKEN_1234567890"
USER_BOT_TOKEN="123456789:AA_VALID_USER_TOKEN_12345678901"
AGENT_BOT_TOKEN="123456789:AA_VALID_AGENT_TOKEN_123456789"
validate_core_bot_tokens
USER_BOT_TOKEN="$ADMIN_BOT_TOKEN"
if validate_core_bot_tokens; then exit 91; fi
USER_BOT_TOKEN="invalid"
if validate_core_bot_tokens; then exit 92; fi
'''
        proc = _bash(script, cwd=PROJECT_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for token_fragment in ("AA_VALID_ADMIN", "AA_VALID_USER", "AA_VALID_AGENT"):
            self.assertNotIn(token_fragment, proc.stdout + proc.stderr)

    def test_config_reports_locked_env_with_sudo_guidance(self):
        src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        match = re.search(r"configure_env\(\) \{.*?\n\}", src, re.S)
        self.assertIsNotNone(match, "configure_env function not found")
        body = match.group(0)
        self.assertIn('[ ! -w "$ENV_FILE" ]', body)
        self.assertIn("not writable", body)
        self.assertIn("sudo", body)


class SnapshotTests(_InstallerHarness):
    def _prepare_sandbox_project(self):
        """Build a minimal fake project layout for create_snapshot_backup."""
        (self.shared_dir / "hiddify_sellbot.db").write_bytes(b"sqlite-fake")
        (self.shared_dir / "servers.json").write_text("{}", encoding="utf-8")
        (self.shared_dir / "plans.json").write_text("{}", encoding="utf-8")
        (self.shared_dir / "agency.db").write_bytes(b"sqlite-fake")
        (self.sandbox / "customer_bot.db").write_bytes(b"sqlite-fake")
        agent_dir = self.sandbox / "AgentBot"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / "agent_bot.db").write_bytes(b"sqlite-fake")
        receipts = self.sandbox / "Receiptions"
        receipts.mkdir(exist_ok=True)
        (receipts / "r1.png").write_bytes(b"png")

    def _run_snapshot(self, prefix="Test"):
        self._prepare_sandbox_project()
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
ROOT_DIR="{self.sandbox}"
ENV_FILE="{self.sandbox}/.env"
LOG_DIR="{self.sandbox}/logs"
BACKUP_DIR="{self.sandbox}/backups"
RECEIPT_DIR="{self.sandbox}/Receiptions"
ADMIN_LOG_FILE="$LOG_DIR/adminbot.log"
USER_LOG_FILE="$LOG_DIR/userbot.log"
AGENT_LOG_FILE="$LOG_DIR/agentbot.log"
CUSTOMER_LOG_FILE="$LOG_DIR/customerbot.log"
cd "{self.sandbox}"
create_snapshot_backup "{prefix}"
'''
        return _bash(script, cwd=self.sandbox)

    def test_10_backup_dir_0700_and_snapshot_0600(self):
        proc = self._run_snapshot()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        backups = self.sandbox / "backups"
        self.assertTrue(backups.is_dir())
        self.assertEqual(stat.S_IMODE(backups.stat().st_mode) & 0o777, 0o700)
        snapshots = list(backups.glob("Test_*.tar.gz"))
        self.assertEqual(len(snapshots), 1, proc.stdout + proc.stderr)
        self.assertEqual(stat.S_IMODE(snapshots[0].stat().st_mode) & 0o777, 0o600)

    def test_11_incomplete_snapshot_removed_on_tar_failure(self):
        self._prepare_sandbox_project()
        script = f'''
set -Eeuo pipefail
source "{self.installer}"
ROOT_DIR="{self.sandbox}"
ENV_FILE="{self.sandbox}/.env"
LOG_DIR="{self.sandbox}/logs"
BACKUP_DIR="{self.sandbox}/backups"
RECEIPT_DIR="{self.sandbox}/Receiptions"
ADMIN_LOG_FILE="$LOG_DIR/adminbot.log"
USER_LOG_FILE="$LOG_DIR/userbot.log"
AGENT_LOG_FILE="$LOG_DIR/agentbot.log"
CUSTOMER_LOG_FILE="$LOG_DIR/customerbot.log"
cd "{self.sandbox}"
tar() {{ return 1; }}
create_snapshot_backup "Broken"
rc=$?
'''
        proc = _bash(script, cwd=self.sandbox)
        # create_snapshot_backup must fail (non-zero) when tar fails
        self.assertNotEqual(proc.returncode, 0,
                            "snapshot must fail when tar fails")
        leftovers = list((self.sandbox / "backups").glob("Broken_*.tar.gz"))
        self.assertEqual(leftovers, [], "incomplete snapshot must be removed")

    def test_snapshot_contains_env_for_recovery(self):
        proc = self._run_snapshot()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        snapshot = next((self.sandbox / "backups").glob("Test_*.tar.gz"))
        listing = subprocess.run(["tar", "-tzf", str(snapshot)],
                                 capture_output=True, text=True, check=True,
                                 timeout=30).stdout.splitlines()
        # remove the exact "./" prefix only — lstrip would corrupt ".env"
        names = [p.removeprefix("./") for p in listing]
        self.assertIn(".env", names,
                      "installer snapshot must keep .env for recovery")


class GitHygieneTests(unittest.TestCase):
    def test_12_env_ignored_and_example_not_ignored(self):
        gi = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertRegex(gi, r"(?m)^\.env$")
        self.assertRegex(gi, r"(?m)^!\.env\.example$")
        self.assertRegex(gi, r"(?m)^\.env\.lock$")
        self.assertRegex(gi, r"(?m)^\.env\.\*\.tmp$")
        self.assertRegex(gi, r"(?m)^\.\.env\.\*\.tmp$")
        # runtime artifacts still listed
        for pattern in ("*.db", "*.db-wal", "*.db-shm", "logs/", "backups/",
                        "Receiptions/", "*.pid", "__pycache__/", "*.pyc", "venv/"):
            self.assertRegex(gi, re.escape(pattern))
        # behavioural check: ignore rules + visibility via git status
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            shutil.copy(PROJECT_ROOT / ".gitignore", repo / ".gitignore")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=30)
            (repo / "README.md").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, timeout=30)
            (repo / ".env").write_text("x=1", encoding="utf-8")
            (repo / ".env.example").write_text("x=\n", encoding="utf-8")
            (repo / ".env.lock").write_text("", encoding="utf-8")
            (repo / ".env.stage8.tmp").write_text("", encoding="utf-8")
            (repo / "..env.stage8.tmp").write_text("", encoding="utf-8")

            def status_lines():
                out = subprocess.run(
                    ["git", "status", "--short", "--untracked-files=all"],
                    cwd=repo, capture_output=True, text=True, check=True,
                    timeout=30).stdout
                return [ln[3:].strip() for ln in out.splitlines() if ln.strip()]

            shown = status_lines()
            self.assertNotIn(".env", shown, ".env must not appear in git status")
            self.assertNotIn(".env.lock", shown, ".env.lock must not appear")
            self.assertNotIn(".env.stage8.tmp", shown, ".env.*.tmp must not appear")
            self.assertNotIn("..env.stage8.tmp", shown, "..env.*.tmp must not appear")
            self.assertIn(".env.example", shown,
                          ".env.example must be visible and trackable")
            self.assertIn("README.md", shown)

    def test_13_no_runtime_artifacts_tracked(self):
        if not (PROJECT_ROOT / ".git").exists():
            self.skipTest("no .git directory in this environment")
        out = subprocess.run(
            ["git", "ls-files"], cwd=PROJECT_ROOT, capture_output=True, text=True,
            check=True).stdout.splitlines()
        bad = [f for f in out if re.search(
            r"(^|/)\.env$|\.env\.lock$|\.db$|\.db-wal$|\.db-shm$|^logs/|^backups/|^Receiptions/|\.pid$|__pycache__/|\.pyc$",
            f)]
        self.assertEqual(bad, [], f"runtime artifacts tracked in git: {bad}")


class InstallerSyntaxTests(unittest.TestCase):
    def test_14_bash_n_install_sh(self):
        proc = subprocess.run(["bash", "-n", str(PROJECT_ROOT / "install.sh")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_install_sh_tokens_use_hidden_prompt(self):
        src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("prompt_secret_required ADMIN_BOT_TOKEN", src)
        self.assertIn("prompt_secret_required USER_BOT_TOKEN", src)
        self.assertIn("prompt_secret_required AGENT_BOT_TOKEN", src)
        # token values must NOT be captured via command substitution anymore
        self.assertNotIn('ADMIN_BOT_TOKEN="$(prompt_secret_required', src)
        self.assertNotIn('USER_BOT_TOKEN="$(prompt_secret_required', src)
        self.assertNotIn('AGENT_BOT_TOKEN="$(prompt_secret_required', src)
        # ADMIN_ID stays a plain prompt
        self.assertIn('prompt_required "ADMIN_ID"', src)

    def test_configure_env_aborts_on_write_failure_without_secret(self):
        src = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")
        match = re.search(r"configure_env\(\) \{.*?\n\}", src, re.S)
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn("failed to update .env", body)


if __name__ == "__main__":
    unittest.main()

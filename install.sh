#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ENV_FILE="$ROOT_DIR/.env"
VENV_DIR="$ROOT_DIR/venv"
LOG_DIR="$ROOT_DIR/logs"
BACKUP_DIR="$ROOT_DIR/backups"
RECEIPT_DIR="$ROOT_DIR/Receiptions"
VERSION_FILE="$ROOT_DIR/VERSION"

ADMIN_MAIN="$ROOT_DIR/AdminBot/main.py"
USER_MAIN="$ROOT_DIR/UserBot/main.py"
AGENT_MAIN="$ROOT_DIR/AgentBot/main.py"
CUSTOMER_MAIN="$ROOT_DIR/CustomerBot/main.py"

ADMIN_PID_FILE="$LOG_DIR/adminbot.pid"
USER_PID_FILE="$LOG_DIR/userbot.pid"
AGENT_PID_FILE="$LOG_DIR/agentbot.pid"
CUSTOMER_PID_FILE="$LOG_DIR/customerbot.pid"
ADMIN_LOG_FILE="$LOG_DIR/adminbot.log"
USER_LOG_FILE="$LOG_DIR/userbot.log"
AGENT_LOG_FILE="$LOG_DIR/agentbot.log"
CUSTOMER_LOG_FILE="$LOG_DIR/customerbot.log"
SYSTEMD_ADMIN_UNIT="hiddify-sellbot-admin.service"
SYSTEMD_USER_UNIT="hiddify-sellbot-user.service"
SYSTEMD_AGENT_UNIT="hiddify-sellbot-agent.service"
SYSTEMD_CUSTOMER_UNIT="hiddify-sellbot-customer.service"
SYSTEMD_ADMIN_UNIT_FILE="/etc/systemd/system/${SYSTEMD_ADMIN_UNIT}"
SYSTEMD_USER_UNIT_FILE="/etc/systemd/system/${SYSTEMD_USER_UNIT}"
SYSTEMD_AGENT_UNIT_FILE="/etc/systemd/system/${SYSTEMD_AGENT_UNIT}"
SYSTEMD_CUSTOMER_UNIT_FILE="/etc/systemd/system/${SYSTEMD_CUSTOMER_UNIT}"
HIDDIFY_STABILIZER_ENV_KEY="HIDDIFY_CREATE_USER_STABILIZE_MODE"

APP_VERSION="dev"
UPDATE_SOURCE_STATUS="not-run"

refresh_app_version() {
  APP_VERSION="dev"
  if [ -f "$VERSION_FILE" ]; then
    APP_VERSION="$(tr -d ' \t\r\n' < "$VERSION_FILE")"
    [ -n "$APP_VERSION" ] || APP_VERSION="dev"
  fi
}
refresh_app_version

ADMIN_ID=""
ADMIN_BOT_TOKEN=""
USER_BOT_TOKEN=""
AGENT_BOT_TOKEN=""
SUB_BOT_USERNAME=""
ENV_CONFIGURED_IN_RUN=0
ENV_WAS_MISSING=0

_green() { printf "\033[32m%s\033[0m\n" "$*"; }
_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
_red() { printf "\033[31m%s\033[0m\n" "$*"; }
_blue() { printf "\033[34m%s\033[0m\n" "$*"; }

usage() {
  cat <<'USAGE'
Usage:
  ./install.sh [command]

Main commands (recommended):
  panel           Open interactive panel (recommended for daily use)
  install         First-time setup
  update          Update code + dependencies + restart bots
  restart         Restart bots
  status          Show bot status
  autostart       Open interactive autostart manager
  help            Show this help

Advanced commands:
  menu, update-force, reinstall, start, stop, config
  diag, logs, ssl, stabilizer, uninstall, factory-reset, version
  autostart-on, autostart-off, autostart-status, autostart-rm
  stabilizer-toggle, stabilizer-update, stabilizer-off, stabilizer-status

Notes:
  - Running ./install.sh with no args opens interactive menu (TTY mode)
  - You can also open panel explicitly: ./install.sh panel
  - On server, use: sudo ./install.sh panel
  - install/update automatically install dependencies from requirements.txt
USAGE
}

ensure_dirs() {
  mkdir -p "$LOG_DIR" "$BACKUP_DIR" "$RECEIPT_DIR"
  touch "$ADMIN_LOG_FILE" "$USER_LOG_FILE" "$AGENT_LOG_FILE" "$CUSTOMER_LOG_FILE"
}

load_env_file() {
  local env_file="$1"
  [ -f "$env_file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#export }"
    [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*= ]] && continue
    local key="${line%%=*}"
    key="$(printf '%s' "$key" | sed -E 's/[[:space:]]+$//')"
    local value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="$(printf '%s' "$value" | sed -E 's/[[:space:]]+$//')"
    if [[ "$value" =~ ^\".*\"$ ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$env_file"
}

set_env_var() {
  local key="$1"
  local value="$2"
  local file="$3"
  local escaped
  escaped="$(printf '%s' "$value" | sed -e 's/[\/&]/\\&/g')"
  if grep -qE "^${key}=" "$file"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$file"
  else
    printf "%s=%s\n" "$key" "$value" >> "$file"
  fi
}

prompt_required() {
  local key="$1"
  local prompt="$2"
  local current="${3:-}"
  local value=""

  while true; do
    if [ -n "$current" ]; then
      read -rp "$prompt [$current]: " value
      value="${value:-$current}"
    else
      read -rp "$prompt: " value
    fi
    if [ -n "$value" ]; then
      printf "%s" "$value"
      return 0
    fi
    _yellow "WARN: $key cannot be empty."
  done
}

configure_env() {
  ensure_dirs
  touch "$ENV_FILE"
  load_env_file "$ENV_FILE" || true

  if [ ! -t 0 ]; then
    _red "ERROR: config mode requires an interactive terminal."
    _yellow "Please set these keys manually in .env: ADMIN_ID, ADMIN_BOT_TOKEN, USER_BOT_TOKEN, AGENT_BOT_TOKEN"
    return 1
  fi

  _blue "Configuring required .env values"
  ADMIN_ID="$(prompt_required "ADMIN_ID" "Admin numeric ID" "${ADMIN_ID:-}")"
  ADMIN_BOT_TOKEN="$(prompt_required "ADMIN_BOT_TOKEN" "Admin bot token" "${ADMIN_BOT_TOKEN:-}")"
  USER_BOT_TOKEN="$(prompt_required "USER_BOT_TOKEN" "User bot token" "${USER_BOT_TOKEN:-}")"
  AGENT_BOT_TOKEN="$(prompt_required "AGENT_BOT_TOKEN" "Agent bot token" "${AGENT_BOT_TOKEN:-}")"
  set_env_var "ADMIN_ID" "$ADMIN_ID" "$ENV_FILE"
  set_env_var "ADMIN_BOT_TOKEN" "$ADMIN_BOT_TOKEN" "$ENV_FILE"
  set_env_var "USER_BOT_TOKEN" "$USER_BOT_TOKEN" "$ENV_FILE"
  set_env_var "AGENT_BOT_TOKEN" "$AGENT_BOT_TOKEN" "$ENV_FILE"

  ENV_CONFIGURED_IN_RUN=1
  _green "OK: .env updated."
}

check_required_env() {
  local allow_prompt="${1:-0}"  # 1=may ask interactive config, 0=never prompt
  load_env_file "$ENV_FILE" || true
  local missing=()
  [ -n "${ADMIN_ID:-}" ] || missing+=("ADMIN_ID")
  [ -n "${ADMIN_BOT_TOKEN:-}" ] || missing+=("ADMIN_BOT_TOKEN")
  [ -n "${USER_BOT_TOKEN:-}" ] || missing+=("USER_BOT_TOKEN")

  if [ "${#missing[@]}" -gt 0 ]; then
    ENV_WAS_MISSING=1
    _yellow "WARN: missing required .env keys: ${missing[*]}"
    if [ "$allow_prompt" = "1" ] && [ -t 0 ]; then
      configure_env || return 1
      load_env_file "$ENV_FILE" || true
      [ -n "${ADMIN_ID:-}" ] && [ -n "${ADMIN_BOT_TOKEN:-}" ] && [ -n "${USER_BOT_TOKEN:-}" ] && return 0
    fi
    _red "ERROR: please complete .env and run again (or run: ./install.sh config)."
    return 1
  fi
}

send_telegram_message() {
  local token="$1"
  local chat_id="$2"
  local text="$3"
  local result code body

  if [ -z "$token" ] || [ -z "$chat_id" ]; then
    return 1
  fi

  if ! command -v curl >/dev/null 2>&1; then
    _yellow "WARN: curl not found; skipping setup welcome message."
    return 1
  fi

  result="$(curl -sS -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "text=${text}" \
    --data-urlencode "disable_web_page_preview=true" \
    --connect-timeout 12 \
    --max-time 20 \
    -w '\n%{http_code}' || true)"

  code="${result##*$'\n'}"
  body="${result%$'\n'*}"
  [ "$code" = "200" ] && printf '%s' "$body" | grep -q '"ok":true'
}

send_first_install_welcome() {
  load_env_file "$ENV_FILE" || true
  local msg_admin msg_user msg_agent
  local admin_ok=0
  local user_ok=0
  local agent_ok=0

  [ -n "${ADMIN_ID:-}" ] || return 0
  [ -n "${ADMIN_BOT_TOKEN:-}" ] || return 0

  msg_admin="✅ نصب و راه‌اندازی Hiddify-SellBot با موفقیت انجام شد.
🤖 ربات ادمین تنظیم و اجرا شد.
🔖 نسخه: ${APP_VERSION}"

  if send_telegram_message "$ADMIN_BOT_TOKEN" "$ADMIN_ID" "$msg_admin"; then
    admin_ok=1
  fi

  if [ -n "${USER_BOT_TOKEN:-}" ]; then
    msg_user="✅ نصب و راه‌اندازی Hiddify-SellBot با موفقیت انجام شد.
👤 توکن ربات کاربران تنظیم شد.
🔖 نسخه: ${APP_VERSION}"
    if send_telegram_message "$USER_BOT_TOKEN" "$ADMIN_ID" "$msg_user"; then
      user_ok=1
    fi
  fi

  if [ -n "${AGENT_BOT_TOKEN:-}" ]; then
    msg_agent="✅ نصب و راه‌اندازی Hiddify-SellBot با موفقیت انجام شد.
🤝 توکن ربات نمایندگی تنظیم شد.
🔖 نسخه: ${APP_VERSION}"
    if send_telegram_message "$AGENT_BOT_TOKEN" "$ADMIN_ID" "$msg_agent"; then
      agent_ok=1
    fi
  fi

  if [ "$admin_ok" -eq 1 ] || [ "$user_ok" -eq 1 ] || [ "$agent_ok" -eq 1 ]; then
    _green "OK: first-install welcome message sent."
  else
    _yellow "WARN: could not deliver welcome message to ADMIN_ID."
    _yellow "Hint: open the bot in Telegram and press /start, then run ./install.sh install again."
  fi
}

install_system_dependencies() {
  local need_install=0
  command -v python3 >/dev/null 2>&1 || need_install=1
  command -v pip3 >/dev/null 2>&1 || need_install=1
  command -v rg >/dev/null 2>&1 || need_install=1

  if [ "$need_install" -eq 0 ]; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    _red "ERROR: required system tools are missing and apt-get is unavailable."
    _yellow "Install manually: python3 python3-venv python3-pip git ripgrep"
    return 1
  fi

  _blue "Installing system dependencies"
  local apt_cmd=()
  if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    apt_cmd=(apt-get)
  elif command -v sudo >/dev/null 2>&1; then
    apt_cmd=(sudo apt-get)
  else
    _red "ERROR: need root privileges (or sudo) to install system packages."
    return 1
  fi

  ${apt_cmd[@]} update
  ${apt_cmd[@]} install -y python3 python3-venv python3-pip git ca-certificates curl ripgrep
}

setup_venv_and_requirements() {
  install_system_dependencies
  ensure_dirs

  if [ ! -d "$VENV_DIR" ]; then
    _blue "Creating virtual environment"
    python3 -m venv "$VENV_DIR"
  fi

  _blue "Installing/updating Python dependencies"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  if [ -f "$ROOT_DIR/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"
  else
    _yellow "WARN: requirements.txt not found; skipping pip install"
  fi
  _green "OK: dependencies installed."
}

create_snapshot_backup() {
  local prefix="$1"
  ensure_dirs
  local ts backup_file
  ts="$(date '+%d-%m-%Y_%H-%M-%S')"
  backup_file="$BACKUP_DIR/${prefix}_${ts}.tar.gz"

  tar -czf "$backup_file" \
    --ignore-failed-read \
    .env \
    Shared/hiddify_sellbot.db \
    Shared/servers.json \
    Shared/plans.json \
    Shared/agency.db \
    CustomerBot/customer_bot.db \
    AgentBot/agent_bot.db \
    Receiptions \
    2>/dev/null || true

  if [ -f "$backup_file" ]; then
    _green "OK: backup created: $backup_file"
  else
    _yellow "WARN: backup was not created."
  fi
}

list_local_change_paths() {
  {
    git -C "$ROOT_DIR" diff --name-only || true
    git -C "$ROOT_DIR" diff --cached --name-only || true
    git -C "$ROOT_DIR" ls-files --others --exclude-standard || true
  } | sed '/^[[:space:]]*$/d' | sort -u
}

RUNTIME_GIT_PRESERVE_PATHS=(
  "Shared/servers.json"
  "Shared/plans.json"
  "Shared/agency.db"
  "CustomerBot/customer_bot.db"
  "AgentBot/agent_bot.db"
)

create_runtime_git_preserve_snapshot() {
  local snapshot_dir=""
  snapshot_dir="$(mktemp -d "${TMPDIR:-/tmp}/hsb-runtime-preserve.XXXXXX")"
  local rel=""
  local copied=0
  for rel in "${RUNTIME_GIT_PRESERVE_PATHS[@]}"; do
    if [ -f "$ROOT_DIR/$rel" ]; then
      mkdir -p "$snapshot_dir/$(dirname "$rel")"
      cp -a "$ROOT_DIR/$rel" "$snapshot_dir/$rel"
      copied=1
    fi
  done
  if [ "$copied" -eq 1 ]; then
    printf '%s' "$snapshot_dir"
  else
    rm -rf "$snapshot_dir"
    printf ''
  fi
}

restore_runtime_git_preserve_snapshot() {
  local snapshot_dir="${1:-}"
  [ -n "$snapshot_dir" ] || return 0
  [ -d "$snapshot_dir" ] || return 0

  local rel=""
  local restored=0
  for rel in "${RUNTIME_GIT_PRESERVE_PATHS[@]}"; do
    if [ -f "$snapshot_dir/$rel" ]; then
      mkdir -p "$(dirname "$ROOT_DIR/$rel")"
      cp -a "$snapshot_dir/$rel" "$ROOT_DIR/$rel"
      restored=1
    fi
  done

  rm -rf "$snapshot_dir"
  if [ "$restored" -eq 1 ]; then
    _blue "Runtime data restored after git sync (servers/plans)."
  fi
}

is_runtime_local_path() {
  local path="$1"
  case "$path" in
    .env|.env.bak*|.env.*.bak|logs|logs/*|backups|backups/*|Receiptions|Receiptions/*)
      return 0
      ;;
    Shared/hiddify_sellbot.db|Shared/data.db|Shared/servers.json|Shared/plans.json|Shared/*.db|Shared/*.db-*|Shared/agency.db|CustomerBot/customer_bot.db|AgentBot/agent_bot.db)
      return 0
      ;;
    *.pid|*.log|*.bak|*.tmp|Backup_Bot_*|Backup_All_*|Pre*.tar.gz|Pre*.zip)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

list_non_runtime_local_changes() {
  local path=""
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    if ! is_runtime_local_path "$path"; then
      echo "$path"
    fi
  done < <(list_local_change_paths)
}

list_runtime_local_changes() {
  local path=""
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    if is_runtime_local_path "$path"; then
      echo "$path"
    fi
  done < <(list_local_change_paths)
}

update_source_if_git() {
  UPDATE_SOURCE_STATUS="not-run"

  if ! command -v git >/dev/null 2>&1; then
    _yellow "WARN: git is not installed; skipping source update."
    UPDATE_SOURCE_STATUS="skipped-no-git"
    return 0
  fi

  if ! git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    _yellow "WARN: project is not a git repository; skipping source update."
    UPDATE_SOURCE_STATUS="skipped-not-repo"
    return 0
  fi

  local non_runtime_changes runtime_changes
  non_runtime_changes="$(list_non_runtime_local_changes || true)"
  runtime_changes="$(list_runtime_local_changes || true)"

  if [ -n "$non_runtime_changes" ]; then
    _yellow "WARN: local code/config changes detected; skipping git pull for safety."
    _yellow "Changed paths:"
    while IFS= read -r path; do
      [ -n "$path" ] && _yellow "  - $path"
    done <<< "$non_runtime_changes"
    _yellow "Hint: commit/stash your changes, or run ./install.sh update-force"
    UPDATE_SOURCE_STATUS="skipped-local-changes"
    return 0
  fi

  if [ -n "$runtime_changes" ]; then
    _blue "Runtime data changes detected; proceeding with code update."
  fi

  local preserve_snapshot=""
  preserve_snapshot="$(create_runtime_git_preserve_snapshot || true)"
  [ -n "$preserve_snapshot" ] && _blue "Preserved runtime data files before git pull."

  local branch before_head after_head
  branch="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  before_head="$(git -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null || echo "")"
  _blue "Updating source from git (branch: $branch)"
  git -C "$ROOT_DIR" fetch --all --prune
  if git -C "$ROOT_DIR" pull --ff-only; then
    after_head="$(git -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null || echo "")"
    if [ -n "$before_head" ] && [ "$before_head" = "$after_head" ]; then
      UPDATE_SOURCE_STATUS="already-latest"
      _blue "Source is already up-to-date."
    else
      UPDATE_SOURCE_STATUS="updated"
      _green "OK: source updated."
    fi
  else
    UPDATE_SOURCE_STATUS="pull-failed"
    _yellow "WARN: git pull failed; continuing with current source."
    _yellow "Hint: run ./install.sh update-force if you want to force-sync code."
  fi
  restore_runtime_git_preserve_snapshot "$preserve_snapshot"
  refresh_app_version
}

force_sync_source_if_git() {
  if ! command -v git >/dev/null 2>&1; then
    _yellow "WARN: git is not installed; skipping source force-sync."
    return 0
  fi

  if ! git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    _yellow "WARN: project is not a git repository; skipping source force-sync."
    return 0
  fi

  local branch target
  branch="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  target="origin/$branch"

  local preserve_snapshot=""
  preserve_snapshot="$(create_runtime_git_preserve_snapshot || true)"
  [ -n "$preserve_snapshot" ] && _blue "Preserved runtime data files before force-sync."

  _blue "Force syncing source from git (branch: $branch)"
  git -C "$ROOT_DIR" fetch --all --prune
  if ! git -C "$ROOT_DIR" show-ref --verify --quiet "refs/remotes/$target"; then
    _yellow "WARN: remote branch $target not found; fallback to origin/main."
    target="origin/main"
  fi
  git -C "$ROOT_DIR" reset --hard "$target"
  restore_runtime_git_preserve_snapshot "$preserve_snapshot"
  _green "OK: source force-synced to $target."
}

show_diagnostics() {
  ensure_dirs
  load_env_file "$ENV_FILE" || true

  show_status
  echo "-----------------------------------------"
  echo "Diagnostics"
  echo "-----------------------------------------"

  local admin_env_state="❌"
  local admin_token_state="❌"
  local user_token_state="❌"
  [ -n "${ADMIN_ID:-}" ] && admin_env_state="✅"
  [ -n "${ADMIN_BOT_TOKEN:-}" ] && admin_token_state="✅"
  [ -n "${USER_BOT_TOKEN:-}" ] && user_token_state="✅"

  echo "Env keys: ADMIN_ID=$admin_env_state ADMIN_BOT_TOKEN=$admin_token_state USER_BOT_TOKEN=$user_token_state"

  if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local branch commit
    branch="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    commit="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "Git: branch=$branch commit=$commit"

    local non_runtime_changes
    non_runtime_changes="$(list_non_runtime_local_changes || true)"
    if [ -n "$non_runtime_changes" ]; then
      echo "Local code changes:"
      while IFS= read -r path; do
        [ -n "$path" ] && echo "  - $path"
      done <<< "$non_runtime_changes"
    else
      echo "Local code changes: none"
    fi
  else
    echo "Git: unavailable"
  fi

  local admin_err admin_warn user_err user_warn agent_err agent_warn customer_err customer_warn
  admin_err="$(grep -Eci 'error|traceback|exception' "$ADMIN_LOG_FILE" 2>/dev/null || true)"
  admin_warn="$(grep -Eci 'warn|warning' "$ADMIN_LOG_FILE" 2>/dev/null || true)"
  user_err="$(grep -Eci 'error|traceback|exception' "$USER_LOG_FILE" 2>/dev/null || true)"
  user_warn="$(grep -Eci 'warn|warning' "$USER_LOG_FILE" 2>/dev/null || true)"
  agent_err="$(grep -Eci 'error|traceback|exception' "$AGENT_LOG_FILE" 2>/dev/null || true)"
  agent_warn="$(grep -Eci 'warn|warning' "$AGENT_LOG_FILE" 2>/dev/null || true)"
  customer_err="$(grep -Eci 'error|traceback|exception' "$CUSTOMER_LOG_FILE" 2>/dev/null || true)"
  customer_warn="$(grep -Eci 'warn|warning' "$CUSTOMER_LOG_FILE" 2>/dev/null || true)"
  admin_err="${admin_err:-0}"
  admin_warn="${admin_warn:-0}"
  user_err="${user_err:-0}"
  user_warn="${user_warn:-0}"
  agent_err="${agent_err:-0}"
  agent_warn="${agent_warn:-0}"
  customer_err="${customer_err:-0}"
  customer_warn="${customer_warn:-0}"

  echo "Logs: admin(error=$admin_err warn=$admin_warn) user(error=$user_err warn=$user_warn) agent(error=$agent_err warn=$agent_warn) customer(error=$customer_err warn=$customer_warn)"
  echo "Recent AdminBot issues:"
  (grep -Ein 'error|traceback|exception|warn|warning' "$ADMIN_LOG_FILE" | tail -n 12) || true
  echo "Recent UserBot issues:"
  (grep -Ein 'error|traceback|exception|warn|warning' "$USER_LOG_FILE" | tail -n 12) || true
  echo "Recent AgentBot issues:"
  (grep -Ein 'error|traceback|exception|warn|warning' "$AGENT_LOG_FILE" | tail -n 12) || true
  echo "Recent CustomerBot issues:"
  (grep -Ein 'error|traceback|exception|warn|warning' "$CUSTOMER_LOG_FILE" | tail -n 12) || true
}

show_live_logs() {
  ensure_dirs
  _blue "Streaming logs (Ctrl+C to stop)..."
  tail -n 60 -F "$ADMIN_LOG_FILE" "$USER_LOG_FILE" "$AGENT_LOG_FILE" "$CUSTOMER_LOG_FILE"
}

reinstall_all() {
  create_snapshot_backup "PreReinstall"
  stop_bots
  rm -rf "$VENV_DIR"
  setup_venv_and_requirements
  check_required_env 0
  init_database
  start_bots
  show_status
  _green "OK: reinstall completed."
}

update_force_all() {
  create_snapshot_backup "PreForceUpdate"
  force_sync_source_if_git
  setup_venv_and_requirements
  check_required_env 0
  init_database
  stop_bots
  start_bots
  show_status
  _green "OK: force update completed."
}

init_database() {
  [ -x "$VENV_DIR/bin/python" ] || return 0
  _blue "Initializing database schema"
  "$VENV_DIR/bin/python" - <<'PY'
from Shared import userbot_db
userbot_db.init_db()
print("db-init:ok")
PY
  _green "OK: database initialized."
}

systemd_available() {
  command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]
}

systemd_units_installed() {
  [ -f "$SYSTEMD_ADMIN_UNIT_FILE" ] && [ -f "$SYSTEMD_USER_UNIT_FILE" ] && [ -f "$SYSTEMD_AGENT_UNIT_FILE" ] && [ -f "$SYSTEMD_CUSTOMER_UNIT_FILE" ]
}

require_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    _red "ERROR: this command requires root privileges."
    _yellow "Run with sudo."
    return 1
  fi
}

write_systemd_units() {
  cat > "$SYSTEMD_ADMIN_UNIT_FILE" <<EOF
[Unit]
Description=Hiddify SellBot AdminBot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${VENV_DIR}/bin/python ${ADMIN_MAIN}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

  cat > "$SYSTEMD_USER_UNIT_FILE" <<EOF
[Unit]
Description=Hiddify SellBot UserBot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${VENV_DIR}/bin/python ${USER_MAIN}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

  cat > "$SYSTEMD_AGENT_UNIT_FILE" <<EOF
[Unit]
Description=Hiddify SellBot AgentBot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${VENV_DIR}/bin/python ${AGENT_MAIN}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

  cat > "$SYSTEMD_CUSTOMER_UNIT_FILE" <<EOF
[Unit]
Description=Hiddify SellBot CustomerBot (multi-bot runner)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${VENV_DIR}/bin/python ${CUSTOMER_MAIN}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
}

autostart_on() {
  require_root || return 1
  if ! systemd_available; then
    _red "ERROR: systemd is not available on this host."
    return 1
  fi
  ensure_dirs
  check_required_env 0
  [ -x "$VENV_DIR/bin/python" ] || {
    _red "ERROR: virtual environment is missing. Run ./install.sh install first."
    return 1
  }

  _blue "Configuring systemd autostart"
  stop_single_bot "$ADMIN_PID_FILE" "$ADMIN_MAIN" "AdminBot"
  stop_single_bot "$USER_PID_FILE" "$USER_MAIN" "UserBot"
  stop_single_bot "$AGENT_PID_FILE" "$AGENT_MAIN" "AgentBot"
  stop_single_bot "$CUSTOMER_PID_FILE" "$CUSTOMER_MAIN" "CustomerBot"
  write_systemd_units
  systemctl daemon-reload
  systemctl enable --now "$SYSTEMD_ADMIN_UNIT" "$SYSTEMD_USER_UNIT" "$SYSTEMD_AGENT_UNIT" "$SYSTEMD_CUSTOMER_UNIT"
  rm -f "$ADMIN_PID_FILE" "$USER_PID_FILE" "$AGENT_PID_FILE" "$CUSTOMER_PID_FILE"
  _green "OK: autostart enabled."
  autostart_status
}

autostart_off() {
  require_root || return 1
  if ! systemd_available; then
    _red "ERROR: systemd is not available on this host."
    return 1
  fi
  if ! systemd_units_installed; then
    _yellow "WARN: autostart services are not installed."
    return 0
  fi

  _blue "Stopping and disabling systemd autostart"
  systemctl disable --now "$SYSTEMD_ADMIN_UNIT" "$SYSTEMD_USER_UNIT" "$SYSTEMD_AGENT_UNIT" "$SYSTEMD_CUSTOMER_UNIT" || true
  rm -f "$ADMIN_PID_FILE" "$USER_PID_FILE" "$AGENT_PID_FILE" "$CUSTOMER_PID_FILE"
  _green "OK: autostart disabled."
}

autostart_rm() {
  require_root || return 1
  if ! systemd_available; then
    _red "ERROR: systemd is not available on this host."
    return 1
  fi

  _blue "Removing systemd autostart services"
  systemctl disable --now "$SYSTEMD_ADMIN_UNIT" "$SYSTEMD_USER_UNIT" "$SYSTEMD_AGENT_UNIT" "$SYSTEMD_CUSTOMER_UNIT" 2>/dev/null || true
  rm -f "$SYSTEMD_ADMIN_UNIT_FILE" "$SYSTEMD_USER_UNIT_FILE" "$SYSTEMD_AGENT_UNIT_FILE" "$SYSTEMD_CUSTOMER_UNIT_FILE"
  systemctl daemon-reload
  systemctl reset-failed 2>/dev/null || true
  rm -f "$ADMIN_PID_FILE" "$USER_PID_FILE" "$AGENT_PID_FILE" "$CUSTOMER_PID_FILE"
  _green "OK: autostart services removed."
}

autostart_status() {
  if ! systemd_available; then
    _yellow "systemd: unavailable"
    return 0
  fi
  if ! systemd_units_installed; then
    _yellow "autostart: not installed"
    return 0
  fi

  local admin_active admin_enabled user_active user_enabled agent_active agent_enabled customer_active customer_enabled admin_pid user_pid agent_pid customer_pid
  admin_active="$(systemctl is-active "$SYSTEMD_ADMIN_UNIT" 2>/dev/null || true)"
  admin_enabled="$(systemctl is-enabled "$SYSTEMD_ADMIN_UNIT" 2>/dev/null || true)"
  user_active="$(systemctl is-active "$SYSTEMD_USER_UNIT" 2>/dev/null || true)"
  user_enabled="$(systemctl is-enabled "$SYSTEMD_USER_UNIT" 2>/dev/null || true)"
  agent_active="$(systemctl is-active "$SYSTEMD_AGENT_UNIT" 2>/dev/null || true)"
  agent_enabled="$(systemctl is-enabled "$SYSTEMD_AGENT_UNIT" 2>/dev/null || true)"
  customer_active="$(systemctl is-active "$SYSTEMD_CUSTOMER_UNIT" 2>/dev/null || true)"
  customer_enabled="$(systemctl is-enabled "$SYSTEMD_CUSTOMER_UNIT" 2>/dev/null || true)"
  admin_pid="$(systemctl show -p MainPID --value "$SYSTEMD_ADMIN_UNIT" 2>/dev/null || true)"
  user_pid="$(systemctl show -p MainPID --value "$SYSTEMD_USER_UNIT" 2>/dev/null || true)"
  agent_pid="$(systemctl show -p MainPID --value "$SYSTEMD_AGENT_UNIT" 2>/dev/null || true)"
  customer_pid="$(systemctl show -p MainPID --value "$SYSTEMD_CUSTOMER_UNIT" 2>/dev/null || true)"

  admin_pid="${admin_pid:-0}"
  user_pid="${user_pid:-0}"
  agent_pid="${agent_pid:-0}"
  customer_pid="${customer_pid:-0}"
  echo "autostart(admin):    active=${admin_active:-unknown} enabled=${admin_enabled:-unknown} pid=$admin_pid"
  echo "autostart(user):     active=${user_active:-unknown} enabled=${user_enabled:-unknown} pid=$user_pid"
  echo "autostart(agent):    active=${agent_active:-unknown} enabled=${agent_enabled:-unknown} pid=$agent_pid"
  echo "autostart(customer): active=${customer_active:-unknown} enabled=${customer_enabled:-unknown} pid=$customer_pid"
}

autostart_menu() {
  if [ ! -t 0 ]; then
    _red "ERROR: autostart menu requires an interactive terminal."
    _yellow "Use direct commands:"
    _yellow "./install.sh autostart-on | autostart-off | autostart-status | autostart-rm"
    return 1
  fi

  while true; do
    echo "========================================="
    echo "AutoStart Manager (systemd)"
    echo "========================================="
    echo "1) Enable autostart (recommended)"
    echo "2) Disable autostart"
    echo "3) Show autostart status"
    echo "4) Remove autostart services"
    echo "0) Back"
    echo "-----------------------------------------"
    read -rp "Select option: " a_choice
    case "${a_choice:-}" in
      1) run_command autostart-on ;;
      2) run_command autostart-off ;;
      3) run_command autostart-status ;;
      4) run_command autostart-rm ;;
      0|q|Q|quit|exit)
        return 0
        ;;
      *)
        _yellow "WARN: invalid option."
        ;;
    esac
    echo "-----------------------------------------"
    read -rp "Press Enter to continue..." _
  done
}

normalize_stabilizer_mode() {
  local mode="${1:-}"
  mode="$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')"
  mode="$(printf '%s' "$mode" | sed -E 's/^[[:space:]"\047]+|[[:space:]"\047]+$//g')"

  case "$mode" in
    ""|toggle|1|true|yes|enable|enabled|on)
      printf 'toggle'
      ;;
    update|patch)
      printf 'update'
      ;;
    off|0|false|no|disable|disabled)
      printf 'off'
      ;;
    *)
      return 1
      ;;
  esac
}

get_stabilizer_mode() {
  local raw=""
  if [ -f "$ENV_FILE" ]; then
    raw="$(grep -E "^${HIDDIFY_STABILIZER_ENV_KEY}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  fi
  normalize_stabilizer_mode "${raw:-toggle}" || printf 'toggle'
}

stabilizer_mode_label() {
  case "${1:-}" in
    toggle)
      printf 'toggle / کامل و پیشنهادی'
      ;;
    update)
      printf 'update / فقط ذخیره مجدد'
      ;;
    off)
      printf 'off / خاموش'
      ;;
    *)
      printf 'unknown'
      ;;
  esac
}

show_stabilizer_status() {
  local mode
  mode="$(get_stabilizer_mode)"
  echo "========================================="
  echo "Hiddify User Stabilizer"
  echo "========================================="
  echo "Current: $(stabilizer_mode_label "$mode")"
  echo "Env:     ${HIDDIFY_STABILIZER_ENV_KEY}=${mode}"
  echo "File:    $ENV_FILE"
  echo "-----------------------------------------"
  echo "toggle: create -> save -> disable briefly -> enable"
  echo "update: create -> save only"
  echo "off:    no workaround"
}

set_stabilizer_mode() {
  local requested="${1:-}"
  local mode=""
  if ! mode="$(normalize_stabilizer_mode "$requested")"; then
    _red "ERROR: invalid stabilizer mode: $requested"
    _yellow "Allowed values: toggle, update, off"
    return 1
  fi

  ensure_dirs
  touch "$ENV_FILE"
  set_env_var "$HIDDIFY_STABILIZER_ENV_KEY" "$mode" "$ENV_FILE"
  _green "OK: Hiddify user stabilizer set to: $mode"
  _yellow "Restart is required to apply this setting."
}

stabilizer_menu() {
  if [ ! -t 0 ]; then
    _red "ERROR: stabilizer menu requires an interactive terminal."
    _yellow "Use direct commands:"
    _yellow "./install.sh stabilizer-toggle | stabilizer-update | stabilizer-off | stabilizer-status"
    return 1
  fi

  while true; do
    local mode
    mode="$(get_stabilizer_mode)"
    echo "========================================="
    echo "Hiddify User Stabilizer"
    echo "========================================="
    echo "Current: $(stabilizer_mode_label "$mode")"
    echo "-----------------------------------------"
    echo "1) Enable full toggle workaround (recommended)"
    echo "2) Enable save/update only"
    echo "3) Disable workaround"
    echo "4) Show status"
    echo "0) Back"
    echo "-----------------------------------------"
    read -rp "Select option: " s_choice
    case "${s_choice:-}" in
      1)
        set_stabilizer_mode toggle
        ;;
      2)
        set_stabilizer_mode update
        ;;
      3)
        set_stabilizer_mode off
        ;;
      4)
        show_stabilizer_status
        ;;
      0|q|Q|quit|exit)
        return 0
        ;;
      *)
        _yellow "WARN: invalid option."
        ;;
    esac

    if [[ "${s_choice:-}" =~ ^[123]$ ]]; then
      local restart_choice=""
      read -rp "Restart bots now? [Y/n]: " restart_choice
      if [[ ! "$restart_choice" =~ ^[Nn]$ ]]; then
        run_command restart || true
      fi
    fi

    echo "-----------------------------------------"
    read -rp "Press Enter to continue..." _
  done
}

wait_for_process_exit() {
  local pid="$1"
  local timeout="${2:-15}"
  local i=0
  [ -n "$pid" ] || return 0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge "$timeout" ]; then
      return 1
    fi
    sleep 1
  done
  return 0
}

stop_single_bot() {
  local pid_file="$1"
  local main_py="$2"
  local title="$3"

  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      if ! wait_for_process_exit "$pid" 5; then
        kill -9 "$pid" 2>/dev/null || true
        wait_for_process_exit "$pid" 5 || true
      fi
    fi
    rm -f "$pid_file"
  fi

  if command -v pgrep >/dev/null 2>&1; then
    local matched_pids
    matched_pids="$(pgrep -f "$main_py" || true)"
    if [ -n "$matched_pids" ]; then
      while IFS= read -r mpid; do
        [ -n "$mpid" ] || continue
        kill "$mpid" 2>/dev/null || true
      done <<< "$matched_pids"
      sleep 1
      matched_pids="$(pgrep -f "$main_py" || true)"
      if [ -n "$matched_pids" ]; then
        while IFS= read -r mpid; do
          [ -n "$mpid" ] || continue
          kill -9 "$mpid" 2>/dev/null || true
        done <<< "$matched_pids"
      fi
    fi
  else
    pkill -f "$main_py" 2>/dev/null || true
    sleep 1
    pkill -9 -f "$main_py" 2>/dev/null || true
  fi

  local wait_i=0
  while pgrep -f "$main_py" >/dev/null 2>&1; do
    wait_i=$((wait_i + 1))
    if [ "$wait_i" -ge 10 ]; then
      break
    fi
    sleep 1
  done

  _green "OK: $title stopped (if running)."
}

stop_bots() {
  ensure_dirs
  if systemd_units_installed; then
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
      _red "ERROR: systemd autostart is installed; use sudo to stop services."
      return 1
    fi
    _blue "Stopping bots via systemd"
    systemctl stop "$SYSTEMD_ADMIN_UNIT" "$SYSTEMD_USER_UNIT" "$SYSTEMD_AGENT_UNIT" "$SYSTEMD_CUSTOMER_UNIT"
    rm -f "$ADMIN_PID_FILE" "$USER_PID_FILE" "$AGENT_PID_FILE" "$CUSTOMER_PID_FILE"
    _green "OK: AdminBot stopped (systemd)."
    _green "OK: UserBot stopped (systemd)."
    _green "OK: AgentBot stopped (systemd)."
    _green "OK: CustomerBot stopped (systemd)."
    return 0
  fi
  _blue "Stopping bots"
  stop_single_bot "$ADMIN_PID_FILE" "$ADMIN_MAIN" "AdminBot"
  stop_single_bot "$USER_PID_FILE" "$USER_MAIN" "UserBot"
  stop_single_bot "$AGENT_PID_FILE" "$AGENT_MAIN" "AgentBot"
  stop_single_bot "$CUSTOMER_PID_FILE" "$CUSTOMER_MAIN" "CustomerBot"
}

start_single_bot() {
  local main_py="$1"
  local pid_file="$2"
  local log_file="$3"
  local title="$4"

  if [ ! -f "$main_py" ]; then
    _yellow "WARN: entrypoint not found: $main_py"
    return 0
  fi

  if [ -f "$pid_file" ]; then
    local current_pid
    current_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$current_pid" ] && kill -0 "$current_pid" 2>/dev/null; then
      _yellow "WARN: $title is already running (PID=$current_pid)."
      return 0
    fi
    rm -f "$pid_file"
  fi

  if command -v pgrep >/dev/null 2>&1; then
    local existing_pid
    existing_pid="$(pgrep -f "$main_py" | head -n 1 || true)"
    if [ -n "$existing_pid" ]; then
      echo "$existing_pid" > "$pid_file"
      _yellow "WARN: $title is already running (detected PID=$existing_pid). Skipping duplicate start."
      return 0
    fi
  fi

  nohup "$VENV_DIR/bin/python" "$main_py" >> "$log_file" 2>&1 &
  local pid=$!
  echo "$pid" > "$pid_file"
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    _green "OK: $title started (PID=$pid)"
  else
    _red "ERROR: failed to start $title. Check log: $log_file"
    return 1
  fi
}

start_bots() {
  ensure_dirs
  check_required_env 0
  [ -x "$VENV_DIR/bin/python" ] || {
    _red "ERROR: virtual environment is missing. Run ./install.sh install first."
    return 1
  }
  if systemd_units_installed; then
    if [ "${EUID:-$(id -u)}" -ne 0 ]; then
      _red "ERROR: systemd autostart is installed; use sudo to start services."
      return 1
    fi
    _blue "Starting bots via systemd"
    systemctl start "$SYSTEMD_ADMIN_UNIT" "$SYSTEMD_USER_UNIT"
    _green "OK: AdminBot started (systemd)"
    _green "OK: UserBot started (systemd)"
    if [ -n "${AGENT_BOT_TOKEN:-}" ]; then
      systemctl start "$SYSTEMD_AGENT_UNIT" "$SYSTEMD_CUSTOMER_UNIT"
      _green "OK: AgentBot started (systemd)"
      _green "OK: CustomerBot started (systemd)"
    else
      _yellow "SKIP: AgentBot (AGENT_BOT_TOKEN not set)"
      _yellow "SKIP: CustomerBot (AGENT_BOT_TOKEN not set)"
    fi
    return 0
  fi

  _blue "Starting bots"
  start_single_bot "$ADMIN_MAIN" "$ADMIN_PID_FILE" "$ADMIN_LOG_FILE" "AdminBot"
  start_single_bot "$USER_MAIN" "$USER_PID_FILE" "$USER_LOG_FILE" "UserBot"
  if [ -n "${AGENT_BOT_TOKEN:-}" ]; then
    start_single_bot "$AGENT_MAIN" "$AGENT_PID_FILE" "$AGENT_LOG_FILE" "AgentBot"
    start_single_bot "$CUSTOMER_MAIN" "$CUSTOMER_PID_FILE" "$CUSTOMER_LOG_FILE" "CustomerBot"
  else
    _yellow "SKIP: AgentBot (AGENT_BOT_TOKEN not set)"
    _yellow "SKIP: CustomerBot (AGENT_BOT_TOKEN not set)"
  fi
}

status_single_bot() {
  local pid_file="$1"
  local title="$2"
  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      _green "RUNNING: $title (PID=$pid)"
      return 0
    fi
  fi
  _yellow "STOPPED: $title"
}

show_status() {
  ensure_dirs
  refresh_app_version
  echo "========================================="
  echo "Hiddify-SellBot"
  echo "Version: $APP_VERSION"
  echo "Project: $ROOT_DIR"
  echo "Env:     $ENV_FILE"
  echo "Logs:    $LOG_DIR"
  echo "Backups: $BACKUP_DIR"
  echo "========================================="
  if systemd_units_installed; then
    autostart_status
    return 0
  fi
  status_single_bot "$ADMIN_PID_FILE" "AdminBot"
  status_single_bot "$USER_PID_FILE" "UserBot"
  status_single_bot "$AGENT_PID_FILE" "AgentBot"
  status_single_bot "$CUSTOMER_PID_FILE" "CustomerBot"
}

factory_reset() {
  if [ ! -t 0 ]; then
    _red "ERROR: factory-reset requires an interactive terminal."
    return 1
  fi
  _yellow "Factory reset will remove bot data (DB/services/settings/receipts)."
  local confirm=""
  read -rp "Type RESET to confirm factory-reset: " confirm
  if [ "$confirm" != "RESET" ]; then
    _yellow "Cancelled."
    return 0
  fi
  create_snapshot_backup "PreFactoryReset"

  stop_bots

  rm -f "$ROOT_DIR/Shared/hiddify_sellbot.db"
  printf '{\n  "servers": []\n}\n' > "$ROOT_DIR/Shared/servers.json"
  printf '{\n  "servers": {}\n}\n' > "$ROOT_DIR/Shared/plans.json"

  rm -rf "$RECEIPT_DIR"
  mkdir -p "$RECEIPT_DIR"

  init_database
  start_bots
  show_status
  _green "OK: factory reset completed."
}

setup_sub_ssl() {
  local raw_domain="${1:-}"
  local email="${2:-}"
  local domain=""

  if [ -z "$raw_domain" ]; then
    _red "ERROR: domain is required."
    _yellow "Usage: ./install.sh ssl <domain> [email]"
    _yellow "Example: sudo ./install.sh ssl sell.example.com admin@example.com"
    return 1
  fi

  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    _red "ERROR: ssl setup needs root privileges."
    _yellow "Run: sudo ./install.sh ssl <domain> [email]"
    return 1
  fi

  domain="$(printf '%s' "$raw_domain" | tr '[:upper:]' '[:lower:]')"
  domain="${domain#http://}"
  domain="${domain#https://}"
  domain="${domain%%/*}"
  domain="${domain%%\?*}"
  domain="${domain%%#*}"
  domain="${domain%%:*}"
  domain="$(printf '%s' "$domain" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"

  if [ -z "$domain" ] || [[ ! "$domain" =~ ^[a-z0-9.-]+$ ]] || [[ "$domain" != *.* ]]; then
    _red "ERROR: invalid domain: $raw_domain"
    _yellow "Use a valid FQDN like: sell.example.com"
    return 1
  fi
  if [[ "$domain" =~ ^[0-9.]+$ ]]; then
    _red "ERROR: IP address is not supported for Let's Encrypt SSL."
    _yellow "Use a real domain and point DNS A/AAAA to this server."
    return 1
  fi

  if command -v ss >/dev/null 2>&1; then
    local l80 l443
    l80="$(ss -ltnp 'sport = :80' 2>/dev/null | tail -n +2 || true)"
    l443="$(ss -ltnp 'sport = :443' 2>/dev/null | tail -n +2 || true)"
    if [ -n "$l80" ] && ! printf '%s\n' "$l80" | grep -qi "nginx"; then
      _red "ERROR: port 80 is already in use by another service."
      _yellow "Stop that service or terminate SSL on another reverse proxy."
      return 1
    fi
    if [ -n "$l443" ] && ! printf '%s\n' "$l443" | grep -qi "nginx"; then
      _red "ERROR: port 443 is already in use by another service."
      _yellow "Stop that service or terminate SSL on another reverse proxy."
      return 1
    fi
  fi

  load_env_file "$ENV_FILE" || true
  local sub_port="${SUB_SERVER_PORT:-8787}"
  if ! [[ "$sub_port" =~ ^[0-9]+$ ]] || [ "$sub_port" -lt 1 ] || [ "$sub_port" -gt 65535 ]; then
    sub_port=8787
  fi

  _blue "Installing nginx + certbot dependencies"
  apt-get update
  apt-get install -y nginx certbot python3-certbot-nginx

  local nginx_conf="/etc/nginx/sites-available/hiddify-sellbot-sub.conf"
  cat > "$nginx_conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:${sub_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

  ln -sf "$nginx_conf" /etc/nginx/sites-enabled/hiddify-sellbot-sub.conf

  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx

  local certbot_cmd=(
    certbot --nginx
    -d "$domain"
    --agree-tos
    --non-interactive
    --no-eff-email
    --redirect
  )
  if [ -n "$email" ]; then
    certbot_cmd+=(--email "$email")
  else
    certbot_cmd+=(--register-unsafely-without-email)
  fi

  _blue "Requesting Let's Encrypt certificate for $domain"
  "${certbot_cmd[@]}"

  touch "$ENV_FILE"
  set_env_var "SUB_SERVER_PUBLIC_HOST" "$domain" "$ENV_FILE"
  set_env_var "SUB_SERVER_PUBLIC_SCHEME" "https" "$ENV_FILE"
  set_env_var "SUB_SERVER_PUBLIC_PORT" "443" "$ENV_FILE"

  if [ -x "$VENV_DIR/bin/python" ]; then
    "$VENV_DIR/bin/python" - <<PY
from Shared import userbot_db
userbot_db.set_managed_sub_base_url("https://${domain}")
print("managed-sub-base-url:ok")
PY
  fi

  _green "OK: SSL configured successfully."
  _green "Domain: https://${domain}"
  _yellow "Next step: ./install.sh restart"
}

uninstall_all() {
  if [ ! -t 0 ]; then
    _red "ERROR: uninstall requires an interactive terminal."
    return 1
  fi

  _yellow "This will uninstall Hiddify-SellBot runtime from this folder:"
  _yellow "- stop bots"
  _yellow "- remove .env, venv, logs, backups, receipts, and runtime DB/data files"
  _yellow "- keep source code files"
  local confirm=""
  read -rp "Type DELETE to confirm uninstall: " confirm
  if [ "$confirm" != "DELETE" ]; then
    _yellow "Cancelled."
    return 0
  fi

  if systemd_units_installed; then
    require_root || return 1
    autostart_rm || true
  fi

  stop_bots
  rm -rf "$VENV_DIR" "$LOG_DIR" "$BACKUP_DIR" "$RECEIPT_DIR"
  rm -f "$ENV_FILE"
  rm -f "$ROOT_DIR/Shared/hiddify_sellbot.db"
  rm -f "$ROOT_DIR/Shared/servers.json"
  rm -f "$ROOT_DIR/Shared/plans.json"

  _green "OK: uninstall completed for $ROOT_DIR"
  _yellow "To remove source code too, run manually:"
  _yellow "cd \"$(dirname "$ROOT_DIR")\" && rm -rf \"$(basename "$ROOT_DIR")\""
}

install_all() {
  setup_venv_and_requirements
  local allow_prompt=0
  if [ ! -f "$ENV_FILE" ]; then
    allow_prompt=1
  fi
  check_required_env "$allow_prompt"
  init_database
  stop_bots
  start_bots
  if [ "$ENV_WAS_MISSING" -eq 1 ] || [ "$ENV_CONFIGURED_IN_RUN" -eq 1 ]; then
    send_first_install_welcome
  fi
  show_status
  _green "OK: install completed."
  echo "Admin log:     tail -f $ADMIN_LOG_FILE"
  echo "User log:      tail -f $USER_LOG_FILE"
  echo "Agent log:     tail -f $AGENT_LOG_FILE"
  echo "Customer log:  tail -f $CUSTOMER_LOG_FILE"
}

update_all() {
  create_snapshot_backup "PreUpdate"
  update_source_if_git
  case "$UPDATE_SOURCE_STATUS" in
    skipped-local-changes)
      _yellow "WARN: source code update skipped due to local code changes."
      _yellow "If this server should track GitHub exactly, run: ./install.sh update-force"
      ;;
    pull-failed)
      _yellow "WARN: source update failed (git pull). Runtime restart will continue."
      ;;
    already-latest)
      _blue "Source already latest. Reinstalling deps/restarting services..."
      ;;
    updated)
      _green "Source sync done. Continuing update pipeline..."
      ;;
  esac
  setup_venv_and_requirements
  check_required_env 0
  init_database
  stop_bots
  start_bots
  show_status
  _green "OK: update completed."
}

interactive_menu() {
  _run_menu_cmd() {
    local cmd="$1"
    if run_command "$cmd"; then
      _green "OK: $cmd completed."
    else
      _red "ERROR: $cmd failed."
    fi
    echo "-----------------------------------------"
    read -rp "Press Enter to return menu..." _
  }

  _run_ssl_wizard() {
    local domain=""
    local email=""
    _blue "SSL setup wizard"
    _yellow "Domain example: sell.example.com (or https://sell.example.com)"
    read -rp "Domain (empty = cancel): " domain
    domain="$(printf '%s' "$domain" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
    if [ -z "$domain" ]; then
      _yellow "Cancelled."
      echo "-----------------------------------------"
      read -rp "Press Enter to return menu..." _
      return 0
    fi
    read -rp "Email for Let's Encrypt (optional): " email
    email="$(printf '%s' "$email" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"

    if [ -n "$email" ]; then
      run_command ssl "$domain" "$email"
    else
      run_command ssl "$domain"
    fi

    echo "-----------------------------------------"
    read -rp "Press Enter to return menu..." _
  }

  _run_force_update_with_confirm() {
    local confirm=""
    _yellow "Force Update will hard-sync code from Git and restart bots."
    _yellow "Runtime data (.env / database / servers / plans) is preserved."
    read -rp "Type FORCE to continue (or anything else to cancel): " confirm
    if [ "$confirm" != "FORCE" ]; then
      _yellow "Cancelled."
      echo "-----------------------------------------"
      read -rp "Press Enter to return menu..." _
      return 0
    fi
    _run_menu_cmd update-force
  }

  while true; do
    refresh_app_version
    echo "========================================="
    echo "Hiddify-SellBot | Version: $APP_VERSION"
    echo "========================================="
    echo "1) install"
    echo "2) update (safe)"
    echo "3) update-force (hard sync)"
    echo "4) restart"
    echo "5) status"
    echo "6) autostart manager"
    echo "7) ssl setup wizard"
    echo "8) hiddify user stabilizer"
    echo "9) help"
    echo "0) exit"
    echo "-----------------------------------------"
    read -rp "Select option: " choice
    case "${choice:-}" in
      1) _run_menu_cmd install ;;
      2) _run_menu_cmd update ;;
      3) _run_force_update_with_confirm ;;
      4) _run_menu_cmd restart ;;
      5) _run_menu_cmd status ;;
      6) autostart_menu ;;
      7) _run_ssl_wizard ;;
      8) stabilizer_menu ;;
      9) _run_menu_cmd help ;;
      0|q|Q|quit|exit)
        _green "Exit."
        return 0
        ;;
      *)
        _yellow "WARN: invalid option."
        ;;
    esac
  done
}

run_command() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    install)
      install_all
      ;;
    update)
      update_all
      ;;
    update-force)
      update_force_all
      ;;
    reinstall)
      reinstall_all
      ;;
    menu|panel)
      interactive_menu
      ;;
    autostart)
      autostart_menu
      ;;
    stabilizer)
      stabilizer_menu
      ;;
    stabilizer-status)
      show_stabilizer_status
      ;;
    stabilizer-toggle)
      set_stabilizer_mode toggle
      ;;
    stabilizer-update)
      set_stabilizer_mode update
      ;;
    stabilizer-off)
      set_stabilizer_mode off
      ;;
    diag)
      show_diagnostics
      ;;
    logs)
      show_live_logs
      ;;
    start)
      start_bots
      ;;
    stop)
      stop_bots
      ;;
    restart)
      stop_bots
      start_bots
      show_status
      ;;
    status)
      show_status
      ;;
    config)
      configure_env
      ;;
    uninstall)
      uninstall_all
      ;;
    factory-reset)
      factory_reset
      ;;
    version)
      echo "$APP_VERSION"
      ;;
    help|-h|--help)
      usage
      ;;
    ssl)
      setup_sub_ssl "$@"
      ;;
    autostart-on)
      autostart_on
      ;;
    autostart-off)
      autostart_off
      ;;
    autostart-rm)
      autostart_rm
      ;;
    autostart-status)
      autostart_status
      ;;
    *)
      _red "ERROR: unknown command: $cmd"
      usage
      return 1
      ;;
  esac
}

main() {
  if [ -z "${1:-}" ]; then
    if [ -t 0 ]; then
      interactive_menu
      return $?
    fi
    set -- install
  fi
  run_command "$@"
}

main "$@"

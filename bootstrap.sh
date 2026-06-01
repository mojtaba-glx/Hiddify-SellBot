#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${HSB_REPO_URL:-https://github.com/mojtaba-glx/Hiddify-SellBot.git}"
REPO_BRANCH="${HSB_REPO_BRANCH:-main}"

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  DEFAULT_INSTALL_DIR="/root/Hiddify-SellBot"
else
  DEFAULT_INSTALL_DIR="$HOME/Hiddify-SellBot"
fi

INSTALL_DIR="${HSB_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"

_green() { printf "\033[32m%s\033[0m\n" "$*"; }
_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
_red() { printf "\033[31m%s\033[0m\n" "$*"; }
_blue() { printf "\033[34m%s\033[0m\n" "$*"; }

usage() {
  cat <<'USAGE'
Usage:
  bash <(curl -fsSL https://raw.githubusercontent.com/mojtaba-glx/Hiddify-SellBot/main/bootstrap.sh)
  bash <(curl -fsSL https://raw.githubusercontent.com/mojtaba-glx/Hiddify-SellBot/main/bootstrap.sh) install
  bash <(curl -fsSL https://raw.githubusercontent.com/mojtaba-glx/Hiddify-SellBot/main/bootstrap.sh) update
  bash <(curl -fsSL https://raw.githubusercontent.com/mojtaba-glx/Hiddify-SellBot/main/bootstrap.sh) panel

Environment overrides:
  HSB_INSTALL_DIR   Target install directory (default: /root/Hiddify-SellBot or ~/Hiddify-SellBot)
  HSB_REPO_URL      Git repository URL
  HSB_REPO_BRANCH   Git branch name (default: main)
USAGE
}

run_root() {
  if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    _red "ERROR: root privileges (or sudo) are required."
    exit 1
  fi
}

ensure_base_tools() {
  local need_install=0
  command -v git >/dev/null 2>&1 || need_install=1
  command -v curl >/dev/null 2>&1 || need_install=1

  if [ "$need_install" -eq 0 ]; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    _red "ERROR: git/curl missing and apt-get is unavailable."
    _yellow "Install manually: git curl ca-certificates"
    exit 1
  fi

  _blue "Installing required base tools (git/curl/ca-certificates)"
  run_root apt-get update
  run_root apt-get install -y git curl ca-certificates
}

clone_or_update_repo() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    _blue "Repository found at $INSTALL_DIR, syncing latest $REPO_BRANCH"
    git -C "$INSTALL_DIR" fetch --all --prune
    if git -C "$INSTALL_DIR" show-ref --verify --quiet "refs/remotes/origin/$REPO_BRANCH"; then
      git -C "$INSTALL_DIR" reset --hard "origin/$REPO_BRANCH"
    else
      _yellow "WARN: origin/$REPO_BRANCH not found, using current branch."
      git -C "$INSTALL_DIR" pull --ff-only || true
    fi
    return 0
  fi

  local has_content=""
  if [ -d "$INSTALL_DIR" ]; then
    has_content="$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)"
  fi

  if [ -d "$INSTALL_DIR" ] && [ -n "$has_content" ]; then
    if [ -f "$INSTALL_DIR/install.sh" ]; then
      _yellow "WARN: $INSTALL_DIR is not a git repo, using existing install.sh."
      return 0
    fi
    _red "ERROR: $INSTALL_DIR exists and is not empty."
    _yellow "Set another path with HSB_INSTALL_DIR, or clear this directory."
    exit 1
  fi

  _blue "Cloning repository into $INSTALL_DIR"
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
}

run_installer() {
  if [ ! -f "$INSTALL_DIR/install.sh" ]; then
    _red "ERROR: install.sh not found in $INSTALL_DIR"
    exit 1
  fi

  chmod +x "$INSTALL_DIR/install.sh"

  local args=("$@")
  if [ "${#args[@]}" -eq 0 ]; then
    args=("install")
  fi

  _green "Running installer: $INSTALL_DIR/install.sh ${args[*]}"
  cd "$INSTALL_DIR"
  bash ./install.sh "${args[@]}"
}

main() {
  if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
  fi

  ensure_base_tools
  clone_or_update_repo
  run_installer "$@"
}

main "$@"

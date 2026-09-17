#!/usr/bin/env bash
# Общие хелперы: цвета, ввод, проверки. Источник: source lib/common.sh
set -euo pipefail

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'

info()    { echo -e "${CYAN}→${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*" >&2; }
die()     { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

ask() { echo -e "${YELLOW}?${NC} $1"; }

confirm() {
  local prompt="${1:-Продолжить?}" default="${2:-n}"
  local yn
  ask "${prompt} [y/N]: "
  read -r yn
  case "$yn" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

require_cmd() {
  local cmd="$1" hint="${2:-}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    [[ -n "$hint" ]] && hint=" ($hint)"
    die "Не найден исполняемый файл: $cmd${hint}"
  fi
}

# Проверка обязательных инструментов
require_tools() {
  require_cmd python3
  require_cmd openssl
  require_cmd curl
}

#!/usr/bin/env bash
# Общие переменные playground. Сорсится всеми остальными скриптами.
# Не запускать напрямую.

# ── Серверы ────────────────────────────────────────────────────────────────
PRIMARY_HOST="127.0.0.1"
PRIMARY_PORT="15353"
PRIMARY_CONTAINER="knot-primary"

SECONDARY1_HOST="127.0.0.1"
SECONDARY1_PORT="15354"
SECONDARY1_CONTAINER="knot-secondary1"

SECONDARY2_HOST="127.0.0.1"
SECONDARY2_PORT="15355"
SECONDARY2_CONTAINER="knot-secondary2"

# Внутренние IP (внутри docker-сети dns-playground)
PRIMARY_IP="172.30.0.10"
SECONDARY1_IP="172.30.0.11"
SECONDARY2_IP="172.30.0.12"

# ── Зоны ──────────────────────────────────────────────────────────────────
ZONES=(example.test playground.local db.test)

# ── TSIG ──────────────────────────────────────────────────────────────────
TSIG_KEY_NAME="playground-tsig"
TSIG_ALGORITHM="hmac-sha256"
TSIG_SECRET="cGxheWdyb3VuZC10c2lnLWtleS1mb3ItdGVzdGluZy1vbmx5IQ=="
# Строка для dig -y:
TSIG_DIG="${TSIG_ALGORITHM}:${TSIG_KEY_NAME}:${TSIG_SECRET}"

# ── dnsadmin API ────────────────────────────────────────────────────────────
# Переопределите через env, если dnsadmin слушает на другом адресе
DNSADMIN_URL="${DNSADMIN_URL:-http://localhost:8080}"
DNSADMIN_USER="${DNSADMIN_USER:-admin}"
DNSADMIN_PASSWORD="${DNSADMIN_PASSWORD:-change-me}"

# ── Цвета ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}→${NC} $*"; }
ok()      { echo -e "  ${GREEN}✓${NC} $*"; }
fail()    { echo -e "  ${RED}✗${NC} $*"; }
warn()    { echo -e "  ${YELLOW}!${NC} $*"; }
section() { echo -e "\n${BOLD}$*${NC}"; echo "$(printf '─%.0s' $(seq 1 60))"; }

# ── Хелперы ───────────────────────────────────────────────────────────────

# dig_soa_serial <host> <port> <zone> → serial или пусто
dig_soa_serial() {
    dig @"$1" -p "$2" "$3" SOA +short +time=2 +tries=1 2>/dev/null \
        | awk '{print $3}' | head -1
}

# dig_q <host> <port> <name> <type> → ответ
dig_q() {
    dig @"$1" -p "$2" "$3" "$4" +short +time=2 +tries=1 2>/dev/null
}

# knotc_exec <container> <args...> → вывод knotc
knotc_exec() {
    local container="$1"; shift
    docker exec "$container" knotc "$@" 2>&1
}

# require_running — проверить что контейнеры запущены
require_running() {
    local missing=()
    for c in "$PRIMARY_CONTAINER" "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
        if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"; then
            missing+=("$c")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}✗ Не запущены контейнеры: ${missing[*]}${NC}"
        echo "  Запустите: docker compose up -d  (из каталога playground/)"
        exit 1
    fi
}

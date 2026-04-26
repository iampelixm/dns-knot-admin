#!/usr/bin/env bash
# Проверка работы playground: сверка SOA serial, резолвинг записей.
set -euo pipefail

PRIMARY="127.0.0.1#15353"
SECONDARY1="127.0.0.1#15354"
SECONDARY2="127.0.0.1#15355"

ZONES=(example.test playground.local db.test)

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }

dig_soa_serial() {
    local server="$1" zone="$2"
    dig @"${server%#*}" -p "${server#*#}" "$zone" SOA +short +time=2 +tries=1 2>/dev/null \
        | awk '{print $3}' | head -1
}

dig_q() {
    local server="$1" zone="$2" type="${3:-A}"
    dig @"${server%#*}" -p "${server#*#}" "$zone" "$type" +short +time=2 +tries=1 2>/dev/null \
        | head -3
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        Knot DNS Playground — проверка синхронизации  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Доступность серверов ─────────────────────────────────────────────────
info "Доступность серверов (SOA example.test):"
for label_server in "primary:${PRIMARY}" "secondary1:${SECONDARY1}" "secondary2:${SECONDARY2}"; do
    label="${label_server%%:*}"; server="${label_server#*:}"
    serial=$(dig_soa_serial "$server" "example.test")
    if [[ -n "$serial" ]]; then
        ok "$label (${server}) — SOA serial: $serial"
    else
        fail "$label (${server}) — нет ответа"
    fi
done
echo ""

# ── 2. Синхронизация serial ─────────────────────────────────────────────────
info "Сверка SOA serial по зонам:"
all_synced=true
for zone in "${ZONES[@]}"; do
    echo "  Зона: $zone"
    serials=()
    for label_server in "primary:${PRIMARY}" "secondary1:${SECONDARY1}" "secondary2:${SECONDARY2}"; do
        label="${label_server%%:*}"; server="${label_server#*:}"
        serial=$(dig_soa_serial "$server" "$zone")
        printf "    %-12s %s\n" "$label:" "${serial:-нет ответа}"
        [[ -n "$serial" ]] && serials+=("$serial")
    done
    unique=$(printf '%s\n' "${serials[@]}" | sort -u | wc -l)
    if [[ "${#serials[@]}" -lt 3 ]]; then
        warn "  не все серверы отвечают — возможно, sync ещё не завершён"
        all_synced=false
    elif [[ "$unique" -eq 1 ]]; then
        ok "  зона синхронизирована на всех трёх серверах"
    else
        fail "  РАССИНХРОН — разные serial на серверах"
        all_synced=false
    fi
    echo ""
done

# ── 3. Примеры резолвинга ────────────────────────────────────────────────────
info "Примеры резолвинга с primary:"
for q_type in "example.test A" "www.example.test A" "example.test MX" "example.test TXT" \
              "playground.local TXT" "sync.playground.local TXT" \
              "_postgresql._tcp.db.test SRV"; do
    name="${q_type% *}"; rtype="${q_type##* }"
    result=$(dig_q "$PRIMARY" "$name" "$rtype")
    if [[ -n "$result" ]]; then
        ok "$rtype $name → $result"
    else
        fail "$rtype $name — нет ответа"
    fi
done
echo ""

# ── 4. Итог ──────────────────────────────────────────────────────────────────
if $all_synced; then
    echo -e "${GREEN}Всё в порядке — все зоны синхронизированы.${NC}"
else
    echo -e "${YELLOW}Есть проблемы. Если только запустил — подожди 5-10 секунд и запусти снова.${NC}"
    echo ""
    echo "  Принудительный NOTIFY с primary:"
    echo "    docker exec knot-primary knotc zone-notify example.test"
    echo "    docker exec knot-primary knotc zone-notify playground.local"
    echo "    docker exec knot-primary knotc zone-notify db.test"
fi
echo ""

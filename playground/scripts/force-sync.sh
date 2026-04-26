#!/usr/bin/env bash
# Принудительная синхронизация всех зон на всех secondary.
# Запускать после изменения зон или при подозрении на рассинхрон.
set -euo pipefail
cd "$(dirname "$0")"
source vars.sh

require_running

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║          Принудительная синхронизация зон            ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── Текущее состояние ────────────────────────────────────────────────────────
section "Текущие serial перед синхронизацией"
declare -A BEFORE
for zone in "${ZONES[@]}"; do
    p=$(dig_soa_serial "$PRIMARY_HOST" "$PRIMARY_PORT" "$zone")
    s1=$(dig_soa_serial "$SECONDARY1_HOST" "$SECONDARY1_PORT" "$zone")
    s2=$(dig_soa_serial "$SECONDARY2_HOST" "$SECONDARY2_PORT" "$zone")
    BEFORE["${zone}_p"]="$p"
    BEFORE["${zone}_s1"]="$s1"
    BEFORE["${zone}_s2"]="$s2"
    printf "  %-22s primary: %-12s s1: %-12s s2: %s\n" \
        "$zone" "${p:-(нет)}" "${s1:-(нет)}" "${s2:-(нет)}"
done

# ── NOTIFY с primary ─────────────────────────────────────────────────────────
section "1. Отправляем NOTIFY с primary"
for zone in "${ZONES[@]}"; do
    info "NOTIFY $zone..."
    docker exec "$PRIMARY_CONTAINER" knotc zone-notify "$zone" 2>&1 \
        | sed 's/^/    /' || true
done
ok "NOTIFY отправлены"

# ── Принудительный zone-refresh на secondary ─────────────────────────────────
section "2. zone-refresh на secondary (немедленный AXFR)"
for container in "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
    info "$container:"
    for zone in "${ZONES[@]}"; do
        docker exec "$container" knotc zone-refresh "$zone" 2>&1 \
            | sed 's/^/    /' || true
    done
done
ok "Refresh запущен на обоих secondary"

# ── Ждём завершения AXFR ─────────────────────────────────────────────────────
section "3. Ждём завершения AXFR..."
for i in $(seq 1 10); do
    sleep 1
    printf "\r  Прошло %d сек..." "$i"
done
echo ""

# ── Итоговая сверка ──────────────────────────────────────────────────────────
section "Результат синхронизации"
all_ok=true
for zone in "${ZONES[@]}"; do
    p=$(dig_soa_serial "$PRIMARY_HOST" "$PRIMARY_PORT" "$zone")
    s1=$(dig_soa_serial "$SECONDARY1_HOST" "$SECONDARY1_PORT" "$zone")
    s2=$(dig_soa_serial "$SECONDARY2_HOST" "$SECONDARY2_PORT" "$zone")
    printf "  %-22s primary: %-12s s1: %-12s s2: %s" \
        "$zone" "${p:-(нет)}" "${s1:-(нет)}" "${s2:-(нет)}"
    if [[ -n "$p" && "$p" == "$s1" && "$p" == "$s2" ]]; then
        echo -e "  ${GREEN}✓${NC}"
    else
        echo -e "  ${RED}✗${NC}"
        all_ok=false
    fi
done

echo ""
if $all_ok; then
    echo -e "${GREEN}Все зоны синхронизированы.${NC}"
else
    echo -e "${YELLOW}Некоторые зоны ещё не синхронизированы.${NC}"
    echo ""
    echo "  Возможные причины:"
    echo "  • secondary ещё выполняет AXFR (большая зона) — подождите и запустите снова"
    echo "  • нет сетевой связности между контейнерами"
    echo "  • ошибка в TSIG-конфигурации (проверьте: bash scripts/knotc.sh conf-check)"
    echo ""
    echo "  Логи secondary1:"
    docker logs --tail=20 "$SECONDARY1_CONTAINER" 2>&1 | grep -i "transfer\|axfr\|error\|notify" \
        | tail -5 | sed 's/^/    /' || true
fi
echo ""

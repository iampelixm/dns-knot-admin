#!/usr/bin/env bash
# Команды knotc для управления Knot DNS через docker exec.
# Запускать: bash scripts/knotc.sh [команда]
# Без аргументов — выполняет стандартный набор диагностики.
set -euo pipefail
cd "$(dirname "$0")"
source vars.sh

require_running

usage() {
    echo ""
    echo "Использование: $0 [команда]"
    echo ""
    echo "Команды:"
    echo "  status          Статус всех зон на всех серверах"
    echo "  notify          NOTIFY всех зон с primary → secondary"
    echo "  notify ZONE     NOTIFY конкретной зоны"
    echo "  refresh         Принудительный AXFR на всех secondary"
    echo "  refresh ZONE    Принудительный AXFR конкретной зоны"
    echo "  stats           Статистика зон"
    echo "  conf-check      Проверка конфига на всех серверах"
    echo "  keys            Показать TSIG-ключи"
    echo "  flush           Сбросить кэш зон (zone-purge)"
    echo ""
}

CMD="${1:-status}"
ZONE_ARG="${2:-}"

case "$CMD" in

# ── Статус зон ──────────────────────────────────────────────────────────────
status)
    echo ""
    echo "╔══════════════════════════════════════════════════════╗"
    echo "║              knotc zone-status — все серверы         ║"
    echo "╚══════════════════════════════════════════════════════╝"
    for container in "$PRIMARY_CONTAINER" "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
        section "Сервер: $container"
        docker exec "$container" knotc zone-status 2>&1 | sed 's/^/  /' || true
    done
    ;;

# ── NOTIFY ──────────────────────────────────────────────────────────────────
notify)
    echo ""
    section "knotc zone-notify — отправка NOTIFY с primary"
    if [[ -n "$ZONE_ARG" ]]; then
        info "NOTIFY зоны $ZONE_ARG → secondary серверы..."
        docker exec "$PRIMARY_CONTAINER" knotc zone-notify "$ZONE_ARG" 2>&1 | sed 's/^/  /'
        ok "NOTIFY отправлен для $ZONE_ARG"
    else
        for zone in "${ZONES[@]}"; do
            info "NOTIFY $zone..."
            docker exec "$PRIMARY_CONTAINER" knotc zone-notify "$zone" 2>&1 | sed 's/^/  /'
            ok "NOTIFY отправлен для $zone"
        done
    fi
    echo ""
    info "Ждём 3 секунды на AXFR..."
    sleep 3
    info "Проверяем serial после NOTIFY:"
    for zone in "${ZONES[@]}"; do
        p=$(dig_soa_serial "$PRIMARY_HOST" "$PRIMARY_PORT" "$zone")
        s1=$(dig_soa_serial "$SECONDARY1_HOST" "$SECONDARY1_PORT" "$zone")
        s2=$(dig_soa_serial "$SECONDARY2_HOST" "$SECONDARY2_PORT" "$zone")
        if [[ "$p" == "$s1" && "$p" == "$s2" ]]; then
            ok "$zone — serial $p — все синхронизированы"
        else
            warn "$zone — primary: $p, s1: ${s1:-(нет)}, s2: ${s2:-(нет)}"
        fi
    done
    ;;

# ── Принудительный AXFR ─────────────────────────────────────────────────────
refresh)
    echo ""
    section "knotc zone-refresh — принудительный AXFR на secondary"
    TARGETS=("$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER")
    for container in "${TARGETS[@]}"; do
        info "Refresh на $container..."
        if [[ -n "$ZONE_ARG" ]]; then
            docker exec "$container" knotc zone-refresh "$ZONE_ARG" 2>&1 | sed 's/^/  /'
        else
            for zone in "${ZONES[@]}"; do
                docker exec "$container" knotc zone-refresh "$zone" 2>&1 | sed 's/^/  /'
            done
        fi
        ok "$container — refresh запущен"
    done
    echo ""
    info "Ждём 3 секунды на AXFR..."
    sleep 3
    info "Проверяем serial:"
    for zone in "${ZONES[@]}"; do
        p=$(dig_soa_serial "$PRIMARY_HOST" "$PRIMARY_PORT" "$zone")
        s1=$(dig_soa_serial "$SECONDARY1_HOST" "$SECONDARY1_PORT" "$zone")
        s2=$(dig_soa_serial "$SECONDARY2_HOST" "$SECONDARY2_PORT" "$zone")
        printf "  %-20s primary: %-12s s1: %-12s s2: %s\n" \
            "$zone" "${p:-(нет)}" "${s1:-(нет)}" "${s2:-(нет)}"
    done
    ;;

# ── Статистика ──────────────────────────────────────────────────────────────
stats)
    echo ""
    section "knotc zone-stats"
    for container in "$PRIMARY_CONTAINER" "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
        echo ""
        info "$container:"
        for zone in "${ZONES[@]}"; do
            echo "  Зона $zone:"
            docker exec "$container" knotc zone-stats "$zone" 2>&1 \
                | grep -E "query|transfer|notify|update" \
                | sed 's/^/    /' || true
        done
    done
    ;;

# ── Проверка конфига ─────────────────────────────────────────────────────────
conf-check)
    echo ""
    section "knotc conf-check"
    for container in "$PRIMARY_CONTAINER" "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
        info "$container:"
        result=$(docker exec "$container" knotc conf-check 2>&1)
        if echo "$result" | grep -qi "error\|fail"; then
            fail "$container — ошибки в конфиге"
            echo "$result" | sed 's/^/    /'
        else
            ok "$container — конфиг в порядке"
            echo "$result" | sed 's/^/    /'
        fi
    done
    ;;

# ── TSIG-ключи ───────────────────────────────────────────────────────────────
keys)
    echo ""
    section "TSIG-ключи на серверах"
    for container in "$PRIMARY_CONTAINER" "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
        info "$container:"
        docker exec "$container" knotc conf-read key 2>&1 | sed 's/^/  /' || true
    done
    ;;

# ── Сброс кэша зон ───────────────────────────────────────────────────────────
flush)
    echo ""
    ZONE_ARG="${ZONE_ARG:-example.test}"
    section "knotc zone-purge (сброс кэша $ZONE_ARG)"
    warn "zone-purge удаляет данные зоны из памяти — secondary потребует повторный AXFR"
    echo ""
    for container in "$SECONDARY1_CONTAINER" "$SECONDARY2_CONTAINER"; do
        info "Purge $ZONE_ARG на $container..."
        docker exec "$container" knotc zone-purge "$ZONE_ARG" 2>&1 | sed 's/^/  /' || true
        ok "$container — purge выполнен"
    done
    echo ""
    info "Запускаем refresh для восстановления..."
    bash "$0" refresh "$ZONE_ARG"
    ;;

--help|-h|help)
    usage
    ;;

*)
    echo "Неизвестная команда: $CMD"
    usage
    exit 1
    ;;
esac

echo ""

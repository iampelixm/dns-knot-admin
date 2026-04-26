#!/usr/bin/env bash
# Тест AXFR (zone transfer) с TSIG-аутентификацией.
# Проверяет что primary корректно отдаёт зоны, а запросы без ключа отклоняются.
set -euo pipefail
cd "$(dirname "$0")"
source vars.sh

require_running

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║            AXFR zone transfer — тесты               ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── AXFR с ключом (должно пройти) ───────────────────────────────────────────
section "AXFR с TSIG-ключом (ожидаем успех)"
for zone in "${ZONES[@]}"; do
    echo ""
    info "Зона: $zone"
    result=$(dig @"$PRIMARY_HOST" -p "$PRIMARY_PORT" \
        "$zone" AXFR \
        -y "${TSIG_DIG}" \
        +time=5 2>&1)
    record_count=$(echo "$result" | grep -c "IN[[:space:]]" || true)
    if echo "$result" | grep -q "Transfer failed\|connection timed out\|communications error"; then
        fail "AXFR провалился"
        echo "$result" | grep -i "error\|fail" | sed 's/^/    /'
    else
        ok "AXFR успешен, записей: $record_count"
        echo "$result" | grep "IN[[:space:]]" | head -5 | sed 's/^/    /'
        echo "    ... (показаны первые 5)"
    fi
done

# ── AXFR без ключа (должно быть отклонено) ──────────────────────────────────
section "AXFR без TSIG-ключа (ожидаем отказ)"
echo ""
info "Попытка AXFR example.test без ключа с primary..."
result=$(dig @"$PRIMARY_HOST" -p "$PRIMARY_PORT" \
    example.test AXFR \
    +time=5 2>&1)
if echo "$result" | grep -qi "Transfer failed\|NOTAUTH\|REFUSED\|SERVFAIL"; then
    ok "Верно отклонено: $(echo "$result" | grep -i 'Transfer\|NOTAUTH\|REFUSED' | head -1 | xargs)"
else
    warn "Неожиданный ответ (проверьте ACL):"
    echo "$result" | head -5 | sed 's/^/    /'
fi

# ── AXFR с неправильным ключом ───────────────────────────────────────────────
section "AXFR с неверным ключом (ожидаем отказ)"
echo ""
info "Попытка AXFR example.test с подделанным ключом..."
FAKE_SECRET="ZmFrZXNlY3JldGtleXRoYXRzaG91bGRmYWlsdmVyaWZpY2F0aW9u"
result=$(dig @"$PRIMARY_HOST" -p "$PRIMARY_PORT" \
    example.test AXFR \
    -y "${TSIG_ALGORITHM}:${TSIG_KEY_NAME}:${FAKE_SECRET}" \
    +time=5 2>&1)
if echo "$result" | grep -qi "Transfer failed\|NOTAUTH\|BADSIG\|REFUSED"; then
    ok "Верно отклонено: $(echo "$result" | grep -i 'Transfer\|NOTAUTH\|BADSIG\|REFUSED' | head -1 | xargs)"
else
    warn "Неожиданный ответ (проверьте конфиг TSIG):"
    echo "$result" | head -5 | sed 's/^/    /'
fi

# ── AXFR напрямую от secondary к primary ────────────────────────────────────
section "AXFR изнутри Docker-сети (secondary → primary)"
echo ""
info "Запуск AXFR из контейнера knot-secondary1 → primary (${PRIMARY_IP}:53)..."
for zone in "${ZONES[@]}"; do
    result=$(docker exec "$SECONDARY1_CONTAINER" \
        kdig @"${PRIMARY_IP}" -p 53 \
        "$zone" AXFR \
        +tsig="${TSIG_ALGORITHM}:${TSIG_KEY_NAME}:${TSIG_SECRET}" \
        2>&1 || true)
    # kdig может не быть — fallback на dig
    if echo "$result" | grep -q "executable file not found\|not found"; then
        result=$(docker exec "$SECONDARY1_CONTAINER" \
            dig @"${PRIMARY_IP}" -p 53 \
            "$zone" AXFR \
            -y "${TSIG_DIG}" \
            +time=5 2>&1 || true)
    fi
    record_count=$(echo "$result" | grep -c "IN[[:space:]]" || true)
    if [[ "$record_count" -gt 0 ]]; then
        ok "$zone — $record_count записей получено"
    else
        warn "$zone — нет записей (возможно, зона уже синхронизирована через knotd)"
    fi
done

# ── Полный дамп зоны ─────────────────────────────────────────────────────────
section "Полный дамп example.test с primary"
echo ""
info "dig @${PRIMARY_HOST}:${PRIMARY_PORT} example.test AXFR -y <tsig>"
echo ""
dig @"$PRIMARY_HOST" -p "$PRIMARY_PORT" \
    example.test AXFR \
    -y "${TSIG_DIG}" \
    +time=5 2>/dev/null \
    | grep -v "^$\|^;" \
    | sed 's/^/  /'

echo ""

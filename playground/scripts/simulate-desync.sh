#!/usr/bin/env bash
# Симуляция рассинхрона зон между серверами.
# Сценарий: обрываем связь secondary → primary, меняем зону, восстанавливаем.
# Наглядно демонстрирует как 4-часовой refresh-min-interval спасает ситуацию.
set -euo pipefail
cd "$(dirname "$0")"
source vars.sh

require_running

ZONE="db.test"
ZONE_FILE="../zones/${ZONE}.zone"

print_serials() {
    local label="$1"
    echo ""
    info "$label"
    for s_info in "primary:${PRIMARY_HOST}:${PRIMARY_PORT}" \
                  "secondary1:${SECONDARY1_HOST}:${SECONDARY1_PORT}" \
                  "secondary2:${SECONDARY2_HOST}:${SECONDARY2_PORT}"; do
        IFS=: read -r name host port <<< "$s_info"
        serial=$(dig_soa_serial "$host" "$port" "$ZONE")
        printf "    %-12s serial: %s\n" "$name" "${serial:-(нет ответа)}"
    done
}

bump_serial() {
    local file="$1"
    local current
    current=$(grep -oP '\d{10}' "$file" | head -1)
    local new=$(( current + 1 ))
    sed -i "s/$current/$new/" "$file"
    echo "$new"
}

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         Симуляция рассинхрона зон                    ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
warn "Этот скрипт временно изменяет zone-файл для симуляции."
warn "Используемая зона: $ZONE"
echo ""

# ── Шаг 0: Исходное состояние ───────────────────────────────────────────────
print_serials "Шаг 0: Исходное состояние"

# ── Шаг 1: Разрываем сеть secondary ─────────────────────────────────────────
section "Шаг 1: Изолируем secondary1 от primary"
info "Отключаем secondary1 от сети dns-playground..."
docker network disconnect knot-playground_dns knot-secondary1 2>/dev/null \
    && ok "secondary1 отключён от сети" \
    || warn "Не удалось отключить (возможно другое имя сети)"

NETWORK=$(docker inspect knot-secondary1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null | head -1)
if [[ -n "$NETWORK" ]]; then
    docker network disconnect "$NETWORK" knot-secondary1 2>/dev/null && ok "secondary1 отключён" || true
fi

# ── Шаг 2: Меняем зону на primary ───────────────────────────────────────────
section "Шаг 2: Изменяем зону $ZONE на primary"
ORIG_SERIAL=$(grep -oP '\d{10}' "$ZONE_FILE" | head -1)
NEW_SERIAL=$(bump_serial "$ZONE_FILE")
info "Serial: $ORIG_SERIAL → $NEW_SERIAL"

# Добавляем запись-маркер рассинхрона
echo "desync-marker IN TXT  \"serial=${NEW_SERIAL} added-while-secondary1-was-offline\"" >> "$ZONE_FILE"
ok "Зона изменена"

# Перезапускаем primary чтобы применить новый zone-файл
info "Перезапускаем primary для применения изменений..."
docker compose -f ../docker-compose.yml restart knot-primary 2>/dev/null \
    || docker restart knot-primary
sleep 3

# Уведомляем secondary2 (которая ещё в сети)
docker exec knot-primary knotc zone-notify "$ZONE" 2>/dev/null || true
sleep 2

print_serials "Шаг 2: После изменения (secondary1 офлайн)"
echo ""
info "secondary1 отстаёт — связи нет, NOTIFY не дошёл"

# ── Шаг 3: Восстанавливаем сеть ─────────────────────────────────────────────
section "Шаг 3: Восстанавливаем сеть secondary1"

COMPOSE_NETWORK="knot-playground_dns"
docker network connect "$COMPOSE_NETWORK" knot-secondary1 2>/dev/null \
    && ok "secondary1 подключён обратно" \
    || warn "Укажите сеть вручную: docker network connect <network> knot-secondary1"

# Принудительный refresh (эмуляция 4-часового срабатывания)
info "Эмулируем 4-часовой forced refresh (zone-refresh)..."
docker exec knot-secondary1 knotc zone-refresh "$ZONE" 2>/dev/null || true
sleep 3

print_serials "Шаг 3: После восстановления сети"

# ── Проверяем запись-маркер ──────────────────────────────────────────────────
section "Проверяем TXT-маркер рассинхрона"
echo ""
info "Запрос desync-marker.${ZONE} TXT со всех серверов:"
for s_info in "primary:${PRIMARY_HOST}:${PRIMARY_PORT}" \
              "secondary1:${SECONDARY1_HOST}:${SECONDARY1_PORT}" \
              "secondary2:${SECONDARY2_HOST}:${SECONDARY2_PORT}"; do
    IFS=: read -r name host port <<< "$s_info"
    result=$(dig_q "$host" "$port" "desync-marker.${ZONE}" "TXT")
    printf "    %-12s %s\n" "$name:" "${result:-(нет — зона ещё не синхронизирована)}"
done

# ── Откат изменений ──────────────────────────────────────────────────────────
section "Откат: убираем маркер из zone-файла"
# Восстанавливаем оригинальный serial
sed -i "s/$NEW_SERIAL/$ORIG_SERIAL/" "$ZONE_FILE"
# Убираем строку с маркером
sed -i '/desync-marker/d' "$ZONE_FILE"

info "Перезапускаем primary с исходной зоной..."
docker compose -f ../docker-compose.yml restart knot-primary 2>/dev/null \
    || docker restart knot-primary
sleep 3

docker exec knot-primary knotc zone-notify "$ZONE" 2>/dev/null || true
sleep 2

docker exec knot-secondary1 knotc zone-refresh "$ZONE" 2>/dev/null || true
docker exec knot-secondary2 knotc zone-refresh "$ZONE" 2>/dev/null || true
sleep 2

print_serials "Финал: все серверы восстановлены"

echo ""
ok "Симуляция завершена. Zone-файл возвращён в исходное состояние."
echo ""

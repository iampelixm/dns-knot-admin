#!/usr/bin/env bash
# Примеры запросов к dnsadmin REST API.
# Требует запущенного dnsadmin (в k8s или локально).
#
# Использование:
#   DNSADMIN_URL=http://localhost:8080 bash scripts/api.sh [команда]
#   DNSADMIN_URL=https://dnsadmin.example.com bash scripts/api.sh [команда]
#
# Команды:
#   login           Получить JWT-токен
#   health          Статус DNS-сервера
#   zones           Список зон
#   zone ZONE       Получить zone-файл
#   sync-status     Сверка serial по всем серверам
#   instances       Список настроенных инстансов
#   validate ZONE   Валидировать zone-файл
#   knot-conf       Получить knot.conf
#   axfr-status     Статус AXFR-секрета
#   all             Выполнить все команды по очереди
set -euo pipefail
cd "$(dirname "$0")"
source vars.sh

CMD="${1:-all}"
ZONE_ARG="${2:-example.test}"

# ── Утилиты ──────────────────────────────────────────────────────────────────

TOKEN_FILE="/tmp/.dnsadmin_playground_token"

api_get()  { curl -sf -H "Authorization: Bearer $(cat $TOKEN_FILE)" "${DNSADMIN_URL}$1"; }
api_post() { curl -sf -H "Authorization: Bearer $(cat $TOKEN_FILE)" \
                  -H "Content-Type: application/json" \
                  -d "$2" "${DNSADMIN_URL}$1"; }

pretty() { python3 -m json.tool 2>/dev/null || cat; }

check_dnsadmin() {
    if ! curl -sf "${DNSADMIN_URL}/health" > /dev/null 2>&1; then
        echo -e "${RED}✗ dnsadmin недоступен по адресу: ${DNSADMIN_URL}${NC}"
        echo ""
        echo "  Укажите адрес через переменную окружения:"
        echo "    DNSADMIN_URL=http://localhost:8080 bash scripts/api.sh"
        echo ""
        echo "  Для доступа к продакшену через kubectl port-forward:"
        echo "    kubectl --context=summersite port-forward -n dns-knot svc/dnsadmin 8080:80"
        echo "    DNSADMIN_URL=http://localhost:8080 bash scripts/api.sh"
        exit 1
    fi
}

do_login() {
    section "POST /api/auth/login"
    info "URL: ${DNSADMIN_URL}/api/auth/login"
    info "Credentials: ${DNSADMIN_USER} / ***"
    echo ""
    response=$(curl -sf \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"${DNSADMIN_USER}\",\"password\":\"${DNSADMIN_PASSWORD}\"}" \
        "${DNSADMIN_URL}/api/auth/login")
    token=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
    echo "$token" > "$TOKEN_FILE"
    ok "Токен получен: ${token:0:30}…"
    echo ""
    echo "  Срок действия: ${JWT_EXPIRE_HOURS:-24} часов"
    echo "  Сохранён в:    $TOKEN_FILE"
}

do_health() {
    section "GET /api/dns-health"
    info "Проверка ответа DNS-сервера (SOA-запрос):"
    echo ""
    api_get "/api/dns-health" | pretty
}

do_zones() {
    section "GET /api/zones"
    info "Список зон в ConfigMap:"
    echo ""
    api_get "/api/zones" | pretty
}

do_zone() {
    local zone="$1"
    section "GET /api/zones/${zone}"
    info "Содержимое zone-файла:"
    echo ""
    api_get "/api/zones/${zone}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('content', '(нет содержимого)'))
"
}

do_sync_status() {
    section "GET /api/zones/sync-status"
    info "Сверка SOA serial по всем инстансам:"
    echo ""
    response=$(api_get "/api/zones/sync-status")

    if echo "$response" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d.get('warning'):
    print('WARNING: ' + d['warning'])
    sys.exit(0)
zones = d.get('zones', [])
for z in zones:
    print(f\"\\nЗона: {z['zone']}  (primary serial: {z.get('primary_serial', '?')})\")
    for s in z['servers']:
        synced = '✓' if s.get('synced') else ('✗' if s.get('synced') is False else '?')
        serial = s.get('serial') or '—'
        msg = f\"  {s.get('message','')}\".rstrip()
        print(f\"  {s['label']:15} {s['role']:10} serial: {str(serial):12} {synced}{msg}\")
" 2>/dev/null; then
        :
    else
        echo "$response" | pretty
    fi
}

do_instances() {
    section "GET /api/instances"
    info "Сконфигурированные Knot-инстансы:"
    echo ""
    api_get "/api/instances" | pretty
}

do_validate() {
    local zone="$1"
    section "POST /api/zones/${zone}/validate"
    info "Валидация zone-файла (dnspython):"
    echo ""
    # Читаем текущий контент зоны
    content=$(api_get "/api/zones/${zone}" | python3 -c "import sys,json; print(json.load(sys.stdin)['content'])")
    api_post "/api/zones/${zone}/validate" \
        "{\"content\": $(python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" <<< "$content")}" \
        | pretty
}

do_knot_conf() {
    section "GET /api/knot-conf"
    info "Текущий knot.conf из ConfigMap:"
    echo ""
    api_get "/api/knot-conf" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('raw', '(нет данных)'))
"
}

do_knot_validate() {
    section "POST /api/knot-conf/validate"
    info "Валидация knot.conf через knotc conf-check:"
    echo ""
    raw=$(api_get "/api/knot-conf" | python3 -c "import sys,json; print(json.load(sys.stdin)['raw'])")
    api_post "/api/knot-conf/validate" \
        "{\"content\": $(python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" <<< "$raw")}" \
        | pretty
}

do_axfr_status() {
    section "GET /api/knot-conf/axfr-status"
    info "Диагностика AXFR-секрета в кластере:"
    echo ""
    api_get "/api/knot-conf/axfr-status" | pretty
}

# ── Точка входа ──────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║           dnsadmin REST API — примеры запросов       ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
info "API: ${DNSADMIN_URL}"
echo ""

check_dnsadmin
do_login

case "$CMD" in
    login)        : ;;  # уже выполнили выше
    health)       do_health ;;
    zones)        do_zones ;;
    zone)         do_zone "$ZONE_ARG" ;;
    sync-status)  do_sync_status ;;
    instances)    do_instances ;;
    validate)     do_validate "$ZONE_ARG" ;;
    knot-conf)    do_knot_conf ;;
    knot-validate) do_knot_validate ;;
    axfr-status)  do_axfr_status ;;
    all)
        do_health
        do_zones
        do_sync_status
        do_instances
        do_knot_conf
        do_axfr_status
        ;;
    *)
        echo "Неизвестная команда: $CMD"
        head -20 "$0" | grep "^#" | sed 's/^# \{0,2\}//'
        exit 1
        ;;
esac

echo ""

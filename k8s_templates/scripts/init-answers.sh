#!/usr/bin/env bash
# init-answers.sh — интерактивный опросник: собирает ответы пользователя
# и сохраняет их в answers/<target>.env (KEY=VALUE).
#
# Аргументы:
#   --target NAME        имя цели/окружения (по умолчанию из env TARGET, иначе "prod")
#   --component NAME     компонент (по умолчанию dns-knot)
#   --no-cluster         не трогать кластер (контекст/ноды заполняются вручную)
#   -h|--help            помощь

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/answers.sh
source "$SCRIPT_DIR/lib/answers.sh"
# shellcheck source=lib/k8s.sh
source "$SCRIPT_DIR/lib/k8s.sh"

COMPONENT="${COMPONENT:-dns-knot}"
TARGET="${TARGET:-prod}"
NO_CLUSTER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --component) COMPONENT="$2"; shift 2 ;;
    --no-cluster) NO_CLUSTER=1; shift ;;
    -h|--help) echo "init-answers.sh --target NAME [--component NAME] [--no-cluster]"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

COMPONENT_DIR="$(cd "$SCRIPT_DIR/../components/$COMPONENT" && pwd)"
ZONES_DIR="$COMPONENT_DIR/zones"

answers_load "$TARGET"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Конструктор k8s-манифестов: $COMPONENT (цель: $TARGET)  ║"
echo "╚══════════════════════════════════════════════════════════╝"

# ── 1. Registry ────────────────────────────────────────────────────────────────
echo ""
info "Registry:"
answer REGISTRY "Реестр (например registry.summer-site.ru)" "registry.summer-site.ru"
answer REGISTRY_IMAGE "Путь образа dnsadmin (например dns-knot/dnsadmin)" "dns-knot/dnsadmin"
answer REGISTRY_PUSH_USER "PUSH-пользователь (запись)"
answer REGISTRY_PUSH_PASSWORD "PUSH-пароль" "" --required
answer REGISTRY_PULL_USER "PULL-пользователь (только чтение)"
answer REGISTRY_PULL_PASSWORD "PULL-пароль" "" --required
answer PULL_SECRET_NAME "Имя pull-secret для Deployment" "registry-summersite"

# ── 2. Namespace / PVC ─────────────────────────────────────────────────────────
echo ""
info "Пространство имён и хранилище:"
answer NAMESPACE "Namespace" "dns-knot"
answer PVC_SIZE "Размер PVC для primary (например 2Gi)" "2Gi"
answer PVC_STORAGE_CLASS "StorageClass" "local-path"

# ── 3. Кластер / ноды ──────────────────────────────────────────────────────────
echo ""
if [[ "$NO_CLUSTER" == "1" ]]; then
  info "Кластер не трогаем (--no-cluster). Укажите ноды вручную."
  answer CONTEXT "kubectl-контекст"
  answer DNS_PRIMARY_NODE "Primary DNS — имя ноды"
  answer DNS_PRIMARY_IP "Primary DNS — IP (listen)"
  answer SECONDARY_NODES "Secondary DNS — имена нод через пробел (пусто = нет)" ""
  answer SECONDARY_IPS "Secondary DNS — IP через пробел (параллельно нодам)" ""
  answer UI_NODE "UI (dnsadmin) — имя ноды" ""
else
  require_cmd kubectl
  select_context
  verify_context_access
  list_nodes
  echo ""
  answer CONTEXT "kubectl-контекст" "$(kubectl_ctx)" --no-store
  answers_save CONTEXT "$(kubectl_ctx)"
  # primary
  local_p="${DNS_PRIMARY_NODE:-}"
  if [[ -z "$local_p" ]]; then
    DNS_PRIMARY_NODE="$(pick_node "Выберите ноду для PRIMARY DNS")"
  fi
  DNS_PRIMARY_IP="${DNS_PRIMARY_IP:-$(node_external_ip "$DNS_PRIMARY_NODE")}"
  answers_save_many DNS_PRIMARY_NODE="$DNS_PRIMARY_NODE" DNS_PRIMARY_IP="$DNS_PRIMARY_IP"
  # secondary
  local_s="${SECONDARY_NODES:-}"
  if [[ -z "$local_s" ]]; then
    SECONDARY_NODES="$(pick_nodes_multi "Выберите ноды для SECONDARY DNS (номера через пробел, Enter = без вторичек)")"
    answers_save SECONDARY_NODES "$SECONDARY_NODES"
  fi
  # IP вторичек (параллельный список к SECONDARY_NODES)
  SECONDARY_IPS=""
  for s in $SECONDARY_NODES; do
    sip="$(node_external_ip "$s")"
    [[ -n "$sip" ]] || warn "Нет IP у вторички $s — беру IP primary."
    SECONDARY_IPS="$SECONDARY_IPS ${sip:-$DNS_PRIMARY_IP}"
  done
  SECONDARY_IPS="$(echo "$SECONDARY_IPS" | xargs)"
  answers_save SECONDARY_IPS "$SECONDARY_IPS"
  # ui
  local_u="${UI_NODE:-}"
  if [[ -z "$local_u" ]]; then
    UI_NODE="$(pick_node "Выберите ноду для UI (dnsadmin)" 0)"
  fi
  [[ -z "$UI_NODE" ]] && UI_NODE="$DNS_PRIMARY_NODE"
  answers_save UI_NODE "$UI_NODE"
fi

# identity и ID primary
domain_part="${DNS_PRIMARY_NODE#*.}"
DEFAULT_IDENTITY="ns.${domain_part}"
answer DNS_PRIMARY_IDENTITY "Identity primary NS (напр. ns.example.com)" "$DEFAULT_IDENTITY"
DNS_PRIMARY_ID="$(echo "$DNS_PRIMARY_NODE" | cut -d. -f1 | sed 's/[^a-z0-9-]/-/g')"
answers_save DNS_PRIMARY_ID "$DNS_PRIMARY_ID"

# ── 4. Ingress ─────────────────────────────────────────────────────────────────
echo ""
info "Публикация UI:"
answer_select INGRESS_HOST "На каком домене должен работать UI?" \
  "summer-site.ru (новый домен)|dnsadmin.summer-site.ru"$'\n'"summersite.ru (старый, отключён)|dnsadmin.summersite.ru"$'\n'"свой вариант|custom"
if [[ "$INGRESS_HOST" == "custom" ]]; then
  answer INGRESS_HOST "Hostname для UI (Ingress)"
fi
answer_yn INGRESS_TLS "Включить TLS (cert-manager letsencrypt)?" no
answer INGRESS_CLASS "IngressClass" "traefik"
answer INGRESS_ENTRYPOINT "Entrypoint" "web"

# ── 5. dnsadmin auth ───────────────────────────────────────────────────────────
echo ""
info "Доступ к UI (dnsadmin):"
answer ADMIN_USERNAME "Имя администратора" "admin"
local_ap="${ADMIN_PASSWORD:-}"
if [[ -z "$local_ap" ]]; then
  ask "Пароль администратора (Enter = сгенерировать): "
  read -r ADMIN_PASSWORD
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 18)}"
  answers_save ADMIN_PASSWORD "$ADMIN_PASSWORD"
fi
if [[ -z "${JWT_SECRET:-}" ]]; then
  JWT_SECRET="$(openssl rand -base64 32)"
  answers_save JWT_SECRET "$JWT_SECRET"
fi

# ── 6. DNS01 ───────────────────────────────────────────────────────────────────
echo ""
info "DNS01 (cert-manager):"
answer_yn DNS01 "Включить DNS01 (ClusterIssuer letsencrypt-dns)?" no
if [[ "$DNS01" == "yes" ]]; then
  answer ACME_EMAIL "E-mail для ACME" "admin@${INGRESS_HOST}"
  answer DNS01_NAMESERVER "Nameserver для DNS01 (наша первичка)" "${DNS_PRIMARY_IP}:53"
  answer DNS01_MODE "Режим DNS01: rfc2136 | webhook" "webhook"
  if [[ "$DNS01_MODE" == "webhook" ]]; then
    answer WEBHOOK_GROUP_NAME "GroupName для cert-manager webhook" "dnsadmin.knot.io"
    answer WEBHOOK_VERSION "Версия APIService" "v1"
  fi
fi

# ── 7. Зоны ────────────────────────────────────────────────────────────────────
echo ""
info "Зоны (из $ZONES_DIR):"
ZONE_FILES=()
mapfile -t ZONE_FILES < <(find "$ZONES_DIR" -maxdepth 1 -name '*.zone' | sort)
if [[ ${#ZONE_FILES[@]} -eq 0 ]]; then
  warn "В $ZONES_DIR нет *.zone. Укажите ZONE_DIR со своими зонами."
fi
for z in "${ZONE_FILES[@]}"; do echo "  $(basename "$z")"; done
answer ZONE_DIR "Дополнительный каталог zone-файлов (Enter = из компонента)" ""
answer DEFAULT_ZONE "Зона по умолчанию" "${DEFAULT_ZONE:-$(basename "${ZONE_FILES[0]}" .zone 2>/dev/null || echo k3s.local)}"

# ── Итог ───────────────────────────────────────────────────────────────────────
echo ""
success "Ответы сохранены: $ANSWERS_FILE"
echo ""
info "Далее:"
echo "  ./scripts/build.sh --target $TARGET"
echo "  ./scripts/deploy.sh --target $TARGET"

#!/usr/bin/env bash
# Деплой инстанса Knot DNS на конкретную ноду кластера.
# Создаёт: ConfigMap knot-config-<id>, PVC knot-data-<id>, Deployment knot-<id>.
# После добавления всех инстансов обновляет KNOT_INSTANCES в Deployment dnsadmin.
set -euo pipefail

NAMESPACE="${NAMESPACE:-dns-knot}"
KNOT_IMAGE="${KNOT_IMAGE:-cznic/knot:3.4}"
AXFR_SECRET_NAME="${AXFR_SECRET_NAME:-knot-axfr}"
DNSADMIN_DEPLOYMENT="${DNSADMIN_DEPLOYMENT:-dnsadmin}"
PVC_SIZE="${PVC_SIZE:-2Gi}"
PVC_STORAGE_CLASS="${PVC_STORAGE_CLASS:-local-path}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}→${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*"; }
die()     { echo -e "${RED}✗${NC} $*" >&2; exit 1; }
ask()     { echo -e "${YELLOW}?${NC} $1"; }

# ── Контекст kubectl ─────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       Деплой Knot DNS на ноду кластера           ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

CONTEXTS=$(kubectl config get-contexts -o name 2>/dev/null)
CURRENT_CTX=$(kubectl config current-context 2>/dev/null || echo "")

echo "Доступные контексты:"
i=1; declare -A CTX_MAP
while IFS= read -r ctx; do
    marker=""; [ "$ctx" = "$CURRENT_CTX" ] && marker=" ${GREEN}(текущий)${NC}"
    echo -e "  $i) $ctx$marker"
    CTX_MAP[$i]="$ctx"
    ((i++))
done <<< "$CONTEXTS"

echo ""
ask "Выберите контекст [Enter = текущий '$CURRENT_CTX']: "
read -r ctx_choice
if [[ -z "$ctx_choice" ]]; then
    KUBECTL_CTX="$CURRENT_CTX"
else
    KUBECTL_CTX="${CTX_MAP[$ctx_choice]:-}"
    [[ -z "$KUBECTL_CTX" ]] && die "Неверный выбор"
fi
KUBECTL="kubectl --context=$KUBECTL_CTX"
info "Контекст: $KUBECTL_CTX"
echo ""

# ── Ноды кластера ────────────────────────────────────────────────────────────

info "Ноды кластера:"
NODE_LINES=$($KUBECTL get nodes -o custom-columns='NAME:.metadata.name,STATUS:.status.conditions[-1].type,ROLES:.metadata.labels.kubernetes\.io/role,AGE:.metadata.creationTimestamp' --no-headers 2>/dev/null)
echo ""

declare -A NODE_MAP
i=1
while IFS= read -r line; do
    node_name=$(echo "$line" | awk '{print $1}')
    node_status=$(echo "$line" | awk '{print $2}')
    # Проверяем, есть ли уже Knot на этой ноде
    existing=$($KUBECTL get deployments -n "$NAMESPACE" -o jsonpath='{range .items[*]}{.metadata.name}:{.spec.template.spec.nodeName}{"\n"}{end}' 2>/dev/null \
        | grep ":$node_name" | cut -d: -f1 || true)
    knot_marker=""
    [[ -n "$existing" ]] && knot_marker=" ${GREEN}[Knot: $existing]${NC}"
    echo -e "  $i) $node_name  ($node_status)$knot_marker"
    NODE_MAP[$i]="$node_name"
    ((i++))
done <<< "$NODE_LINES"

echo ""
ask "На какую ноду деплоить Knot? [номер]: "
read -r node_choice
TARGET_NODE="${NODE_MAP[$node_choice]:-}"
[[ -z "$TARGET_NODE" ]] && die "Неверный выбор ноды"
info "Нода: $TARGET_NODE"

# ── Параметры инстанса ───────────────────────────────────────────────────────

echo ""
# Предлагаем имя по умолчанию на основе имени ноды (первый сегмент до точки)
default_id=$(echo "$TARGET_NODE" | cut -d. -f1 | sed 's/[^a-z0-9-]/-/g')
ask "Имя инстанса (будет использовано в именах ресурсов, напр. ns1/ns2) [по умолч: $default_id]: "
read -r INSTANCE_ID
INSTANCE_ID="${INSTANCE_ID:-$default_id}"
[[ "$INSTANCE_ID" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "Имя инстанса: только a-z, 0-9, дефис"

# Уже существует?
if $KUBECTL get deployment "knot-$INSTANCE_ID" -n "$NAMESPACE" &>/dev/null; then
    warn "Deployment knot-$INSTANCE_ID уже существует в namespace $NAMESPACE"
    ask "Перезаписать? [y/N]: "
    read -r overwrite
    [[ "$overwrite" =~ ^[Yy]$ ]] || { info "Отменено."; exit 0; }
fi

echo ""
ask "Публичный IP ноды $TARGET_NODE (будет использован в listen и пробах): "
read -r NODE_IP
[[ "$NODE_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Введите корректный IPv4"

echo ""
ask "Метка этого NS-сервера (напр. ns1.example.com) [по умолч: $INSTANCE_ID.example.com]: "
read -r NS_IDENTITY
NS_IDENTITY="${NS_IDENTITY:-$INSTANCE_ID.example.com}"

echo ""
info "Роль инстанса:"
echo "  1) primary   — авторитетный мастер, хранит зоны локально"
echo "  2) secondary — получает зоны по AXFR с primary"
ask "Роль [1/2, по умолч: 1]: "
read -r role_choice
case "${role_choice:-1}" in
    2) INSTANCE_ROLE="secondary" ;;
    *) INSTANCE_ROLE="primary" ;;
esac
info "Роль: $INSTANCE_ROLE"

PRIMARY_IP=""
if [[ "$INSTANCE_ROLE" = "secondary" ]]; then
    echo ""
    # Найдём IP существующего primary
    existing_primary_cm=$($KUBECTL get configmap -n "$NAMESPACE" -l "knot-role=primary" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    if [[ -n "$existing_primary_cm" ]]; then
        detected_primary_ip=$($KUBECTL get configmap "$existing_primary_cm" -n "$NAMESPACE" \
            -o jsonpath='{.metadata.annotations.knot-listen-ip}' 2>/dev/null || true)
        [[ -n "$detected_primary_ip" ]] && info "Обнаружен primary IP: $detected_primary_ip"
    fi
    ask "IP primary-сервера (откуда получать зоны по AXFR): "
    read -r PRIMARY_IP
    [[ "$PRIMARY_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Введите корректный IPv4"
fi

# ── Подтверждение ────────────────────────────────────────────────────────────

echo ""
echo "──────────────────────────────────────────"
echo "  Контекст:   $KUBECTL_CTX"
echo "  Namespace:  $NAMESPACE"
echo "  Нода:       $TARGET_NODE"
echo "  Инстанс:    knot-$INSTANCE_ID"
echo "  IP ноды:    $NODE_IP"
echo "  Identity:   $NS_IDENTITY"
echo "  Роль:       $INSTANCE_ROLE"
[[ -n "$PRIMARY_IP" ]] && echo "  Primary IP: $PRIMARY_IP"
echo "  PVC:        knot-data-$INSTANCE_ID (${PVC_SIZE}, ${PVC_STORAGE_CLASS})"
echo "  Image:      $KNOT_IMAGE"
echo "──────────────────────────────────────────"
echo ""
ask "Применить? [y/N]: "
read -r confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { info "Отменено."; exit 0; }

# ── Генерация knot.conf для инстанса ─────────────────────────────────────────

if [[ "$INSTANCE_ROLE" = "primary" ]]; then
    KNOT_CONF=$(cat <<EOF
server:
  listen: ${NODE_IP}@53
  identity: ${NS_IDENTITY}
  nsid: ${NS_IDENTITY}

log:
  - target: stdout
    any: info

database:
  storage: /var/lib/knot

include: /etc/knot/conf.d/axfr.conf

zone: []
EOF
)
else
    KNOT_CONF=$(cat <<EOF
server:
  listen: ${NODE_IP}@53
  identity: ${NS_IDENTITY}
  nsid: ${NS_IDENTITY}

log:
  - target: stdout
    any: info

database:
  storage: /var/lib/knot

include: /etc/knot/conf.d/axfr.conf

remote:
  - id: primary
    address: ${PRIMARY_IP}@53

# refresh-min-interval: принудительный поллинг primary не реже раза в 4 часа
# даже если NOTIFY не пришёл (защита от рассинхрона при потере связи)
zone-defaults:
  refresh-min-interval: 14400
  refresh-max-interval: 86400

zone: []
EOF
)
fi

# ── PVC ──────────────────────────────────────────────────────────────────────

info "Создаю PVC knot-data-$INSTANCE_ID ..."
$KUBECTL apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: knot-data-$INSTANCE_ID
  namespace: $NAMESPACE
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: $PVC_STORAGE_CLASS
  resources:
    requests:
      storage: $PVC_SIZE
EOF
success "PVC knot-data-$INSTANCE_ID"

# ── ConfigMap ─────────────────────────────────────────────────────────────────

info "Создаю ConfigMap knot-config-$INSTANCE_ID ..."
$KUBECTL apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: knot-config-$INSTANCE_ID
  namespace: $NAMESPACE
  labels:
    app: knot
    knot-instance: "$INSTANCE_ID"
    knot-role: "$INSTANCE_ROLE"
  annotations:
    knot-listen-ip: "$NODE_IP"
    knot-node: "$TARGET_NODE"
data:
  knot.conf: |
$(echo "$KNOT_CONF" | sed 's/^/    /')
EOF
success "ConfigMap knot-config-$INSTANCE_ID"

# ── Deployment ───────────────────────────────────────────────────────────────

info "Создаю Deployment knot-$INSTANCE_ID ..."
$KUBECTL apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: knot-$INSTANCE_ID
  namespace: $NAMESPACE
  labels:
    app: knot
    knot-instance: "$INSTANCE_ID"
    knot-role: "$INSTANCE_ROLE"
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: knot
      knot-instance: "$INSTANCE_ID"
  template:
    metadata:
      labels:
        app: knot
        knot-instance: "$INSTANCE_ID"
        knot-role: "$INSTANCE_ROLE"
    spec:
      nodeName: $TARGET_NODE
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      containers:
        - name: knot
          image: $KNOT_IMAGE
          imagePullPolicy: IfNotPresent
          command: [knotd]
          args: [-c, /etc/knot/knot.conf]
          ports:
            - name: dns-udp
              containerPort: 53
              protocol: UDP
            - name: dns-tcp
              containerPort: 53
              protocol: TCP
          readinessProbe:
            tcpSocket:
              host: $NODE_IP
              port: 53
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            tcpSocket:
              host: $NODE_IP
              port: 53
            initialDelaySeconds: 15
            periodSeconds: 20
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          volumeMounts:
            - name: knot-config
              mountPath: /etc/knot/knot.conf
              subPath: knot.conf
              readOnly: true
            - name: knot-zones
              mountPath: /zones
              readOnly: true
            - name: knot-axfr
              mountPath: /etc/knot/conf.d/axfr.conf
              subPath: axfr.conf
              readOnly: true
            - name: knot-data
              mountPath: /var/lib/knot
      volumes:
        - name: knot-config
          configMap:
            name: knot-config-$INSTANCE_ID
        - name: knot-zones
          configMap:
            name: knot-config-$INSTANCE_ID
        - name: knot-axfr
          secret:
            secretName: $AXFR_SECRET_NAME
        - name: knot-data
          persistentVolumeClaim:
            claimName: knot-data-$INSTANCE_ID
EOF
success "Deployment knot-$INSTANCE_ID"

# ── Обновляем KNOT_INSTANCES в dnsadmin ──────────────────────────────────────

info "Обновляю KNOT_INSTANCES в Deployment $DNSADMIN_DEPLOYMENT ..."

# Читаем текущее значение
current_instances=$($KUBECTL get deployment "$DNSADMIN_DEPLOYMENT" -n "$NAMESPACE" \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="KNOT_INSTANCES")].value}' 2>/dev/null || true)

new_entry="{\"id\":\"$INSTANCE_ID\",\"label\":\"$NS_IDENTITY\",\"configmap\":\"knot-config-$INSTANCE_ID\",\"deployment\":\"knot-$INSTANCE_ID\",\"ip\":\"$NODE_IP\",\"role\":\"$INSTANCE_ROLE\"}"

if [[ -z "$current_instances" ]] || [[ "$current_instances" = "[]" ]] || [[ "$current_instances" = "null" ]]; then
    new_instances="[$new_entry]"
else
    # Убираем старую запись с тем же id если есть, добавляем новую
    new_instances=$(echo "$current_instances" | python3 -c "
import sys, json
data = json.load(sys.stdin)
data = [x for x in data if x.get('id') != '$INSTANCE_ID']
data.append(json.loads('$new_entry'))
print(json.dumps(data))
")
fi

$KUBECTL set env deployment/"$DNSADMIN_DEPLOYMENT" -n "$NAMESPACE" \
    "KNOT_INSTANCES=$new_instances" 2>/dev/null \
    && success "KNOT_INSTANCES обновлён: $new_instances" \
    || warn "Не удалось обновить KNOT_INSTANCES в $DNSADMIN_DEPLOYMENT (обновите вручную)"

# ── Итог ─────────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════════"
success "Инстанс knot-$INSTANCE_ID задеплоен на $TARGET_NODE"
echo ""
info "Проверка:"
echo "  kubectl --context=$KUBECTL_CTX get pod -n $NAMESPACE -l knot-instance=$INSTANCE_ID"
echo "  dig @$NODE_IP version.bind TXT CH"
echo ""
info "Все инстансы Knot в кластере:"
$KUBECTL get deployments -n "$NAMESPACE" -l app=knot \
    -o custom-columns='ИНСТАНС:.metadata.name,РОЛЬ:.metadata.labels.knot-role,IP:.metadata.annotations.knot-listen-ip,НОДА:.spec.template.spec.nodeName' \
    --no-headers 2>/dev/null || true
echo "══════════════════════════════════════════════════"

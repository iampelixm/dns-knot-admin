#!/usr/bin/env bash
# build.sh — собирает k8s-манифесты из answers/<target>.env по шаблонам компонента.
# Работает офлайн (без кластера): читает ответы, рендерит шаблоны в out/<target>/.
#
# Аргументы:
#   --target NAME        имя цели (по умолчанию prod)
#   --component NAME     компонент (по умолчанию dns-knot)
#   -h|--help            помощь

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/answers.sh
source "$SCRIPT_DIR/lib/answers.sh"
# shellcheck source=lib/render.sh
source "$SCRIPT_DIR/lib/render.sh"

COMPONENT="${COMPONENT:-dns-knot}"
TARGET="${TARGET:-prod}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --component) COMPONENT="$2"; shift 2 ;;
    -h|--help) echo "build.sh --target NAME [--component NAME]"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

COMPONENT_DIR="$(cd "$SCRIPT_DIR/../components/$COMPONENT" && pwd)"
OUT_DIR="$(cd "$SCRIPT_DIR/../out" && pwd)/$TARGET"
CONF_FILE="$COMPONENT_DIR/component.conf"

[[ -f "$CONF_FILE" ]] || die "Нет $CONF_FILE для компонента $COMPONENT."
# shellcheck source=/dev/null
source "$CONF_FILE"

answers_load "$TARGET"
require_tools

info "Компонент: $COMPONENT ($DESCRIPTION)"
info "Цель: $TARGET"
info "Зоны: ${ZONE_DIR:-$COMPONENT_DIR/$ZONES_DIR}"

# ── Валидация ответов ──────────────────────────────────────────────────────────
[[ -n "${NAMESPACE:-}" ]]        || die "Нет NAMESPACE в ответах."
[[ -n "${DNS_PRIMARY_NODE:-}" ]] || die "Нет DNS_PRIMARY_NODE в ответах."
[[ -n "${DNS_PRIMARY_IP:-}" ]]   || die "Нет DNS_PRIMARY_IP в ответах."
[[ -n "${INGRESS_HOST:-}" ]]     || die "Нет INGRESS_HOST в ответах."

# ── Переменные для рендера ─────────────────────────────────────────────────────
export NAMESPACE PVC_SIZE PVC_STORAGE_CLASS
export DNS_PRIMARY_ID DNS_PRIMARY_NODE DNS_PRIMARY_IP DNS_PRIMARY_IDENTITY
export KNOT_IMAGE="${KNOT_IMAGE:-cznic/knot:3.4}"
export AXFR_SECRET_NAME="${AXFR_SECRET_NAME:-knot-axfr}"
export AXFR_KEY_ID="${AXFR_KEY_ID:-secondary-01}"
export PULL_SECRET_NAME
export UI_NODE="${UI_NODE:-$DNS_PRIMARY_NODE}"
export INGRESS_HOST INGRESS_CLASS INGRESS_ENTRYPOINT
export DEFAULT_ZONE
export ADMIN_USERNAME ADMIN_PASSWORD JWT_SECRET
export REGISTRY="${REGISTRY:-registry.summersite.ru}"
export DNSADMIN_IMAGE="${DNSADMIN_IMAGE:-$REGISTRY/$REGISTRY_IMAGE}"

# TLS-аннотации
if [[ "${INGRESS_TLS:-no}" == "yes" ]]; then
  export INGRESS_TLS_ANNOTATION="cert-manager.io/cluster-issuer: letsencrypt-prod"
  export INGRESS_TLS="tls:
  - hosts:
      - $INGRESS_HOST
    secretName: dnsadmin-$INGRESS_HOST-tls"
else
  export INGRESS_TLS_ANNOTATION="#"
  export INGRESS_TLS="#"
fi

# ── Зоны ───────────────────────────────────────────────────────────────────────
ZDIR="${ZONE_DIR:-$COMPONENT_DIR/$ZONES_DIR}"
if [[ ! -d "$ZDIR" ]]; then
  die "Каталог зон не найден: $ZDIR"
fi
ZONE_FILES=()
mapfile -t ZONE_FILES < <(find "$ZDIR" -maxdepth 1 -name '*.zone' | sort)
[[ ${#ZONE_FILES[@]} -eq 0 ]] && die "В $ZDIR нет *.zone файлов."

info "Зоны:"
for z in "${ZONE_FILES[@]}"; do echo "  $(basename "$z")"; done

# IP вторичек (для remote/ACL в axfr.conf)
SECONDARY_NODES_IPS=""
if [[ -n "${SECONDARY_NODES:-}" ]]; then
  # если IP вторичек заданы в ответах через SECONDARY_IPS (через пробел) — используем их,
  # иначе берём из DNS_PRIMARY_NODE замыканием (вторички без кластера не известны).
  SECONDARY_NODES_IPS="${SECONDARY_IPS:-$DNS_PRIMARY_IP}"
  SECONDARY_NODES_IPS="$(echo "$SECONDARY_NODES_IPS" | xargs)"
fi
export SECONDARY_NODES="${SECONDARY_NODES:-}"
export DNS01_FLAG="$DNS01"

# ── Генерация knot.conf / зон / axfr.conf / KNOT_INSTANCES ────────────────────
ZONE_GEN="$(mktemp)"
python3 - "$DNS_PRIMARY_IP" "$DNS_PRIMARY_IDENTITY" "$SECONDARY_NODES" \
  "$SECONDARY_NODES_IPS" "${ZONE_FILES[@]}" > "$ZONE_GEN" <<'PY'
import sys, os

primary_ip = sys.argv[1]
identity   = sys.argv[2]
secondaries = sys.argv[3].split() if sys.argv[3] else []
secondary_ips = sys.argv[4].split() if sys.argv[4] else []
zone_files  = sys.argv[5:]
dns01       = os.environ.get("DNS01_FLAG", "") == "yes"

sec_ip = dict(zip(secondaries, secondary_ips))

remotes = []
acl_addrs = []
for s in secondaries:
    sid = s.split(".")[0]
    remotes.append(f"  - id: {sid}-remote\n    address: {sec_ip.get(s, primary_ip)}@53\n    key: secondary-01")
    acl_addrs.append(sec_ip.get(s, primary_ip))

zones = [os.path.basename(zf)[:-5] for zf in zone_files]

notify = ", ".join([f"{s.split('.')[0]}-remote" for s in secondaries]) or ""
primary_zones = []
secondary_zones = []
for d in zones:
    entry = f"  - domain: {d}\n    file: /zones/{d}.zone\n    acl: [axfr-allowed"
    if dns01:
        entry += ", update-allowed"
    entry += "]"
    if notify:
        entry += f"\n    notify: [{notify}]"
    entry += "\n    dnssec-signing: on"
    primary_zones.append(entry)
    secondary_zones.append(f"      - domain: {d}\n        master: [primary]\n        acl: [notify-allowed]")

knot_conf = f"""server:
  listen: {primary_ip}@53
  identity: {identity}
  nsid: {identity}

log:
  - target: stdout
    any: info

database:
  storage: /var/lib/knot

include: /etc/knot/conf.d/axfr.conf

zone:
""" + "\n".join(primary_zones) + "\n"

zones_data = []
for zf in zone_files:
    domain = os.path.basename(zf)
    content = open(zf).read().rstrip("\n")
    zones_data.append(f"  {domain}: |\n    " + "\n    ".join(content.splitlines()))

def emit(section, body):
    print("SECTION__" + section)
    print(body)
    print("SECTION_END__")

emit("KNOT_CONF", "\n".join("    " + l for l in knot_conf.splitlines()))
emit("ZONES_DATA", "\n".join(zones_data))
emit("PRIMARY_ZONES", "\n".join(primary_zones))
emit("SECONDARY_ZONES", "\n".join(secondary_zones))
emit("REMOTES", "\n".join(remotes))
emit("ACL_ADDRS", ", ".join(acl_addrs))
PY

parse_section() {
  local name="$1"
  awk -v name="$name" '
    $0 == "SECTION__" name {f=1; next}
    $0 == "SECTION_END__" {if (f) exit}
    f {print}
  ' "$ZONE_GEN"
}

export KNOT_CONF="$(parse_section KNOT_CONF)"
export ZONES_DATA="$(parse_section ZONES_DATA)"
export PRIMARY_ZONES="$(parse_section PRIMARY_ZONES)"
export SECONDARY_ZONES="$(parse_section SECONDARY_ZONES)"
REMOTES="$(parse_section REMOTES)"
ACL_ADDRS="$(parse_section ACL_ADDRS)"
rm -f "$ZONE_GEN"

# ── TSIG-ключи ─────────────────────────────────────────────────────────────────
info "Генерация TSIG-ключей..."
if [[ -n "${TSIG_SECRET:-}" ]]; then
  AXFR_KEY_SECRET="$TSIG_SECRET"
else
  AXFR_KEY_SECRET="$(openssl rand -base64 32)"
fi
export AXFR_KEY_SECRET

AXFR_CONF="key:
  - id: ${AXFR_KEY_ID}
    algorithm: hmac-sha256
    secret: ${AXFR_KEY_SECRET}
${REMOTES:+remote:
${REMOTES}
}
acl:
"
if [[ -n "$ACL_ADDRS" ]]; then
  AXFR_CONF+="  - id: axfr-allowed
    action: transfer
    address: [${ACL_ADDRS}]
    key: ${AXFR_KEY_ID}
"
fi
if [[ "$DNS01" == "yes" ]]; then
  AXFR_CONF+="  - id: certmanager-key
    algorithm: hmac-sha256
    secret: ${CERTMANAGER_TSIG_SECRET}
"
  AXFR_CONF+="  - id: update-allowed
    action: update
    key: certmanager-key
"
fi
export AXFR_CONF="$(printf '%s\n' "$AXFR_CONF" | sed 's/^/    /')"

# ── KNOT_INSTANCES для dnsadmin ────────────────────────────────────────────────
KNOT_INSTANCES_JSON="$(python3 - "$DNS_PRIMARY_ID" "$DNS_PRIMARY_IDENTITY" "$DNS_PRIMARY_IP" "$DNS_PRIMARY_NODE" "$SECONDARY_NODES" "$SECONDARY_NODES_IPS" <<'PY'
import sys, json
pid, plabel, pip, pnode = sys.argv[1:5]
secondaries = sys.argv[5].split() if len(sys.argv) > 5 and sys.argv[5] else []
secondary_ips = sys.argv[6].split() if len(sys.argv) > 6 and sys.argv[6] else []
sec_ip = dict(zip(secondaries, secondary_ips))
instances = [{"id": pid, "label": f"{pid} (primary)", "ip": pip, "role": "primary",
              "configmap": "knot-config", "deployment": "knot"}]
for s in secondaries:
    sid = s.split(".")[0]
    instances.append({"id": sid, "label": f"{sid} (secondary)", "ip": sec_ip.get(s, pip),
                      "role": "secondary",
                      "configmap": f"knot-config-{sid}", "deployment": f"knot-{sid}"})
print(json.dumps(instances, ensure_ascii=False))
PY
)"
export KNOT_INSTANCES="$KNOT_INSTANCES_JSON"

# ── DNS01-переменные ───────────────────────────────────────────────────────────
if [[ "$DNS01" == "yes" ]]; then
  export ACME_EMAIL="${ACME_EMAIL:-admin@$INGRESS_HOST}"
  export DNS01_NAMESERVER="${DNS01_NAMESERVER:-$DNS_PRIMARY_IP:53}"
  export DNS01_MODE="${DNS01_MODE:-rfc2136}"
  export CERT_MANAGER_RBAC="  - apiGroups: [\"\"]
    resources: [\"secrets\"]
    resourceNames: [\"certmanager-tsig\"]
    verbs: [\"get\", \"create\", \"update\", \"patch\", \"delete\"]"

  # TSIG-ключ для RFC2136 — всегда нужен, даже в webhook-режиме
  # (cert-manager всё равно требует NS для проверки)
  if [[ -z "${CERTMANAGER_TSIG_SECRET:-}" ]]; then
    CERTMANAGER_TSIG_SECRET="$(openssl rand -base64 32)"
  fi
  export CERTMANAGER_TSIG_SECRET

  # Webhook-specific
  if [[ "$DNS01_MODE" == "webhook" ]]; then
    export WEBHOOK_GROUP_NAME="${WEBHOOK_GROUP_NAME:-dnsadmin.knot.io}"
    export WEBHOOK_VERSION="${WEBHOOK_VERSION:-v1}"

    # Генерация самоподписанного CA и серверного сертификата для webhook
    info "Генерация TLS-сертификатов для cert-manager webhook..."
    TMP_CA_KEY="$(mktemp)" TMP_CA_CERT="$(mktemp)"
    TMP_SERVER_KEY="$(mktemp)" TMP_SERVER_CSR="$(mktemp)" TMP_SERVER_CERT="$(mktemp)"
    TMP_SERVER_EXT="$(mktemp)"

    openssl req -x509 -newkey rsa:2048 -keyout "$TMP_CA_KEY" -out "$TMP_CA_CERT" \
      -days 3650 -nodes -subj "/CN=dnsadmin-webhook-ca" 2>/dev/null

    openssl req -newkey rsa:2048 -keyout "$TMP_SERVER_KEY" -out "$TMP_SERVER_CSR" \
      -nodes -subj "/CN=dnsadmin.${NAMESPACE}.svc" 2>/dev/null

    printf "subjectAltName = DNS:dnsadmin.%s.svc\n" "$NAMESPACE" > "$TMP_SERVER_EXT"

    openssl x509 -req -in "$TMP_SERVER_CSR" -CA "$TMP_CA_CERT" -CAkey "$TMP_CA_KEY" \
      -CAcreateserial -out "$TMP_SERVER_CERT" -days 365 -extfile "$TMP_SERVER_EXT" 2>/dev/null

    export WEBHOOK_CA_BUNDLE="$(base64 -w0 < "$TMP_CA_CERT")"
    export WEBHOOK_TLS_CERT="$(base64 -w0 < "$TMP_SERVER_CERT")"
    export WEBHOOK_TLS_KEY="$(base64 -w0 < "$TMP_SERVER_KEY")"

    rm -f "$TMP_CA_KEY" "$TMP_CA_CERT" "$TMP_SERVER_KEY" "$TMP_SERVER_CSR" "$TMP_SERVER_CERT" "$TMP_SERVER_EXT"
  else
    export WEBHOOK_GROUP_NAME=""
    export WEBHOOK_VERSION=""
    export WEBHOOK_CA_BUNDLE=""
    export WEBHOOK_TLS_CERT=""
    export WEBHOOK_TLS_KEY=""
  fi
else
  export ACME_EMAIL=""
  export DNS01_NAMESERVER=""
  export CERT_MANAGER_RBAC=""
  export WEBHOOK_GROUP_NAME=""
  export WEBHOOK_VERSION=""
  export WEBHOOK_CA_BUNDLE=""
  export WEBHOOK_TLS_CERT=""
  export WEBHOOK_TLS_KEY=""
  export DNS01_MODE=""
fi

# ── Рендер ─────────────────────────────────────────────────────────────────────
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
export OUT_DIR

# base — всегда
render_group "$COMPONENT_DIR/$BASE_TEMPLATES" "$OUT_DIR"

# secondary — на каждую вторичку, если условие выполнено
if [[ -n "$SECONDARY_COND" && -n "${!SECONDARY_COND:-}" ]]; then
  info "Secondary-шаблоны:"
  # параллельные списки SECONDARY_NODES / SECONDARY_IPS
  sec_nodes=($SECONDARY_NODES)
  sec_ips=($SECONDARY_NODES_IPS)
  for ((i=0; i<${#sec_nodes[@]}; i++)); do
    s="${sec_nodes[$i]}"
    export INSTANCE_ID="$(echo "$s" | cut -d. -f1 | sed 's/[^a-z0-9-]/-/g')"
    export NODE_NAME="$s"
    export NODE_IP="${sec_ips[$i]:-$DNS_PRIMARY_IP}"
    domain_part="${DNS_PRIMARY_NODE#*.}"
    export NS_IDENTITY="${INSTANCE_ID}.${domain_part}"
    export PRIMARY_IP="$DNS_PRIMARY_IP"
    render_group "$COMPONENT_DIR/$SECONDARY_TEMPLATES" "$OUT_DIR" "secondary-${INSTANCE_ID}-"
    unset NODE_IP NS_IDENTITY
  done
fi

# cert-manager — если DNS01=yes
if [[ -n "$CERT_MANAGER_COND" && "${!CERT_MANAGER_COND:-no}" == "yes" ]]; then
  info "cert-manager-шаблоны (DNS01):"
  mkdir -p "$OUT_DIR/cert-manager"
  render_group "$COMPONENT_DIR/$CERT_MANAGER_TEMPLATES" "$OUT_DIR/cert-manager"

  # Удаляем файлы, не соответствующие DNS01_MODE
  if [[ "$DNS01_MODE" == "webhook" ]]; then
    rm -f "$OUT_DIR/cert-manager/cluster-issuer-rfc2136.yaml"
  elif [[ "$DNS01_MODE" == "rfc2136" ]]; then
    rm -f "$OUT_DIR/cert-manager/cluster-issuer-webhook.yaml" \
          "$OUT_DIR/cert-manager/apiservice-webhook.yaml" \
          "$OUT_DIR/cert-manager/dnsadmin-webhook-tls-secret.yaml"
  fi
fi

# ── Патч deployment + service для webhook (TLS + порт 8443) ──────────────
if [[ "$DNS01_MODE" == "webhook" ]]; then
  info "Патч deployment/service для cert-manager webhook (TLS + 8443)..."
  python3 - "$OUT_DIR/80-dnsadmin-deployment.yaml" "$OUT_DIR/90-dnsadmin-service.yaml" <<'PY'
import sys, yaml

dep_path, svc_path = sys.argv[1], sys.argv[2]

# Patch deployment
with open(dep_path) as f:
    dep = yaml.safe_load(f)

container = dep["spec"]["template"]["spec"]["containers"][0]

# Add port 8443
container["ports"].append({
    "name": "https-webhook",
    "containerPort": 8443
})

# Add TLS env vars
container["env"].extend([
    {"name": "TLS_CERT_PATH", "value": "/etc/dnsadmin-tls/tls.crt"},
    {"name": "TLS_KEY_PATH", "value": "/etc/dnsadmin-tls/tls.key"},
])

# Add volume mount
container.setdefault("volumeMounts", []).append({
    "name": "dnsadmin-tls",
    "mountPath": "/etc/dnsadmin-tls",
    "readOnly": True,
})

# Add volume
dep["spec"]["template"]["spec"].setdefault("volumes", []).append({
    "name": "dnsadmin-tls",
    "secret": {"secretName": "dnsadmin-webhook-tls", "defaultMode": 0o400},
})

with open(dep_path, "w") as f:
    yaml.dump(dep, f, default_flow_style=False, sort_keys=False)

# Patch service
with open(svc_path) as f:
    svc = yaml.safe_load(f)

svc["spec"]["ports"].append({
    "name": "https-webhook",
    "port": 443,
    "targetPort": 8443,
})

with open(svc_path, "w") as f:
    yaml.dump(svc, f, default_flow_style=False, sort_keys=False)
PY
fi

# ── Валидация YAML ─────────────────────────────────────────────────────────────
info "Проверка YAML..."
if ! python3 -c "import yaml" 2>/dev/null; then
  warn "Модуль PyYAML не установлен — пропускаю валидацию (манифесты можно проверить: kubectl apply --dry-run=client -f)."
else
  local fail=0
  while IFS= read -r -d '' f; do
    if ! python3 -c "import yaml,sys; list(yaml.safe_load_all(open(sys.argv[1])))" "$f" 2>/dev/null; then
      warn "Некорректный YAML: $f"
      fail=1
    fi
  done < <(find "$OUT_DIR" -name '*.yaml' -print0)
  [[ $fail -eq 0 ]] && success "Все манифесты корректны."
fi

# ── Итог ───────────────────────────────────────────────────────────────────────
echo ""
success "Манифесты собраны в $OUT_DIR"
echo ""
info "Далее:"
echo "  ./scripts/deploy.sh --target $TARGET"

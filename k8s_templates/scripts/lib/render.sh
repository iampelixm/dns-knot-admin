#!/usr/bin/env bash
# Рендер шаблонов манифестов: заменяет {{PLACEHOLDER}} на значения из окружения.
# Источник: source lib/render.sh
set -euo pipefail

# Переменные, разрешённые к подстановке (защита от случайных {{}} в шаблонах)
RENDER_VARS=(
  NAMESPACE PVC_SIZE PVC_STORAGE_CLASS
  DNS_PRIMARY_ID DNS_PRIMARY_NODE DNS_PRIMARY_IP DNS_PRIMARY_IDENTITY
  KNOT_IMAGE AXFR_SECRET_NAME AXFR_CONF PRIMARY_ZONES KNOT_CONF ZONES_DATA
  INSTANCE_ID NODE_NAME NODE_IP NS_IDENTITY PRIMARY_IP AXFR_KEY_ID SECONDARY_ZONES
  UI_NODE ADMIN_USERNAME ADMIN_PASSWORD JWT_SECRET DEFAULT_ZONE KNOT_INSTANCES
  DNSADMIN_IMAGE PULL_SECRET_NAME
  INGRESS_HOST INGRESS_CLASS INGRESS_ENTRYPOINT INGRESS_TLS INGRESS_TLS_ANNOTATION
  ACME_EMAIL DNS01_NAMESERVER CERTMANAGER_TSIG_SECRET CERT_MANAGER_RBAC
  WEBHOOK_GROUP_NAME WEBHOOK_VERSION WEBHOOK_CA_BUNDLE WEBHOOK_TLS_CERT WEBHOOK_TLS_KEY
)

# Рендер одного файла шаблона → заданный выходной путь
render_template() {
  local tpl="$1" out="$2"
  [[ -f "$tpl" ]] || die "Шаблон не найден: $tpl"
  mkdir -p "$(dirname "$out")"
  python3 - "$tpl" "$out" <<'PY'
import os, sys
tpl, out = sys.argv[1], sys.argv[2]
allowed = set("""NAMESPACE PVC_SIZE PVC_STORAGE_CLASS
DNS_PRIMARY_ID DNS_PRIMARY_NODE DNS_PRIMARY_IP DNS_PRIMARY_IDENTITY
KNOT_IMAGE AXFR_SECRET_NAME AXFR_CONF PRIMARY_ZONES KNOT_CONF ZONES_DATA
INSTANCE_ID NODE_NAME NODE_IP NS_IDENTITY PRIMARY_IP AXFR_KEY_ID SECONDARY_ZONES
UI_NODE ADMIN_USERNAME ADMIN_PASSWORD JWT_SECRET DEFAULT_ZONE KNOT_INSTANCES
DNSADMIN_IMAGE PULL_SECRET_NAME
INGRESS_HOST INGRESS_CLASS INGRESS_ENTRYPOINT INGRESS_TLS INGRESS_TLS_ANNOTATION
ACME_EMAIL DNS01_NAMESERVER CERTMANAGER_TSIG_SECRET CERT_MANAGER_RBAC
WEBHOOK_GROUP_NAME WEBHOOK_VERSION WEBHOOK_CA_BUNDLE WEBHOOK_TLS_CERT WEBHOOK_TLS_KEY""".split())
with open(tpl) as f:
    text = f.read()
def repl(m):
    var = m.group(1)
    if var not in allowed:
        return m.group(0)
    return os.environ.get(var, "")
import re
text = re.sub(r"\{\{\s*(\w+)\s*\}\}", repl, text)
with open(out, "w") as f:
    f.write(text)
PY
  echo "$out"
}

# Рендер группы шаблонов из каталога в выходной каталог
render_group() {
  local tpl_dir="$1" out_dir="$2" prefix="${3:-}"
  local f out
  for f in "$tpl_dir"/*.yaml; do
    [[ -f "$f" ]] || continue
    out="$out_dir/${prefix}$(basename "$f")"
    render_template "$f" "$out"
    info "Рендер: $out"
  done
}

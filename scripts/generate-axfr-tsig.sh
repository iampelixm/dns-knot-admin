#!/usr/bin/env bash
# Генерация YAML-фрагмента TSIG + пример ACL для Secret knot-axfr (ключ axfr.conf).
# Требуется Docker с образом Knot (тот же, что в dnsadmin / кластере).
set -euo pipefail

KNOT_IMAGE="${KNOT_IMAGE:-cznic/knot:3.4}"
KEY_ID="${1:-axfr-$(openssl rand -hex 4)}"
ACL_ID="${ACL_ID:-axfr-allowed}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker не найден в PATH" >&2
  exit 1
fi

out="$(docker run --rm "$KNOT_IMAGE" keymgr -t "$KEY_ID" hmac-sha256)"

# Убрать комментарий вида # hmac-sha256:...
yaml="$(printf '%s\n' "$out" | grep -v '^[[:space:]]*#')"

cat <<EOF
${yaml}

acl:
  - id: $ACL_ID
    action: transfer
    address:
      - 127.0.0.1
    key: $KEY_ID
EOF

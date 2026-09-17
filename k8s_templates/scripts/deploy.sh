#!/usr/bin/env bash
# deploy.sh — применяет собранные манифесты out/<target>/ в кластер и выполняет
# кластерные шаги (сборка образа, pull-secret, rollout, DNS01-настройка).
#
# Аргументы:
#   --target NAME        имя цели (по умолчанию prod)
#   --component NAME     компонент (по умолчанию dns-knot)
#   --skip-image-build   не собирать/пушить образ dnsadmin
#   --dry-run            только показать, что будет применено (без apply)
#   -h|--help            помощь

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/answers.sh
source "$SCRIPT_DIR/lib/answers.sh"
# shellcheck source=lib/k8s.sh
source "$SCRIPT_DIR/lib/k8s.sh"
# shellcheck source=lib/registry.sh
source "$SCRIPT_DIR/lib/registry.sh"

COMPONENT="${COMPONENT:-dns-knot}"
TARGET="${TARGET:-prod}"
SKIP_BUILD=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --component) COMPONENT="$2"; shift 2 ;;
    --skip-image-build) SKIP_BUILD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) echo "deploy.sh --target NAME [--component NAME] [--skip-image-build] [--dry-run]"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

COMPONENT_DIR="$(cd "$SCRIPT_DIR/../components/$COMPONENT" && pwd)"
OUT_DIR="$(cd "$SCRIPT_DIR/../out" && pwd)/$TARGET"

[[ -f "$COMPONENT_DIR/component.conf" ]] || die "Нет компонента $COMPONENT."
# shellcheck source=/dev/null
source "$COMPONENT_DIR/component.conf"

answers_load "$TARGET"

[[ -d "$OUT_DIR" ]] || die "Манифесты не собраны. Сначала: ./scripts/build.sh --target $TARGET"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Деплой $COMPONENT (цель: $TARGET)                      ║"
echo "╚══════════════════════════════════════════════════════════╝"

# ── 1. Контекст ────────────────────────────────────────────────────────────────
require_cmd kubectl
CONTEXT="${CONTEXT:-}"
if [[ -z "$CONTEXT" ]]; then
  select_context
else
  kubectl_set_context "$CONTEXT"
fi
verify_context_access

export REGISTRY="${REGISTRY:-registry.summer-site.ru}"
export REGISTRY_IMAGE="${REGISTRY_IMAGE:-dns-knot/dnsadmin}"
export DNSADMIN_IMAGE="${DNSADMIN_IMAGE:-$REGISTRY/$REGISTRY_IMAGE}"
export NAMESPACE="${NAMESPACE:-dns-knot}"
export PULL_SECRET_NAME="${PULL_SECRET_NAME:-registry-summersite}"

# ── 2. Учётные данные registry ────────────────────────────────────────────────
info "Реестр: $REGISTRY"
check_push_pull
validate_push_creds "$REGISTRY_PUSH_USER" "$REGISTRY_PUSH_PASSWORD"
validate_pull_creds "$REGISTRY_PULL_USER" "$REGISTRY_PULL_PASSWORD"

# ── 3. Сборка и push образа dnsadmin ──────────────────────────────────────────
if [[ "$SKIP_BUILD" != "1" ]]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  if [[ -d "$REPO_ROOT/backend" && -d "$REPO_ROOT/src" ]]; then
    PKG="$REPO_ROOT/package.json"
    VERSION=""
    if [[ -f "$PKG" ]]; then
      VERSION="$(python3 -c "import json;print(json.load(open('$PKG'))['version'])" 2>/dev/null || true)"
    fi
    [[ -n "$VERSION" ]] && DNSADMIN_IMAGE="$REGISTRY/$REGISTRY_IMAGE:$VERSION"
    export DNSADMIN_IMAGE
    info "Сборка и push образа $DNSADMIN_IMAGE"
    require_cmd docker
    docker_login "$REGISTRY_PUSH_USER" "$REGISTRY_PUSH_PASSWORD"
    docker build -t "$DNSADMIN_IMAGE" "$REPO_ROOT"
    docker push "$DNSADMIN_IMAGE"
    success "Образ запушен: $DNSADMIN_IMAGE"
  else
    warn "Каталог репозитория dnsadmin-ui не найден ($REPO_ROOT) — пропускаю сборку."
  fi
else
  info "Сборка пропущена (--skip-image-build). Образ: $DNSADMIN_IMAGE"
fi

# ── 4. Apply ───────────────────────────────────────────────────────────────────
info "Манифесты:"
find "$OUT_DIR" -name '*.yaml' | sort | while read -r f; do echo "  ${f#$OUT_DIR/}"; done

if [[ "$DRY_RUN" == "1" ]]; then
  success "Dry-run: применяется из $OUT_DIR в namespace '$NAMESPACE' (ничего не сделано)."
  exit 0
fi

confirm "Применить манифесты в кластер (контекст: $(kubectl_ctx), namespace: $NAMESPACE)?" || die "Отменено."

# Namespace ОБЯЗАТЕЛЬНО первым
if [[ -f "$OUT_DIR/00-namespace.yaml" ]]; then
  info "Применяю Namespace..."
  $KUBECTL apply -f "$OUT_DIR/00-namespace.yaml"
fi

# TLS-секрет для webhook — ДО основного deployment (иначе pod не сможет смонтировать)
if [[ "${DNS01_MODE:-}" == "webhook" && -f "$OUT_DIR/cert-manager/dnsadmin-webhook-tls-secret.yaml" ]]; then
  info "TLS-секрет для webhook (применяется перед deployment)..."
  $KUBECTL apply -f "$OUT_DIR/cert-manager/dnsadmin-webhook-tls-secret.yaml"
fi

info "Применяю остальные манифесты..."
for f in "$OUT_DIR"/*.yaml "$OUT_DIR"/secondary-*.yaml; do
  [[ -f "$f" ]] || continue
  [[ "$(basename "$f")" == "00-namespace.yaml" ]] && continue
  $KUBECTL apply -f "$f"
done
if [[ -d "$OUT_DIR/cert-manager" ]]; then
  info "Применяю cert-manager-манифесты..."
  $KUBECTL apply -f "$OUT_DIR/cert-manager/"
fi

# pull-secret из PULL-учётки (read-only)
ensure_pull_secret "$NAMESPACE"

# ── 5. Rollout ─────────────────────────────────────────────────────────────────
info "Ожидание rollout Deployment knot и dnsadmin..."
$KUBECTL -n "$NAMESPACE" rollout status deployment/knot --timeout=300s || warn "knot не стал ready за отведённое время"
$KUBECTL -n "$NAMESPACE" rollout status deployment/dnsadmin --timeout=300s || warn "dnsadmin не стал ready за отведённое время"

# ── 6. DNS01 ───────────────────────────────────────────────────────────────────
if [[ "${DNS01:-no}" == "yes" ]]; then
  info "Настройка DNS01 (cert-manager)..."
  DNS01_NAMESERVER="${DNS01_NAMESERVER:-$DNS_PRIMARY_IP:53}"
  if $KUBECTL -n cert-manager get deployment cert-manager >/dev/null 2>&1; then
    NEW_ARGS="$($KUBECTL -n cert-manager get deployment cert-manager -o json \
      | python3 -c "
import sys, json
d = json.load(sys.stdin)
args = d['spec']['template']['spec']['containers'][0].get('args', [])
args = [a for a in args if not a.startswith('--dns01-recursive-nameservers')]
args += ['--dns01-recursive-nameservers=$DNS01_NAMESERVER', '--dns01-recursive-nameservers-only']
print(json.dumps(args))
")"
    $KUBECTL -n cert-manager patch deployment cert-manager --type=strategic -p \
      "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"cert-manager-controller\",\"args\":$NEW_ARGS}]}}}}" \
      || warn "cert-manager не патчится — проверьте вручную."
    success "cert-manager пропатчен: --dns01-recursive-nameservers=${DNS01_NAMESERVER} + --dns01-recursive-nameservers-only"
  else
    warn "Deployment cert-manager не найден — DNS01 требует cert-manager в namespace 'cert-manager'."
  fi
fi

# ── Итог ───────────────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
success "Развёртывание завершено (контекст: $(kubectl_ctx), ns: $NAMESPACE)"
echo ""
info "Проверка DNS:"
echo "  dig @${DNS_PRIMARY_IP} version.bind TXT CH"
echo "  dig @${DNS_PRIMARY_IP} ${DEFAULT_ZONE} SOA +short"
echo ""
info "UI:"
echo "  https://${INGRESS_HOST} (логин: ${ADMIN_USERNAME:-admin})"
echo ""
info "Полезные команды:"
echo "  kubectl --context=$(kubectl_ctx) -n $NAMESPACE get pods -o wide"
echo "  kubectl --context=$(kubectl_ctx) -n $NAMESPACE get cm knot-config -o yaml"
echo "══════════════════════════════════════════════════════════"

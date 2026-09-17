#!/usr/bin/env bash
# Учётные данные registry: раздельные PUSH (запись) и PULL (только чтение).
# Источник: source lib/registry.sh
set -euo pipefail

# Проверка доступа к registry по HTTP API: 0 = доступ, 1 = нет.
registry_api_ok() {
  local user="$1" pass="$2" reg="$3"
  curl -sf -u "$user:$pass" "https://$reg/v2/" -o /dev/null 2>/dev/null
}

# Пытается выполнить действие, требующее ЗАПИСИ (start blob upload).
# 0 = запись разрешена, 1 = нет (только чтение).
# Проверка идёт против РЕАЛЬНОГО репозитория (REGISTRY_IMAGE) — на несуществующем
# репо registry отвечает 404 даже для пользователя с правами записи.
registry_can_push() {
  local user="$1" pass="$2" reg="$3" repo="${4:-$REGISTRY_IMAGE}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -u "$user:$pass" \
    -X POST "https://$reg/v2/$repo/blobs/uploads/" 2>/dev/null || echo 000)"
  case "$code" in
    2*) return 0 ;;
    404) warn "Проверка записи: репозиторий '$repo' не найден — права записи не подтверждены."
         return 0 ;;
    401|403|000) return 1 ;;
    *) return 1 ;;
  esac
}

validate_push_creds() {
  local user="$1" pass="$2"
  if ! registry_api_ok "$user" "$pass" "$REGISTRY"; then
    die "Не удалось аутентифицироваться в $REGISTRY (push-учётка)."
  fi
  if registry_can_push "$user" "$pass" "$REGISTRY"; then
    success "PUSH-учётка '$user' имеет права на запись."
  else
    warn "PUSH-учётка '$user' НЕ имеет прав на запись в $REGISTRY — push образа будет невозможен."
  fi
}

validate_pull_creds() {
  local user="$1" pass="$2"
  if ! registry_api_ok "$user" "$pass" "$REGISTRY"; then
    die "Не удалось аутентифицироваться в $REGISTRY (pull-учётка)."
  fi
  if registry_can_push "$user" "$pass" "$REGISTRY"; then
    warn "PULL-учётка '$user' имеет права на запись в $REGISTRY (ожидалась read-only)."
  else
    success "PULL-учётка '$user' — read-only (запись запрещена)."
  fi
}

# Проверка, что PUSH/PULL заданы и разные
check_push_pull() {
  [[ -n "${REGISTRY_PUSH_USER:-}" && -n "${REGISTRY_PUSH_PASSWORD:-}" ]] || \
    die "Не задана PUSH-учётка registry (REGISTRY_PUSH_USER/PASSWORD)."
  [[ -n "${REGISTRY_PULL_USER:-}" && -n "${REGISTRY_PULL_PASSWORD:-}" ]] || \
    die "Не задана PULL-учётка registry (REGISTRY_PULL_USER/PASSWORD)."
  if [[ "$REGISTRY_PUSH_USER" == "$REGISTRY_PULL_USER" ]]; then
    warn "PUSH и PULL используют одну учётку ($REGISTRY_PUSH_USER). Рекомендуются раздельные права."
  fi
}

docker_login() {
  echo "$2" | docker login "$REGISTRY" -u "$1" --password-stdin >/dev/null 2>&1 \
    || die "docker login $REGISTRY ($1) не удался."
  success "docker login $REGISTRY (push: $1) — OK"
}

# Создаёт/обновляет imagePullSecret из PULL-учётных данных (read-only).
ensure_pull_secret() {
  local ns="${1:-$NAMESPACE}" secret_name="${PULL_SECRET_NAME:-registry-summersite}"
  info "Создаю pull-secret '$secret_name' в namespace '$ns' из PULL-учётки (read-only)..."
  $KUBECTL create secret docker-registry "$secret_name" \
    --namespace "$ns" \
    --docker-server="$REGISTRY" \
    --docker-username="$REGISTRY_PULL_USER" \
    --docker-password="$REGISTRY_PULL_PASSWORD" \
    --dry-run=client -o yaml | $KUBECTL apply -f - || die "Не удалось применить pull-secret."
  success "Pull-secret $secret_name применён (пользователь: $REGISTRY_PULL_USER)."
}

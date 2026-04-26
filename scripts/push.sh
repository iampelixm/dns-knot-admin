#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/build.local.conf"

# ── Конфигурация ──────────────────────────────────────────────────────────────

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Ошибка: $CONFIG_FILE не найден."
  echo "Сначала запустите scripts/build.sh — он создаст конфигурацию интерактивно."
  exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

if [[ -z "${REGISTRY:-}" || -z "${REGISTRY_USER:-}" || -z "${REGISTRY_PASSWORD:-}" ]]; then
  echo "Ошибка: в $CONFIG_FILE должны быть заполнены REGISTRY, REGISTRY_USER, REGISTRY_PASSWORD."
  exit 1
fi

# ── Версия ────────────────────────────────────────────────────────────────────

cd "$ROOT_DIR"
VERSION=$(node -p "require('./package.json').version")

IMAGE="$REGISTRY/dnsadmin:$VERSION"
IMAGE_LATEST="$REGISTRY/dnsadmin:latest"

# Проверить, что образ с таким тегом существует локально
if ! docker image inspect "$IMAGE" > /dev/null 2>&1; then
  echo "Образ $IMAGE не найден локально."
  echo "Сначала запустите: scripts/build.sh"
  exit 1
fi

# ── Пуш ──────────────────────────────────────────────────────────────────────

echo "Логин в $REGISTRY..."
echo "$REGISTRY_PASSWORD" | docker login \
  "$(echo "$REGISTRY" | cut -d'/' -f1)" \
  -u "$REGISTRY_USER" \
  --password-stdin

echo ""
echo "Пуш: $IMAGE"
docker push "$IMAGE"

echo "Пуш: $IMAGE_LATEST"
docker push "$IMAGE_LATEST"

docker logout "$(echo "$REGISTRY" | cut -d'/' -f1)"

echo ""
echo "Готово: $IMAGE"

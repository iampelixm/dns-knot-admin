#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/build.local.conf"

# ── Конфигурация ──────────────────────────────────────────────────────────────

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Файл конфигурации не найден: $CONFIG_FILE"
  echo "Создаём конфигурацию (сохранится в $CONFIG_FILE, в git не попадёт)."
  echo ""

  read -rp  "Registry (например registry.example.com/dns-knot): " _reg
  read -rp  "Логин в registry: " _user
  read -rsp "Пароль в registry: " _pass
  echo ""

  cat > "$CONFIG_FILE" <<EOF
REGISTRY=$_reg
REGISTRY_USER=$_user
REGISTRY_PASSWORD=$_pass
EOF
  echo "Конфигурация сохранена."
  echo ""
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

if [[ -z "${REGISTRY:-}" || -z "${REGISTRY_USER:-}" || -z "${REGISTRY_PASSWORD:-}" ]]; then
  echo "Ошибка: в $CONFIG_FILE должны быть заполнены REGISTRY, REGISTRY_USER, REGISTRY_PASSWORD."
  exit 1
fi

# ── Версия ────────────────────────────────────────────────────────────────────

cd "$ROOT_DIR"
CURRENT_VERSION=$(node -p "require('./package.json').version")

echo "Текущая версия: $CURRENT_VERSION"

# Вычислить следующий патч для отображения в подсказке
NEXT_PATCH=$(node -p "
  const [maj, min, pat] = '${CURRENT_VERSION}'.split('.').map(Number);
  \`\${maj}.\${min}.\${pat + 1}\`
")

read -rp "Обновить патч-версию? ($CURRENT_VERSION → $NEXT_PATCH) [y/N]: " _bump

if [[ "${_bump,,}" == "y" ]]; then
  VERSION=$(node -e "
    const fs = require('fs');
    const pkg = JSON.parse(fs.readFileSync('package.json'));
    const [maj, min, pat] = pkg.version.split('.').map(Number);
    pkg.version = \`\${maj}.\${min}.\${pat + 1}\`;
    fs.writeFileSync('package.json', JSON.stringify(pkg, null, 2) + '\n');
    process.stdout.write(pkg.version);
  ")
  echo "Версия обновлена: $CURRENT_VERSION → $VERSION"
else
  VERSION="$CURRENT_VERSION"
  echo "Версия не изменена: $VERSION"
fi

# ── Сборка ────────────────────────────────────────────────────────────────────

IMAGE="$REGISTRY/dnsadmin:$VERSION"
IMAGE_LATEST="$REGISTRY/dnsadmin:latest"

echo ""
echo "Сборка образа:"
echo "  $IMAGE"
echo "  $IMAGE_LATEST"
echo ""

docker build \
  -t "$IMAGE" \
  -t "$IMAGE_LATEST" \
  "$ROOT_DIR"

echo ""
echo "Готово: $IMAGE"
echo "Чтобы отправить в registry, запустите: scripts/push.sh"

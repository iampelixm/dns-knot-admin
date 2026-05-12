#!/usr/bin/env bash
# clean_registry.sh — удаляет старые версионные теги образа dnsadmin из реестра.
# Использует Docker Registry v2 API (basic auth).
#
# Использование:
#   scripts/clean_registry.sh              # оставить последние 5 версий
#   scripts/clean_registry.sh --keep 3    # оставить последние 3 версии
#   scripts/clean_registry.sh --dry-run   # показать, что будет удалено, без удаления
#   scripts/clean_registry.sh --debug     # вывести сырые заголовки ответа (диагностика)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/build.local.conf"

# ── Аргументы ─────────────────────────────────────────────────────────────────

KEEP=5
DRY_RUN=false
DEBUG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep)    KEEP="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --debug)   DEBUG=true; shift ;;
    *) echo "Неизвестный аргумент: $1"; echo "Использование: $0 [--keep N] [--dry-run] [--debug]"; exit 1 ;;
  esac
done

# ── Конфигурация ──────────────────────────────────────────────────────────────

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Ошибка: $CONFIG_FILE не найден. Запустите scripts/build.sh."
  exit 1
fi

# shellcheck source=/dev/null
source "$CONFIG_FILE"

if [[ -z "${REGISTRY:-}" || -z "${REGISTRY_USER:-}" || -z "${REGISTRY_PASSWORD:-}" ]]; then
  echo "Ошибка: REGISTRY, REGISTRY_USER, REGISTRY_PASSWORD должны быть заполнены в $CONFIG_FILE"
  exit 1
fi

# Разбить REGISTRY на хост и путь: "registry.example.com/dns-knot" → host + path
REGISTRY_HOST="$(echo "$REGISTRY" | cut -d'/' -f1)"
REGISTRY_PREFIX="$(echo "$REGISTRY" | cut -d'/' -f2-)"
REPO="$REGISTRY_PREFIX/dnsadmin"
API_BASE="https://$REGISTRY_HOST/v2/$REPO"
CREDS="$REGISTRY_USER:$REGISTRY_PASSWORD"

# ── Вспомогательные функции ───────────────────────────────────────────────────

# Запрос к Registry API с basic auth
registry_curl() {
  curl --silent --show-error --location \
    --user "$CREDS" \
    ${REGISTRY_INSECURE:+--insecure} \
    "$@"
}

# Получить список всех тегов (возвращает строки через \n)
list_tags() {
  local url="$API_BASE/tags/list"
  local result
  result=$(registry_curl --fail "$url" 2>&1) || {
    echo "Ошибка при получении тегов из $url:" >&2
    echo "$result" >&2
    exit 1
  }
  echo "$result" | python3 -c "import sys,json; tags=json.load(sys.stdin).get('tags') or []; print('\n'.join(tags))"
}

# Получить Docker-Content-Digest для тега.
# Используем GET + --dump-header вместо HEAD: ряд реестров не включает
# Docker-Content-Digest в ответ на HEAD-запрос.
get_digest() {
  local tag="$1"
  local headers body tmp_body
  tmp_body=$(mktemp)
  # Принимаем оба типа манифеста — Docker v2 и OCI.
  headers=$(registry_curl \
    --dump-header - \
    --output "$tmp_body" \
    --header "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    --header "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
    --header "Accept: application/vnd.oci.image.manifest.v1+json" \
    --header "Accept: application/vnd.oci.image.index.v1+json" \
    "$API_BASE/manifests/$tag" 2>&1)
  body=$(cat "$tmp_body"); rm -f "$tmp_body"

  if $DEBUG; then
    echo "" >&2
    echo "  [debug] заголовки ответа для тега '$tag':" >&2
    echo "$headers" | sed 's/^/    /' >&2
    if [[ -n "$body" ]]; then
      echo "  [debug] тело ответа:" >&2
      echo "$body" | sed 's/^/    /' >&2
    fi
  fi

  echo "$headers" \
    | grep -i "^docker-content-digest:" \
    | tr -d '\r' \
    | awk '{print $2}' \
    | head -1
}

# Удалить манифест по digest
delete_manifest() {
  local digest="$1"
  local http_code
  http_code=$(registry_curl --output /dev/null --write-out "%{http_code}" \
    --request DELETE \
    "$API_BASE/manifests/$digest")
  echo "$http_code"
}

# ── Основная логика ───────────────────────────────────────────────────────────

echo "Registry:     $REGISTRY_HOST"
echo "Репозиторий:  $REPO"
echo "Оставить:     последние $KEEP версионных тегов + latest"
$DRY_RUN  && echo "Режим:        --dry-run (ничего удалено не будет)"
$DEBUG    && echo "Режим:        --debug (выводятся сырые заголовки)"
echo ""

ALL_TAGS=$(list_tags)

if [[ -z "$ALL_TAGS" ]]; then
  echo "Теги не найдены — репозиторий пуст."
  exit 0
fi

# Отфильтровать только семантические версии (X.Y.Z), отсортировать по semver
VERSION_TAGS=$(echo "$ALL_TAGS" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+([.-].+)?$' | sort -V)
OTHER_TAGS=$(echo "$ALL_TAGS"   | grep -vE '^[0-9]+\.[0-9]+\.[0-9]+([.-].+)?$' | sort)

TOTAL=$(echo "$VERSION_TAGS" | grep -c . || true)

echo "Найдено версионных тегов: $TOTAL"
if [[ -n "$OTHER_TAGS" ]]; then
  echo "Прочие теги (не удаляются): $(echo "$OTHER_TAGS" | tr '\n' ' ')"
fi
echo ""

if [[ "$TOTAL" -le "$KEEP" ]]; then
  echo "Удаление не требуется (тегов: $TOTAL, порог: $KEEP)."
  exit 0
fi

DELETE_COUNT=$(( TOTAL - KEEP ))
TAGS_TO_DELETE=$(echo "$VERSION_TAGS" | head -n "$DELETE_COUNT")
TAGS_TO_KEEP=$(echo "$VERSION_TAGS"   | tail -n "$KEEP")

echo "Будут СОХРАНЕНЫ:"
echo "$TAGS_TO_KEEP" | sed 's/^/  ✓ /'
echo "  ✓ latest"
echo ""
echo "Будут УДАЛЕНЫ ($DELETE_COUNT шт.):"
echo "$TAGS_TO_DELETE" | sed 's/^/  ✗ /'
echo ""

if $DRY_RUN; then
  echo "Dry-run: реальных удалений нет."
  exit 0
fi

read -rp "Подтвердить удаление? [y/N]: " _confirm
if [[ "${_confirm,,}" != "y" ]]; then
  echo "Отмена."
  exit 0
fi

echo ""

DELETED=0
FAILED=0

while IFS= read -r TAG; do
  [[ -z "$TAG" ]] && continue
  printf "%-30s" "Удаление $TAG ..."

  if $DEBUG; then
    DIGEST=$(get_digest "$TAG" || true)
  else
    DIGEST=$(get_digest "$TAG" 2>/dev/null || true)
  fi

  if [[ -z "$DIGEST" ]]; then
    echo " ПРОПУСК (не удалось получить digest)"
    if ! $DEBUG; then
      echo "  → запустите с --debug чтобы увидеть ответ реестра"
    fi
    FAILED=$(( FAILED + 1 ))
    continue
  fi

  HTTP_CODE=$(delete_manifest "$DIGEST" 2>/dev/null || true)

  case "$HTTP_CODE" in
    202|200)
      echo " OK"
      DELETED=$(( DELETED + 1 ))
      ;;
    405)
      echo " ОШИБКА 405 — удаление отключено в реестре"
      echo "  Включите: storage.delete.enabled: true (Docker Registry v2) или аналог в вашем реестре."
      FAILED=$(( FAILED + 1 ))
      ;;
    *)
      echo " ОШИБКА HTTP $HTTP_CODE (digest: $DIGEST)"
      FAILED=$(( FAILED + 1 ))
      ;;
  esac
done <<< "$TAGS_TO_DELETE"

echo ""
echo "Удалено: $DELETED  /  Ошибок: $FAILED"
[[ "$FAILED" -gt 0 ]] && exit 1 || exit 0

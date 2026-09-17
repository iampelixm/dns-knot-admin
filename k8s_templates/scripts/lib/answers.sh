#!/usr/bin/env bash
# Работа с answers-файлами: загрузка, сохранение, вопросы с дефолтом.
# Источник: source lib/answers.sh
set -euo pipefail

ANSWERS_DIR="${ANSWERS_DIR:-$(cd "$SCRIPT_DIR/../answers" && pwd)}"
ANSWERS_FILE=""

# Загрузка answers-файла (если существует) — source.
answers_load() {
  local target="${1:?target}"
  ANSWERS_FILE="$ANSWERS_DIR/$target.env"
  if [[ -f "$ANSWERS_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$ANSWERS_FILE"
    info "Загружены ответы из $ANSWERS_FILE"
  else
    warn "Файл ответов $ANSWERS_FILE не найден — вопросы будут без дефолтов."
  fi
}

# Обновляет/добавляет KEY=VALUE в answers-файл. Создаёт файл при необходимости.
# Вызывается после каждого ответа (или пачкой через answers_save_many).
answers_save() {
  local key="$1" value="$2"
  [[ -z "$ANSWERS_FILE" ]] && return 0
  mkdir -p "$(dirname "$ANSWERS_FILE")"
  # убираем старый ключ, дописываем новый
  if [[ -f "$ANSWERS_FILE" ]]; then
    grep -v "^${key}=" "$ANSWERS_FILE" > "$ANSWERS_FILE.tmp" || true
    mv "$ANSWERS_FILE.tmp" "$ANSWERS_FILE"
  else
    : > "$ANSWERS_FILE"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$ANSWERS_FILE"
}

# Сохраняет несколько ключей: answers_save_many KEY=VALUE KEY=VALUE ...
answers_save_many() {
  local kv
  for kv in "$@"; do
    answers_save "${kv%%=*}" "${kv#*=}"
  done
}

# Вопрос с дефолтом; пишет ответ в файл.
#   answer VAR "prompt" "default" [--no-store] [--required]
answer() {
  local var="$1" prompt="$2" default="${3:-}"
  local store=1 required=0
  [[ "$4" == "--no-store" ]] && store=0
  [[ "$4" == "--required" ]] && required=1
  local cur="${!var:-}"
  [[ -z "$cur" ]] && cur="$default"
  local disp="${cur:-}"
  ask "${prompt}${disp:+ [по умолч: $disp]}: "
  local val
  read -r val
  val="${val:-$cur}"
  if [[ "$required" == "1" && -z "$val" ]]; then
    die "Значение $var обязательно."
  fi
  export "$var=$val"
  [[ "$store" == "1" ]] && answers_save "$var" "$val"
}

# Select из списка; записывает выбранное значение.
#   answer_select VAR "prompt" OPTIONS
#   OPTIONS — строка вида "label1|value1"$'\n'"label2|value2"...
answer_select() {
  local var="$1" prompt="$2" options="$3"
  local cur="${!var:-}" i=0 line label value choices=()
  echo ""
  info "$prompt"
  while IFS=$'\n' read -r line; do
    [[ -z "$line" ]] && continue
    i=$((i + 1))
    label="${line%%|*}"
    value="${line#*|}"
    choices+=("$i")
    echo "  $i) $label"
  done <<< "$options"
  echo ""
  local selected_idx
  ask "Выберите номер (1-$i) [по умолч: ${cur:-1}]: "
  read -r selected_idx
  selected_idx="${selected_idx:-${cur:-1}}"
  i=0
  while IFS=$'\n' read -r line; do
    [[ -z "$line" ]] && continue
    i=$((i + 1))
    [[ "$i" == "$selected_idx" ]] || continue
    value="${line#*|}"
    export "$var=$value"
    answers_save "$var" "$value"
    info "$var = $value"
    return 0
  done <<< "$options"
  die "Неверный выбор: $selected_idx"
}

# Вопрос Да/Нет; записывает yes/no.
answer_yn() {
  local var="$1" prompt="$2" default="${3:-no}"
  local cur="${!var:-}"
  [[ -z "$cur" ]] && cur="$default"
  local yn disp
  disp="$cur"
  ask "${prompt}${disp:+ [$disp]}: "
  read -r yn
  yn="${yn:-$cur}"
  case "${yn,,}" in
    y|yes) yn=yes ;;
    n|no)  yn=no ;;
    *) yn=no ;;
  esac
  export "$var=$yn"
  answers_save "$var" "$yn"
}

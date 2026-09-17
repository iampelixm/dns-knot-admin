#!/usr/bin/env bash
# Работа с kubectl: контекст (выбор существующего или создание нового), ноды.
# Источник: source lib/k8s.sh
set -euo pipefail

KUBECTL="kubectl"

kubectl_set_context() {
  local ctx="$1"
  KUBECTL="kubectl --context=$ctx"
}

kubectl_ctx() { echo "${KUBECTL#*--context=}"; }

select_context() {
  require_cmd kubectl "https://kubernetes.io/docs/tasks/tools/"
  info "Доступные контексты kubectl:"
  local -a contexts
  mapfile -t contexts < <(kubectl config get-contexts -o name 2>/dev/null || true)
  if [[ ${#contexts[@]} -eq 0 ]]; then
    warn "Контекстов нет — создадим новый."
    create_context_interactive
    return
  fi

  local i current_ctx=""
  current_ctx="$(kubectl config current-context 2>/dev/null || true)"
  for i in "${!contexts[@]}"; do
    local marker=""
    [[ "${contexts[$i]}" == "$current_ctx" ]] && marker=" ${GREEN}(текущий)${NC}"
    echo "  $((i+1))) ${contexts[$i]}$marker"
  done
  echo "  $(( ${#contexts[@]}+1 ))) Создать новый контекст"

  ask "Выберите контекст [Enter = текущий '$current_ctx']: "
  read -r choice
  if [[ -z "$choice" ]]; then
    [[ -z "$current_ctx" ]] && die "Нет текущего контекста."
    kubectl_set_context "$current_ctx"
    return
  fi
  local idx=$((choice))
  if [[ $idx -eq $(( ${#contexts[@]}+1 )) ]]; then
    create_context_interactive
  elif [[ $idx -ge 1 && $idx -le ${#contexts[@]} ]]; then
    kubectl_set_context "${contexts[$((idx-1))]}"
  else
    die "Неверный выбор контекста."
  fi
}

create_context_interactive() {
  ask "Имя нового контекста: "
  read -r new_ctx_name
  [[ -z "$new_ctx_name" ]] && die "Имя контекста не может быть пустым."
  ask "URL API-сервера (например https://192.168.1.242:6443): "
  read -r server_url
  [[ -z "$server_url" ]] && die "URL сервера обязателен."
  ask "Путь к файлу kubeconfig (если есть), Enter — ввести данные вручную: "
  read -r kc_path

  if [[ -n "$kc_path" ]]; then
    [[ -f "$kc_path" ]] || die "Файл не найден: $kc_path"
    kubectl config set-cluster "$new_ctx_name" --kubeconfig="$kc_path" >/dev/null || true
    kubectl config set-context "$new_ctx_name" --kubeconfig="$kc_path" >/dev/null || true
    kubectl_set_context "$new_ctx_name"
    info "Контекст создан из файла: $kc_path"
    return
  fi

  ask "Путь к CA-сертификату (или пусто для insecure-skip-tls-verify): "
  read -r ca_path
  ask "Токен/данные пользователя (Bearer-токен, или путь к kubeconfig): "
  read -r token

  if [[ -n "$ca_path" && -f "$ca_path" ]]; then
    kubectl config set-cluster "$new_ctx_name" --server="$server_url" --certificate-authority="$ca_path" >/dev/null
  else
    kubectl config set-cluster "$new_ctx_name" --server="$server_url" --insecure-skip-tls-verify=true >/dev/null
  fi
  kubectl config set-credentials "$new_ctx_name" --token="$token" >/dev/null 2>&1 || \
    kubectl config set-credentials "$new_ctx_name" --username="" >/dev/null 2>&1 || true
  kubectl config set-context "$new_ctx_name" --cluster="$new_ctx_name" --user="$new_ctx_name" >/dev/null
  kubectl_set_context "$new_ctx_name"
  info "Контекст '$new_ctx_name' создан."
}

verify_context_access() {
  info "Проверяю доступ к кластеру ($(kubectl_ctx))..."
  if ! $KUBECTL cluster-info >/dev/null 2>&1; then
    warn "Не удалось получить cluster-info. Проверьте контекст вручную."
  fi
  if ! $KUBECTL auth can-i get nodes >/dev/null 2>&1; then
    die "У текущего пользователя нет прав на чтение нод. Скрипту нужен доступ к 'get nodes'."
  fi
  success "Доступ к кластеру подтверждён."
}

node_external_ip() {
  local node="$1"
  local ip
  ip="$($KUBECTL get node "$node" -o jsonpath='{.status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null || true)"
  [[ -z "$ip" ]] && ip="$($KUBECTL get node "$node" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
  echo "$ip"
}

node_ready() {
  local node="$1"
  $KUBECTL get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo Unknown
}

list_nodes() {
  info "Ноды кластера:"
  echo ""
  $KUBECTL get nodes -o custom-columns='NODE:.metadata.name,STATUS:.status.conditions[-1].type,INTERNAL:.status.addresses[?(@.type=="InternalIP")].address,EXTERNAL:.status.addresses[?(@.type=="ExternalIP")].address,LABELS:.metadata.labels' 2>/dev/null || die "Не удалось получить список нод."
}

pick_node() {
  local prompt="${1:-Выберите ноду [номер]}" require_ready="${2:-1}"
  local -a nodes
  mapfile -t nodes < <($KUBECTL get nodes -o name 2>/dev/null | sed 's|^node/||')
  [[ ${#nodes[@]} -eq 0 ]] && die "Нет нод в кластере."

  local i
  for i in "${!nodes[@]}"; do
    local n="${nodes[$i]}" st=""
    st="$(node_ready "$n")"
    local ip=""
    ip="$(node_external_ip "$n")"
    local warn_marker=""
    [[ "$st" != "True" ]] && warn_marker=" ${YELLOW}(NotReady)${NC}"
    echo "  $((i+1))) $n [$st, $ip]$warn_marker"
  done
  echo ""
  ask "$prompt: "
  read -r choice
  local idx=$((choice-1))
  [[ $idx -ge 0 && $idx -lt ${#nodes[@]} ]] || die "Неверный выбор ноды."
  local node="${nodes[$idx]}"
  local st=""
  st="$(node_ready "$node")"
  if [[ "$require_ready" == "1" && "$st" != "True" ]]; then
    warn "Нода $node не готова (status: $st)."
    confirm "Продолжить с неготовой нодой?" || die "Отменено."
  fi
  echo "$node"
}

pick_nodes_multi() {
  local prompt="${1:-Выберите ноды (номера через пробел, Enter = пропустить)}"
  local -a nodes
  mapfile -t nodes < <($KUBECTL get nodes -o name 2>/dev/null | sed 's|^node/||')
  [[ ${#nodes[@]} -eq 0 ]] && die "Нет нод в кластере."

  local i
  for i in "${!nodes[@]}"; do
    local n="${nodes[$i]}" st="" ip=""
    st="$(node_ready "$n")"
    ip="$(node_external_ip "$n")"
    local warn_marker=""
    [[ "$st" != "True" ]] && warn_marker=" ${YELLOW}(NotReady)${NC}"
    echo "  $((i+1))) $n [$st, $ip]$warn_marker"
  done
  echo ""
  ask "$prompt: "
  read -r choice_line
  [[ -z "$choice_line" ]] && return 0
  local selected=()
  local c
  for c in $choice_line; do
    local idx=$((c-1))
    if [[ $idx -ge 0 && $idx -lt ${#nodes[@]} ]]; then
      selected+=("${nodes[$idx]}")
    else
      warn "Пропускаю неверный номер: $c"
    fi
  done
  echo "${selected[*]}"
}

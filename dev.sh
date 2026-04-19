#!/usr/bin/env bash
# Локальная разработка: Vite в Docker (docker-compose.yml), API — uvicorn на хосте :8080 (см. README.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  cat <<EOF
Использование: $(basename "$0") <команда>

  start       поднять Vite (docker compose up -d), порт 5173
  start-fg    то же, в foreground (логи в терминале, Ctrl+C — стоп)
  stop        остановить и убрать контейнер compose
  restart     stop + start
  status      docker compose ps
  logs        docker compose logs -f (опционально имя сервиса, по умолчанию dev)

API: в другом терминале из backend/ запустите uvicorn на 8080 (см. README.md).
EOF
}

cmd="${1:-start}"

case "$cmd" in
  -h|--help|help)
    usage
    exit 0
    ;;
  start)
    docker compose up -d
    echo "Откройте http://localhost:5173 — /api проксируется на 127.0.0.1:8080 (запустите uvicorn в backend/)."
    ;;
  start-fg)
    docker compose up
    ;;
  stop)
    docker compose down
    ;;
  restart)
    docker compose down
    docker compose up -d
    echo "http://localhost:5173"
    ;;
  status)
    docker compose ps
    ;;
  logs)
    docker compose logs -f "${2:-dev}"
    ;;
  *)
    echo "Неизвестная команда: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac

# DNS Agent

Go-агент, работающий на каждой DNS-ноде (DaemonSet). Принимает конфигурацию Knot от dnsadmin и применяет её.

## Архитектура

```
┌─────────────┐     POST /apply      ┌──────────────────┐
│  dnsadmin    │  X-API-Key: ... ──▶ │  dns-agent       │
│  (FastAPI)   │                     │  (Go, port 8080) │
└─────────────┘                     └──────┬───────────┘
                                           │
                              ┌────────────┴────────────┐
                              │ пишет knot.conf на ноде │
                              │ knotc conf-check        │
                              │ knotc reload            │
                              └─────────────────────────┘
```

## Эндпоинты

| Метод | Путь      | Аутентификация | Описание                        |
|-------|-----------|----------------|---------------------------------|
| GET   | `/health` | нет            | Liveness/readiness probe        |
| GET   | `/status` | X-API-Key      | Версия, uptime, alive           |
| POST  | `/apply`  | X-API-Key      | Применить конфиг → reload       |
| POST  | `/reload` | X-API-Key      | Только reload (query: cmd)      |

## Аутентификация

Один общий симметричный ключ (`AGENT_API_KEY`).

**Где задаётся:**

- **k8s-templates/answers/prod.env** — `AGENT_API_KEY="dns-agent-..."`
- При рендере манифестов ключ попадает в **K8s Secret** `dns-agent-key`
- DaemonSet монтирует Secret как env var `AGENT_API_KEY`
- Агент читает `AGENT_API_KEY` и требует его в заголовке `X-API-Key`

**Как бэкенд передаёт ключ агенту:**

Ключ не хранится в коде бэкенда. Администратор вписывает его в `delivery_config.api_key` при настройке сервера в UI. Когда бэкенд вызывает `POST /apply`, он читает `api_key` из `delivery_config` сервера и отправляет в `X-API-Key`.

## Как настроить сервер в UI

1. Открыть **DNS-серверы** → создать/редактировать сервер
2. **Способ доставки** → `Через агента`
3. **delivery_config:**
```json
{
  "agent_url": "http://<node-ip>:8080",
  "api_key": "<тот же ключ, что в prod.env>",
  "config_path": "/etc/knot/knot.conf",
  "reload_cmd": "knotc reload"
}
```
4. Сохранить → нажать **Доставить**

## Сборка

```bash
docker build -t registry.summer-site.ru/dns-agent:latest -f agent/Dockerfile agent/
docker push registry.summer-site.ru/dns-agent:latest
```

Автоматически собирается в `scripts/build.sh` если существует `agent/Dockerfile`.

## Деплой

Манифесты рендерятся в `k8s_templates/out/prod/dns-agent-daemonset.yaml`.

```bash
kubectl --context=dns -n dns-knot apply -f k8s_templates/out/prod/dns-agent-daemonset.yaml
```

Проверка:
```bash
kubectl --context=dns -n dns-knot rollout status daemonset/dns-agent
```

## Локальный запуск (для теста)

```bash
AGENT_API_KEY=test-key AGENT_PORT=8080 go run ./agent/
```

Или через docker-compose:
```bash
AGENT_API_KEY=test-key docker compose up agent
```
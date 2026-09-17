# dnsadmin API

dnsadmin — FastAPI-приложение для управления зонами Knot DNS через Kubernetes ConfigMap.  
Доступен Web UI (SPA) и REST API.

## Базовый URL

```
http://dnsadmin.k3s.local
```

Внутри кластера — `http://dnsadmin.dns-knot.svc:80`.

## Автодокументация (Swagger / ReDoc)

С версии 0.4.11 включена автоматическая документация FastAPI:

| Интерфейс | URL |
|-----------|-----|
| Swagger UI | `/docs` |
| ReDoc | `/redoc` |
| OpenAPI JSON | `/openapi.json` |

Все эндпоинты `/api/*` защищены JWT — используйте кнопку **Authorize** в Swagger
для ввода токена (после `POST /api/auth/login`).

---

## Инстансы

### `GET /api/instances`

Список Knot-инстансов из переменной `KNOT_INSTANCES`.

**Response:**
```json
{
  "instances": [
    {"id": "ns",  "label": "ns (primary)",          "ip": "37.230.115.233", "role": "primary",   "configmap": "knot-config",     "deployment": "knot"},
    {"id": "ns1", "label": "ns1 (secondary)",        "ip": "176.53.173.136", "role": "secondary", "configmap": "knot-config-ns1", "deployment": "knot-ns1"},
    {"id": "ns2", "label": "ns2 (secondary, NAT)",   "ip": "77.106.252.16",  "role": "secondary", "configmap": "knot-config-ns2", "deployment": "knot-ns2"}
  ]
}
```

Параметр `?instance=ns1` доступен в части эндпоинтов (knot-conf, zones), чтобы работать с конкретным инстансом.

---

## Диагностика

### `GET /api/dns-health`

Проверка ответа Knot по UDP (SOA-запрос).

**Response:**
```json
{
  "ok": true,
  "message": "SOA для k3s.local (через 37.230.115.233)",
  "latency_ms": 1.23,
  "probe_host": "37.230.115.233",
  "probe_source": "knot.conf.listen",
  "probe_port": 53
}
```

---

## Конфигурация Knot (knot.conf)

### `GET /api/knot-conf`

Получить текущий knot.conf.

**Response:**
```json
{
  "raw": "server:\n  listen: ...",
  "schema_version": "1"
}
```

### `GET /api/knot-conf/schema`

JSON Schema для knot.conf.

### `GET /api/knot-conf/model`

Разобранный knot.conf в виде структурированной модели.

**Response:** `KnotEditorModel` (см. секцию Модели)

### `POST /api/knot-conf/validate`

Валидация knot.conf без сохранения.

**Body:**
```json
{
  "content": "server:\n  listen: ...",
  "axfr_override": null
}
```

**Response:**
```json
{
  "ok": true,
  "yaml_ok": true,
  "yaml_error": null,
  "knotc": {
    "ran": true,
    "ok": true,
    "message": "Configuration is valid"
  },
  "axfr": {
    "config_includes_knot_path": true,
    "source": "secret",
    "cluster": { ... },
    "hints": []
  }
}
```

### `POST /api/knot-conf/render-model`

Собрать knot.conf из JSON-модели без сохранения (предпросмотр).

**Body:** `KnotEditorModel`

**Response:**
```json
{ "content": "server:\n  ..." }
```

### `PUT /api/knot-conf`

Сохранить knot.conf (raw text) и перезапустить Knot.

**Body:**
```json
{ "content": "server:\n  listen: ..." }
```

**Response:**
```json
{
  "status": "ok",
  "restarted_at": "2026-06-03T11:38:17Z",
  "validation": { ... }
}
```

### `PUT /api/knot-conf/model`

Сохранить knot.conf через модель и перезапустить.

**Body:** `KnotEditorModel`

**Response:** тот же статус.

---

## AXFR (TSIG-ключи для трансфера зон)

### `GET /api/knot-conf/axfr`

Получить текущий YAML-фрагмент AXFR из Secret.

**Response:**
```json
{
  "content": "key:\n  ...",
  "structured": { "keys": [...], "acls": [...] },
  "structured_parse_warning": null
}
```

### `PUT /api/knot-conf/axfr`

Сохранить AXFR-фрагмент (YAML или structured) и перезапустить.

**Body (YAML):**
```json
{ "content": "key:\n  id: ..." }
```

**Body (structured):**
```json
{
  "structured": {
    "keys": [{ "id": "secondary-01", "algorithm": "hmac-sha256", "secret": "base64..." }],
    "acls": [{ "id": "axfr-allowed", "action": "transfer", "address": ["192.168.1.0/24"], "key": "secondary-01" }]
  }
}
```

**Response:**
```json
{
  "status": "ok",
  "restarted_at": "...",
  "knotc": { "ran": true, "ok": true, "message": "..." }
}
```

### `POST /api/knot-conf/axfr/generate-tsig`

Сгенерировать YAML-фрагмент TSIG через `keymgr -t`.

**Body:**
```json
{
  "key_id": "axfr-mykey",
  "with_acl": true,
  "acl_id": "axfr-allowed"
}
```

### `POST /api/knot-conf/axfr/parse-fragment`

Разобрать YAML-фрагмент AXFR в structured.

**Body:**
```json
{ "content": "key:\n  id: ..." }
```

**Response:**
```json
{
  "structured": { ... },
  "structured_parse_warning": null
}
```

### `POST /api/knot-conf/axfr/render-model`

Собрать YAML-фрагмент из structured-модели.

**Body:** `AxfrFragmentModel`

**Response:**
```json
{ "content": "key:\n  ..." }
```

### `GET /api/knot-conf/axfr-status`

Диагностика Secret AXFR без содержимого.

---

## Зоны

### `GET /api/zones`

Список зон.

**Response:**
```json
{
  "zones": [
    { "name": "k3s.local", "dnssec_signing": true },
    { "name": "summersite.ru", "dnssec_signing": true },
    { "name": "hoteldev.ru", "dnssec_signing": true },
    { "name": "traveldev.ru", "dnssec_signing": true }
  ]
}
```

### `GET /api/zones/{zone_name}`

Получить zone-файл.

**Response:**
```json
{
  "zone": "traveldev.ru",
  "content": "$ORIGIN traveldev.ru.\n$TTL 3600\n..."
}
```

### `PUT /api/zones/{zone_name}`

Сохранить zone-файл (весь текст). Serial bump — автоматически.

**Body:**
```json
{ "content": "$ORIGIN traveldev.ru.\n$TTL 3600\n..." }
```

**Response:**
```json
{
  "status": "ok",
  "restarted_at": "2026-06-03T11:39:10Z",
  "notify_sent": "true"
}
```

### `POST /api/zones/{zone_name}/validate`

Проверить zone-файл без сохранения.

**Body:**
```json
{ "content": "$ORIGIN ..." }
```

**Response:**
```json
{
  "valid": true,
  "errors": []
}
```

### `POST /api/zones/{zone_name}/parse-form`

Разобрать zone-файл в форму редактора.

**Response:**
```json
{
  "form": {
    "soa": {
      "ttl": 3600,
      "primary_ns": "ns.traveldev.ru.",
      "admin_email": "admin.traveldev.ru.",
      "serial": 2026060301,
      "refresh": 3600,
      "retry": 600,
      "expire": 1209600,
      "minimum": 3600
    },
    "ns": [{ "host": "ns.traveldev.ru." }, { "host": "ns1.traveldev.ru." }, { "host": "ns2.traveldev.ru." }],
    "records": [
      { "name": "@", "rtype": "A", "value": "80.87.198.162" },
      { "name": "broker", "rtype": "A", "value": "37.230.115.233" },
      ...
    ]
  }
}
```

### `POST /api/zones/{zone_name}/render-form`

Собрать zone-файл из формы.

**Body:** `ZoneEditorFormModel`

**Response:**
```json
{ "content": "$ORIGIN traveldev.ru.\n..." }
```

### `PUT /api/zones/{zone_name}/form`

Сохранить зону через форму редактора.

### `POST /api/zones/{zone_name}/upsert-record`

Добавить или обновить одну запись.

**Body:**
```json
{
  "name": "broker",
  "rtype": "A",
  "value": "37.230.115.233",
  "ttl": null
}
```

Автоматический serial bump + restart + notify + синхронизация secondary ConfigMap (для новых зон).

### `PATCH /api/zones/{zone_name}/dnssec`

Включить/выключить DNSSEC для зоны.

**Body:**
```json
{ "signing": true }
```

### `GET /api/zones/{zone_name}/dnssec-ds`

Получить DS/DNSKEY записи (если DNSSEC включён).

### `GET /api/zones/{zone_name}/fqdns`

Список FQDN из зоны (A / AAAA / CNAME).

### `GET /api/zones/sync-status`

Сверка SOA serial по всем зонам на всех инстансах (требует KNOT_INSTANCES).

**Response:**
```json
{
  "instances": [...],
  "zones": [
    {
      "zone": "summersite.ru",
      "servers": [
        { "id": "ns", "serial": 2026091701, "synced": true },
        { "id": "ns2", "serial": 2026091701, "synced": true }
      ]
    }
  ]
}
```

### `POST /api/zones/{zone_name}/sync`

Принудительная синхронизация зоны на все secondary инстансы.
Добавляет блок зоны в knot.conf secondary ConfigMap-ов, если её там нет,
и перезапускает соответствующие Knot-деплойменты.

**Response:**
```json
{
  "zone": "summer-site.ru",
  "synced_secondaries": ["ns1", "ns2"],
  "skipped": ["ns(primary)"]
}
```

---

## Синхронизация инстансов

При добавлении новой зоны через API (PUT /api/zones/{zone} или form)
бэкенд автоматически:

1. Добавляет zone-файл и блок `- domain:` в **primary** ConfigMap (`knot-config`)
2. Рестартует primary Knot
3. Добавляет блок `- domain:` в **secondary** ConfigMap-ы (`knot-config-ns1`, `knot-config-ns2`)
4. Рестартует каждый secondary Knot
5. Отправляет `knotc zone-notify`

Текущий статус синхронизации — `GET /api/zones/sync-status`.

---

## Ingress Wizard (бонус)

### `GET /api/k8s/namespaces`
### `GET /api/k8s/services?namespace=<ns>`
### `GET /api/k8s/ingresses[?namespace=<ns>]`
### `POST /api/k8s/ingress/render`

Генерация YAML-манифеста Ingress.

---

## Модели

### ZoneEditorFormModel

```json
{
  "soa": {
    "ttl": 3600,
    "primary_ns": "ns.traveldev.ru.",
    "admin_email": "admin.traveldev.ru.",
    "serial": 2026060301,
    "refresh": 3600,
    "retry": 600,
    "expire": 1209600,
    "minimum": 3600
  },
  "ns": [{ "host": "ns.traveldev.ru." }],
  "records": [
    { "name": "@", "ttl": null, "rtype": "A", "value": "80.87.198.162" }
  ]
}
```

### KnotEditorModel

```json
{
  "server": {
    "listen": "37.230.115.233@53",
    "identity": "ns.summersite.ru",
    "nsid": "ns.summersite.ru"
  },
  "log": [
    { "target": "stdout", "any": "info" }
  ],
  "database": { "storage": "/var/lib/knot" },
  "include": "/etc/knot/conf.d/axfr.conf",
  "zones": [
    {
      "domain": "traveldev.ru",
      "file": "/zones/traveldev.ru.zone",
      "acl": ["axfr-allowed"],
      "notify": ["ns1-remote", "ns2-remote"],
      "dnssec_signing": "on"
    }
  ]
}
```

### AxfrFragmentModel

```json
{
  "keys": [
    { "id": "secondary-01", "algorithm": "hmac-sha256", "secret": "<base64>" }
  ],
  "acls": [
    { "id": "axfr-allowed", "action": "transfer", "address": ["192.168.1.0/24"], "key": "secondary-01" }
  ]
}
```

---

## cert-manager Webhook (DNS-01)

Эндпоинты для cert-manager webhook DNS-01-провайдера.
Доступны без JWT (только внутри кластера, через APIService).

| Метод | Путь | Описание |
|-------|------|---------|
| GET | `/{groupName}/healthz` | Health check |
| POST | `/{groupName}/present` | Создать `_acme-challenge` TXT-запись |
| POST | `/{groupName}/cleanup` | Удалить `_acme-challenge` TXT-запись |
| GET | `/{groupName}/v1/healthz` | APIService-путь (с версией) |
| POST | `/{groupName}/v1/present` | APIService-путь |
| POST | `/{groupName}/v1/cleanup` | APIService-путь |

`groupName` по умолчанию: `dnsadmin.knot.io` (задаётся `WEBHOOK_GROUP_NAME`).

### WebhookChallengeRequest

```json
{
  "dnsName": "_acme-challenge.example.com",
  "key": "abc123...",
  "zone": "example.com",
  "type": "TXT"
}
```

### Ответ

```json
{ "status": "ok" }
```

### Схема работы

1. cert-manager → APIService `v1.dnsadmin.knot.io` → `dnsadmin:8443`
2. dnsadmin добавляет TXT-запись `_acme-challenge.{zone}` в Knot-зону через ConfigMap
3. После подтверждения — удаляет запись

---

## Доступ

Сейчас висит на `dnsadmin.k3s.local` (только внутри кластера, entrypoint `web`).  
Для доступа снаружи нужно либо:

1. **Пробросить порт**: `kubectl --context summersite port-forward -n dns-knot svc/dnsadmin 8080:80`
2. **Сделать Ingress** на поддомен вроде `dnsadmin.hoteldev.ru` или `dnsadmin.dev.summersite.ru`

---

## Секреты

- `JWT_SECRET` — в Secret `dnsadmin-auth`
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — там же
- AXFR TSIG — в Secret `knot-axfr`, ключ `axfr.conf`

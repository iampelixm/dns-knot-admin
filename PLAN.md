# План реализации: Zone Versioning + TSIG Management + App-First Architecture

## Архитектурные решения (проверенные)

- **Source of truth**: SQLite (PVC). K8s ConfigMap — только рендер-таргет.
- **Проверка изменений**: через `sha256(content) == last_version.checksum`, а не по serial.
- **Serial**: используется только для SOA записи и быстрой проверки poller'ом (UDP cheaper чем fetch).
- **AXFR secondary**: один общий ключ `axfr-secondary` для всех slave.
- **Per-zone ACL**: `tsig_allowed_keys` — JSON-массив key_id из `tsig_keys`. Scope ключа определяет action:
  - `axfr` → `action: transfer` в knot.conf
  - `dns_update` → `action: update` в knot.conf
- **Version creation**: только после успешного push на сервер.
- **DNS-01 cert-manager**: НЕ создаёт версии (`version_source=None` в `_apply_zone_update`).
- **Initial backup**: при старте с пустой БД — всё из ConfigMap + Secret → SQLite, source=`initial`.
- **Poller**: раз в час (3600 сек, настраивается через `TSIG_POLL_INTERVAL`).
- **Лимит**: 100 последних версий на зону.
- **Шифрование**: `MASTER_ENCRYPTION_KEY` в ENV → Fernet → `secret_encrypted` в `tsig_keys`.

---

## Этап 1: Backend-инфраструктура

### Файлы

| Файл | Действие |
|------|----------|
| `backend/app/db.py` | **Создать** — SQLite init, migrate(), таблицы |
| `backend/app/crypto_utils.py` | **Создать** — Fernet encrypt/decrypt |
| `backend/app/settings.py` | **Создать** — Pydantic Settings (все ENV) |
| `backend/app/main.py` | **Изменить** — import settings, startup → migrate(), backup |
| `backend/requirements.txt` | **Изменить** — +cryptography, +pydantic-settings |
| `Dockerfile` | **Изменить** — mkdir -p /data |
| `k8s/95-dnsadmin-pvc.yaml` | **Создать** — PVC для /data |
| `k8s/80-dnsadmin-deployment.example.yaml` | **Изменить** — +volumeMounts, +MASTER_ENCRYPTION_KEY |

### SQLite схема (таблицы)

```sql
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zones (
    zone_name TEXT PRIMARY KEY,
    serial INTEGER NOT NULL DEFAULT 0,
    dnssec_signing INTEGER NOT NULL DEFAULT 0,
    tsig_allowed_keys TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zone_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_name TEXT NOT NULL,
    content TEXT NOT NULL,
    serial INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (zone_name) REFERENCES zones(zone_name)
);
CREATE INDEX IF NOT EXISTS idx_zv_zone_ct ON zone_versions(zone_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_zv_ck ON zone_versions(checksum);

CREATE TABLE IF NOT EXISTS tsig_keys (
    key_id TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL DEFAULT 'hmac-sha256',
    secret_encrypted TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT
);
```

### startup_backup()

При первом запуске (проверка: `settings['initial_backup_done']` не существует):

1. `core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)` — читаем ConfigMap
2. Для каждого `*.zone` файла: `INSERT INTO zones` + `INSERT INTO zone_versions(source='initial')` + checksum
3. `INSERT INTO settings (key='knot_conf', value=JSON.stringify(knot.conf))`
4. `read_axfr_secret()` — если есть: парсим YAML → для каждого key → `INSERT INTO tsig_keys(scope='axfr')` + encrypt
5. `INSERT INTO settings (key='initial_backup_done', value='true')`

### _apply_zone_update() — изменение

```python
def _apply_zone_update(zone_name: str, content: str, *, version_source: str | None = "api") -> Dict[str, str]:
    # ... validate, serial bump, ConfigMap, restart, NOTIFY ...

    if version_source is not None:
        serial = extract_serial(content)
        checksum = sha256(content.encode()).hexdigest()
        last = get_latest_version(zone_name)  # последняя версия из БД
        if last is None or last["checksum"] != checksum:
            vid = create_version(zone_name, content, serial, checksum, source=version_source)
            result["version_id"] = vid
    return result
```

### Тесты

- `backend/tests/test_db.py` — migrate(), create/drop tables, startup_backup() с fake ConfigMap
- `backend/tests/test_crypto_utils.py` — encrypt/decrypt roundtrip, missing key error

---

## Этап 2: TSIG Keys API

### Файлы

| Файл | Действие |
|------|----------|
| `backend/app/routers/tsig_keys.py` | **Создать** — роутер /api/tsig-keys |

### Эндпоинты

```
GET    /api/tsig-keys                        — список (без secret!)
POST   /api/tsig-keys                        — создать (возвращаем secret 1 раз)
POST   /api/tsig-keys/{key_id}/revoke        — отозвать (status→revoked)
DELETE /api/tsig-keys/{key_id}               — удалить навсегда
```

Body POST create:
```json
{"name": "gitlab-deploy", "scope": "dns_update", "algorithm": "hmac-sha256"}
```

Response POST create:
```json
{"key_id": "gitlab-deploy", "algorithm": "hmac-sha256", "scope": "dns_update",
 "secret": "base64...", "status": "active", "created_at": "..."}
```

Генерация secret: `os.urandom(32)` → base64, потом encrypt и в БД. Просто. Без keymgr — keymgr только для Knot-совместимого отображения, нам base32/64-ключ подходит для любого сервера.

### Тесты

- `backend/tests/test_tsig_keys.py` — create, list, revoke, delete, encrypt check

---

## Этап 3: Zone Versioning

### Файлы

| Файл | Действие |
|------|----------|
| `backend/app/zone_versioning.py` | **Создать** — CRUD zone_versions |
| `backend/app/main.py` | **Изменить** — эндпоинты версий |

### zone_versioning.py — функции

```python
def create_version(zone_name, content, serial, checksum, source) -> int
def get_versions(zone_name, limit=100) -> list[dict]         # без content
def get_version_content(vid) -> str | None
def delete_version(vid) -> bool
def restore_version(zone_name, vid) -> str                   # content для push
def get_latest_version(zone_name) -> dict | None             # последняя по created_at
def enforce_version_limit(zone_name, limit=100)              # DELETE oldest if > 100
```

### Эндпоинты (в main.py)

```
GET    /api/zones/{zone}/versions                 — список версий
GET    /api/zones/{zone}/versions/{vid}            — содержимое версии
DELETE /api/zones/{zone}/versions/{vid}            — удалить
GET    /api/zones/{zone}/versions/{vid}/export     — скачать zone-файл
POST   /api/zones/{zone}/versions/{vid}/restore    — восстановить
POST   /api/zones/{zone}/versions/import           — загрузить zone-файл (multipart)
```

### Интеграция

- В `_apply_zone_update()`: вызов `create_version()` после успешного push (с `version_source` параметром)
- DNS-01 (`_webhook_present`, `_webhook_cleanup`): `version_source=None`
- Upsert-record: `version_source="api"`
- Restore: `version_source="restore"`
- Import: `version_source="import"`

### Лимит 100

Вызывается в конце `create_version()`:
```sql
DELETE FROM zone_versions WHERE zone_name = ? AND id NOT IN (
    SELECT id FROM zone_versions WHERE zone_name = ? ORDER BY created_at DESC LIMIT 100
)
```

### Тесты

- `backend/tests/test_zone_versioning.py` — create, list, limit, restore, delete, checksum dedup

---

## Этап 4: Backup / Export

### Файлы

| Файл | Действие |
|------|----------|
| `backend/app/exporter.py` | **Создать** — export/import logic |
| `backend/app/main.py` | **Изменить** — эндпоинты backup |

### exporter.py — функции

```python
def export_zone(zone_name, version_id=None) -> str       # content
def export_all_zones() -> bytes                          # tar.gz
def import_zone(zone_name, content) -> dict              # validate + create_version
```

### Эндпоинты

```
GET    /api/backup/export-all            → tar.gz
POST   /api/backup/run                   → ручной снапшот всех зон
GET    /api/backup/status                → статистика (N зон, N версий, размер)
```

### Export tar.gz структура

```
export-2026-09-18.tar.gz
├── example.com.zone
├── test.com.zone
├── metadata.json     # [{zone, serial, checksum, updated_at}]
└── settings.json     # {"knot_conf": "...", "initial_backup_done": true, ...}
```

### Import

```
POST /api/zones/{zone}/versions/import  (multipart: zone-файл)
→ validate_zonefile
→ extract_serial
→ sha256(content) vs last_version.checksum → если совпадает → 409 Conflict
→ create_version(source="import")
→ НЕ проливаем на сервер (пользователь решит когда)
```

### Тесты

- `backend/tests/test_exporter.py` — export tar.gz, import zone, dedup by checksum

---

## Этап 5: DNS Backend Abstraction

### Файлы

| Файл | Действие |
|------|----------|
| `backend/app/dns_backend.py` | **Создать** — ABC |
| `backend/app/adapters/__init__.py` | **Создать** — пустой |
| `backend/app/adapters/knot_k8s.py` | **Создать** — адаптер |
| `backend/app/main.py` | **Изменить** — выделить логику в адаптер |

### DNSBackend (ABC)

```python
class DNSBackend(ABC):
    @abstractmethod
    def get_zone(self, zone_name: str) -> str | None: ...
    @abstractmethod
    def update_zone(self, zone_name: str, content: str) -> dict: ...
    @abstractmethod
    def create_zone(self, zone_name: str, content: str) -> dict: ...
    @abstractmethod
    def delete_zone(self, zone_name: str) -> dict: ...
    @abstractmethod
    def list_zone_names(self) -> list[str]: ...
    def push_server_config(self, knot_conf: str) -> dict: ...
    def push_tsig_keys(self, tsig_yaml: str) -> dict: ...
```

### KnotK8sAdapter

Переместить из `main.py`:
- `_read_knot_conf_map`, `_apply_knot_conf_and_restart`, `_trigger_knot_restart`
- `_knotc_zone_notify`, `_sync_zone_to_secondaries`
- `_apply_zone_update` → `update_zone()`
- `ensure_zone_in_knot_conf`, `ensure_zone_in_knot_conf_secondary`

### Эндпоинты

```
GET    /api/push/status       — что не пролито (сравнение БД vs ConfigMap)
POST   /api/push/zones        — пролить все зоны
POST   /api/push/zones/{zone} — пролить одну зону
POST   /api/push/config       — пролить knot.conf
```

### Тесты

- `backend/tests/test_dns_backend.py` — ABC methods, mock adapter
- `backend/tests/test_knot_k8s_adapter.py` — перемещённые тесты из test_zone_update

---

## Этап 6: Per-Zone ACL

### Что делаем

1. Поле `zones.tsig_allowed_keys` уже в схеме (из Этапа 1)
2. API для управления:

```
GET    /api/zones/{zone}/acl          — список allowed key_id
PUT    /api/zones/{zone}/acl          — заменить список
POST   /api/zones/{zone}/acl/{key_id} — добавить ключ
DELETE /api/zones/{zone}/acl/{key_id} — удалить ключ
```

3. При push на Knot: генерация acl-блоков в knot.conf
   - Для каждого key_id из `tsig_allowed_keys`:
     - Ищем в `tsig_keys` по key_id
     - Если scope=`axfr` → `action: transfer`
     - Если scope=`dns_update` → `action: update`
   - Формируем acl-блоки + ссылки в zone-блоке

### Тесты

- `backend/tests/test_zone_acl.py` — add/remove keys, generate knot.conf acl blocks

---

## Этап 7: Secondary AXFR Key Distribution

### Что делаем

- Общий ключ `axfr-secondary` уже в `tsig_keys` (из initial backup)
- При создании новой зоны: автоматически добавляем `axfr-secondary` в `tsig_allowed_keys`
- При push на Knot: acl-блок с ключом `axfr-secondary` генерируется в knot.conf
- Secondary получают зону через AXFR с этим ключом (штатная работа Knot)

### Тесты

- Playground: primary → secondary AXFR с общим ключом

---

## Этап 8: Фронтенд

### Новые файлы

| Файл | Действие |
|------|----------|
| `src/views/TsigKeys.vue` | **Создать** — управление TSIG-ключами |
| `src/views/Backup.vue` | **Создать** — backup status, export, snapshots |

### Изменяемые файлы

| Файл | Действие |
|------|----------|
| `src/api/client.ts` | **Изменить** — новые типы + функции |
| `src/router/index.ts` | **Изменить** — маршруты /tsig-keys, /backup |

### TsigKeys.vue

```
Таблица ключей:
  key_id | algorithm | scope | status | created_at | revoked_at | [Revoke] [Delete]
Кнопка "Создать ключ":
  → диалог: имя, scope, algorithm
  → после: показать secret (один раз, кнопка Copy)
```

### Backup.vue

```
Статистика: N зон, M версий, размер БД
Кнопка "Создать снапшот"  → POST /api/backup/run
Кнопка "Экспорт всех зон" → download tar.gz
```

### Zones.vue — вкладка "Версии"

Добавить tab `versions`:

```
Список версий:
  #id | serial | source | created_at | [View] [Restore] [Export] [Delete]
```

После save: обновить список версий.

---

## Порядок реализации (12 шагов)

| Шаг | Этап | Действие | Проверка |
|-----|------|----------|----------|
| 1 | 1 | `db.py` + `crypto_utils.py` + `settings.py` + requirements.txt | pytest |
| 2 | 1 | `startup_backup()` — миграция ConfigMap→SQLite | pytest + fake ConfigMap |
| 3 | 1 | Интеграция в `main.py` (startup, _apply_zone_update) | pytest |
| 4 | 2 | `routers/tsig_keys.py` — эндпоинты TSIG-ключей | pytest |
| 5 | 3 | `zone_versioning.py` — CRUD + limit 100 | pytest |
| 6 | 3 | Эндпоинты версий в `main.py` | curl / pytest |
| 7 | 4 | `exporter.py` — export tar.gz, import zone | pytest |
| 8 | 4 | Backup эндпоинты | curl / pytest |
| 9 | 5 | `dns_backend.py` + `KnotK8sAdapter` | pytest |
| 10 | 6 | Per-zone ACL API + push в knot.conf | integration test |
| 11 | 7 | `tsig_poller.py` — polling раз в час | test + playground |
| 12 | 8 | Фронтенд: client.ts, TsigKeys.vue, Backup.vue, вкладка Версии | manual |
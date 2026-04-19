# Changelog

Формат: [семвер] — дата, затем список изменений. Новые записи добавляйте **вверх** файла.

## [0.2.0] — 2026-04-19

### Редактор `knot.conf` (dnsadmin)

- **Новый экран «knot.conf»** (`/knot-conf`): три вкладки — **Форма** (поля по схеме), **YAML** (полный текст), **AXFR (Secret)** (фрагмент `key:` / `acl:` из Kubernetes Secret).
- **Навигация** между экранами «Зоны» и «knot.conf» в шапке обоих представлений.

### API (FastAPI)

- `GET /api/knot-conf` — текущий `knot.conf` из ConfigMap и версия JSON-схемы редактора.
- `GET /api/knot-conf/schema` — описание полей для UI: типы, `enum`, тексты подсказок (без внешних URL; тексты хранятся в `backend/app/knot_schema/schema.json`).
- `GET /api/knot-conf/model` — структурированная модель (секции `server`, `include`, список `zone`) для формы.
- `POST /api/knot-conf/render-model` — сборка YAML из JSON-модели **без** записи в кластер (предпросмотр и цепочка «форма → проверка»).
- `POST /api/knot-conf/validate` — разбор YAML и **`knotc conf-check`** во временном каталоге с подстановкой файлов зон из ConfigMap и фрагмента AXFR; опциональное тело **`axfr_override`** — проверка с переданным текстом Secret без сохранения.
- `PUT /api/knot-conf` — сохранение сырого `knot.conf`: валидация → запись в ConfigMap `knot-config` → перезапуск Deployment Knot (как для зон).
- `PUT /api/knot-conf/model` — применение модели формы к текущему конфигу (через `ruamel.yaml`, сохраняются прочие верхнеуровневые секции), затем та же валидация и запись.
- `GET /api/knot-conf/axfr` — чтение Secret (по умолчанию `knot-axfr`, ключ `axfr.conf`).
- `PUT /api/knot-conf/axfr` — запись Secret после YAML-парса фрагмента и успешного `knotc conf-check` с этим содержимым, затем перезапуск Knot.

### Бэкенд (модули и зависимости)

- **`ruamel.yaml`** — парсинг/сериализация `knot.conf` с меньшей потерей структуры, чем у грубого текстового редактирования.
- **`app/knot_schema/schema.json`** — схема MVP для полей формы (`server.listen`, `identity`, `nsid`, `automatic-acl`, `include`, поля зоны: `domain`, `file`, `master`, `notify`, `acl`, `dnssec-signing`).
- **`app/knot_editor_model.py`** — извлечение модели из дерева YAML и обратное наложение; пустые строки `domain` в форме не попадают в итоговый список зон.
- **`app/knot_validate.py`** — подготовка песочницы для `knotc`: переписывание путей `file:` и `include` на временные файлы, подстановка `*.zone` из ConfigMap.

### Контейнер (Docker)

- **Базовый образ** финальной стадии заменён на **`cznic/knot:3.4`** с установкой Python 3 и pip: в образе доступен **`knotc`** той же ветки, что и у сервера Knot в кластере, для согласованной проверки `conf-check`.

### Kubernetes / документация

- **RBAC** (`dnsadmin-rbac.yaml`): для ServiceAccount `dnsadmin` добавлены права на Secret **`knot-axfr`** (`get`, `patch`, `update`) — чтение и обновление AXFR-фрагмента из админки.
- **Документация в репозитории:** каталог **`k8s/`** в корне dnsadmin-ui (`k8s/README.md`) — ConfigMap / Secret / монтирование в pod, NOTIFY/AXFR между провайдерами, переменные `KNOT_AXFR_SECRET_NAME` / `KNOT_AXFR_SECRET_KEY`; вне репозитория короткая отсылка в `CONFIG_RUNBOOK.md` у стека `configs/dns-knot`.
- **Комментарий** в `knot-deployment.yaml` у volume Secret `knot-axfr` — отсылка на `k8s/README.md` в репозитории.

### Тесты

- **`tests/test_knot_editor.py`** — разбор модели, применение к YAML, `master`/`notify`/ACL, сохранение сторонних секций (например `log:`).

### Версионирование приложения

- Версия HTTP API (метаданные FastAPI) поднята до **2.1.0** (минор относительно 2.0.0).

---

## [0.1.1] — 2026-04-19

- **dnsadmin (UI + API):** вкладка редактора зоны «Форма» (SOA, NS, таблица записей) и вкладка «Текст»; синтаксис zone-файла проверяется через API (dnspython) перед сохранением и по кнопке «Проверить синтаксис».
- **dnsadmin:** эндпоинты `POST .../validate`, `.../parse-form`, `.../render-form`, `PUT .../form`; сохранение сырого текста зоны тоже валидируется перед записью в ConfigMap.
- **dnsadmin:** индикатор доступности Knot (`GET /api/dns-health`, UDP SOA); в Deployment добавлены переменные `KNOT_DNS_HOST`, `DNS_HEALTH_PROBE_ZONE`.
- **dnsadmin-ui backend:** исправлены отступы при добавлении зоны в `knot.conf` (`ensure_zone_in_knot_conf` — выравнивание блока `zone:`).
- **dnsadmin-ui backend:** `zone_editor.py` — разбор/сборка зоны, длинные TXT режутся на строки ≤255 символов для валидного BIND-текста.
- **Knot Deployment:** монтирование всех ключей зон из ConfigMap в `/zones` (убран ограниченный список `items`), чтобы зоны, добавленные через API, попадали в pod.

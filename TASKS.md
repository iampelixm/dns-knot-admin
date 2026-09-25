# Реестр задач — dnsadmin-ui

## Активные задачи

### P1: Синхронизация зон между инстансами (critical)
Зона, добавленная через UI, попадает только в ConfigMap primary (`knot-config`).
Secondary ConfigMap-ы (`knot-config-ns1`, `knot-config-ns2`) не обновляются —
вторичные Knot-инстансы не знают о новой зоне и игнорируют NOTIFY.

- [x] Доработать `_apply_zone_update()` — при добавлении/изменении зоны обновлять
      все secondary ConfigMap-ы в соответствии со списком `KNOT_INSTANCES`
- [x] Рестартовать secondary Deployment-ы после обновления их ConfigMap
- [x] Синхронизация работает на каждом сохранении, а не только для новых зон
- [x] Добавлен эндпоинт `POST /api/zones/{zone}/sync` для принудительной синхронизации
- [x] Для `summer-site.ru` (уже создана): достаточно отредактировать и сохранить —
      или вызвать `POST /api/zones/summer-site.ru/sync`
- [x] После добавления зоны — выполнить синхронизацию: `POST /api/zones/summer-site.ru/sync`

### ✅ API-документация (v0.4.13)
- [x] Swagger UI (`/docs`) и ReDoc (`/redoc`) включены
- [x] OpenAPI-описание + docstrings ко всем эндпоинтам
- [x] Теги для группировки роутеров (Zones, TSIG Keys, DNS Servers, Zone Servers)
- [x] docs/api.md обновлён до актуального состояния
- [x] Исправлена API-несовместимость (zone_name → name, content URL)

### ✅ Удаление зон (v0.4.13)
- [x] DELETE /api/zones/{zone_name} — удаляет zone-файл из ConfigMap, убирает блок из knot.conf,
      перезапускает Knot primary + secondary, затем удаляет из БД
- [x] Кнопка удаления в UI редактора зон (с подтверждением ElMessageBox)

### ✅ P2: Ручное управление zone-файлами через API (medium)
- [x] CreateZoneDialog — создание зоны через PUT /api/zones/{name}/form
- [ ] POST /api/zones — эндпоинт создания зоны с базовым шаблоном
- [ ] PATCH /api/zones/{zone_name}/serial — ручной serial bump

### ✅ P3: DNSSEC — генерация ключей (medium)
DNSSEC DS/DNSKEY отображаются, есть UI для включения.
Knot генерирует KSK/ZSK автоматически при включении dnssec-signing.

- [x] POST /api/zones/{zone_name}/dnssec/init — включение signing + рестарт + получение DS
- [x] Кнопка «Включить DNSSEC (с генерацией ключей)» в UI

### ✅ P3: Backup зон (medium)
- [x] GET /api/zones/export — скачать все zone-файлы + knot.conf tgz
- [x] POST /api/zones/import — загрузить tgz и импортировать в ConfigMap
- [x] Кнопки «Экспорт» / «Импорт» в тулбаре редактора зон

### P4: ns1 — нода hello в статусе NotReady (low)
- [ ] Восстановить ноду hello.cluster.summersite.ru или перенести ns1 на другую ноду
- [ ] Обновить манифесты deploy/summersite/

### P4: Миграция на deploy/dns-knot (low)
- [ ] Заменить статичные манифесты deploy/summersite/ на шаблонизированные
      deploy/dns-knot/
- [ ] Провести деплой через новый скрипт deploy-dns-knot.sh

### P5: Knot Conf Editor — MVP (low)
- [ ] Более структурированный редактор конфигурации (сейчас сырой textarea)

### P5: Zone-автодополнение в форме редактора (low)
- [ ] Автоподстановка $ORIGIN, $TTL, серийного номера

## Выполненные задачи

### ✅ Multi-instance поддержка (v0.4)
- `KNOT_INSTANCES` — JSON с ns/ns1/ns2
- `GET /api/zones/sync-status` — сверка serial по всем серверам
- `GET /api/knot-conf?instance=ns1` — работа с конкретным инстансом

### ✅ AXFR-редактор
- Чтение/запись TSIG-ключей через Secret
- Генерация через keymgr, парсинг/рендер YAML
- Валидация knot.conf с подстановкой axfr

### ✅ Ingress-мастер
- Список namespace/service/ingress из кластера
- Генерация YAML-манифеста Ingress

### ✅ Zone-редактор
- Текстовый редактор (CodeMirror) и форма
- Автоserial bump, knotc zone-notify
- Upsert-запись, DNSSEC вкл/выкл

### ✅ JWT-аутентификация
- POST /api/auth/login → Bearer-токен
- Все /api/* эндпоинты защищены

### ✅ cert-manager webhook DNS-01 (v0.4.12)
- Webhook endpoints: present/cleanup/healthz с поддержкой APIService-путей
- run.py: dual-port uvicorn (HTTP 8080 + HTTPS 8443)
- build.sh: генерация самоподписанного CA + server cert
- APIService + ClusterIssuer + TLS Secret templates
- YAML-patch для deployment/service при DNS01=webhook
- init-answers.sh: выбор режима rfc2136 / webhook
- DNS01_MODE filtering в build.sh (rfc2136/webhook)
- sort_keys=False в yaml.dump() для сохранения порядка полей
- APIService: groupPriorityMinimum/versionPriority + port 443
- deploy.sh: TLS Secret применяется до deployment

### 🔲 Проверить E2E (webhook DNS01)
- [ ] Собрать образ dnsadmin с webhook-эндпоинтами
- [ ] Развернуть с DNS01=yes, DNS01_MODE=webhook
- [ ] Создать тестовый Certificate в cert-manager
- [ ] Проверить, что сертификат выпущен (issued)
- [ ] Проверить, что TXT-запись очищена после выпуска
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
- [ ] Для `summer-site.ru` (уже создана): достаточно отредактировать и сохранить —
      или вызвать `POST /api/zones/summer-site.ru/sync`

### P1: API-документация (high)
- [ ] Включить Swagger UI и ReDoc в FastAPI (сейчас `docs_url=None, redoc_url=None`)
- [ ] Добавить автодокументацию по каждому эндпоинту
- [ ] При необходимости защитить /docs базовой аутентификацией или закрыть за JWT
- [ ] Обновить docs/api.md до актуального состояния

### P2: Удаление зон (medium)
Нет UI/API для удаления зоны. Нужно:
- [ ] DELETE /api/zones/{zone_name} — удалить zone-файл из ConfigMap, убрать блок
      из knot.conf, перезапустить Knot
- [ ] Добавить кнопку удаления в UI (с подтверждением)

### P2: Ручное управление zone-файлами через API (medium)
- [ ] Добавить эндпоинт POST /api/zones — создание новой зоны с базовым шаблоном
- [ ] PATCH /api/zones/{zone_name}/serial — ручной serial bump

### P3: DNSSEC — генерация ключей (medium)
DNSSEC DS/DNSKEY отображаются, но нет UI для первоначального включения
(нужно сгенерировать KSK/ZSK через `keymgr`).

- [ ] POST /api/zones/{zone_name}/dnssec/init — генерация ключей + включение signing
- [ ] Соответствующий UI в редакторе зон

### P3: Backup зон (medium)
- [ ] Скрипт или эндпоинт для бэкапа всех zone-файлов из ConfigMap
- [ ] Restore зон из бэкапа

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
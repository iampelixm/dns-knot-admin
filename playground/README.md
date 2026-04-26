# Knot DNS Playground

Локальная среда для тестирования мульти-инстанс конфигурации Knot DNS:
**1 primary + 2 secondary** с репликацией через AXFR/NOTIFY и TSIG-аутентификацией.

---

## Содержание

1. [Архитектура](#архитектура)
2. [Требования](#требования)
3. [Быстрый старт](#быстрый-старт)
4. [Тестовые зоны](#тестовые-зоны)
5. [Как работает репликация](#как-работает-репликация)
6. [Скрипты](#скрипты)
7. [Ручные команды](#ручные-команды)
8. [Сценарии тестирования](#сценарии-тестирования)
9. [API dnsadmin](#api-dnsadmin)
10. [Конфигурация KNOT\_INSTANCES](#конфигурация-knot_instances)
11. [Устранение проблем](#устранение-проблем)

---

## Архитектура

```
                    ┌─────────────────────────────────┐
                    │     Docker-сеть dns-playground   │
                    │         172.30.0.0/24            │
                    │                                  │
  ┌─────────────────┴──────┐                          │
  │   knot-primary         │                          │
  │   172.30.0.10 : 53     │ ──NOTIFY──►  knot-secondary1
  │   host port: 15353     │             172.30.0.11 : 53
  │   роль: PRIMARY        │ ──NOTIFY──►  host port: 15354
  │   зоны: все 3          │                          │
  └─────────────────┬──────┘ ──NOTIFY──►  knot-secondary2
                    │             172.30.0.12 : 53    │
                    │             host port: 15355     │
                    │◄──AXFR──────────────────────────┤
                    └─────────────────────────────────┘
```

| Контейнер        | Внутренний IP | Порт на хосте | Роль      |
|------------------|--------------|---------------|-----------|
| `knot-primary`   | 172.30.0.10  | 15353         | PRIMARY   |
| `knot-secondary1`| 172.30.0.11  | 15354         | SECONDARY |
| `knot-secondary2`| 172.30.0.12  | 15355         | SECONDARY |

**Аутентификация:** TSIG `hmac-sha256`, ключ `playground-tsig` — для AXFR и NOTIFY.  
**Защита от рассинхрона:** `refresh-min-interval: 14400` — secondary поллит primary каждые 4 часа даже без NOTIFY.

> ⚠️ TSIG-ключ в этом playground захардкожен и предназначен **только для тестирования**.

---

## Требования

- Docker Engine 24+
- Docker Compose v2 (`docker compose`)
- `dig` (пакет `dnsutils` / `bind-tools`)
- `curl` (для API-скриптов)
- `python3` (для форматирования JSON в API-скриптах)

```bash
# Ubuntu / Debian
sudo apt install dnsutils curl python3

# macOS
brew install bind curl python3
```

---

## Быстрый старт

```bash
# Из корня репозитория
cd playground

# Поднять все три контейнера
docker compose up -d

# Подождать 5-10 секунд (secondary делают начальный AXFR)
sleep 10

# Проверить синхронизацию и базовые запросы
./check.sh
```

Ожидаемый вывод `check.sh`:
```
✓ primary (127.0.0.1:15353) — SOA serial: 2026042501
✓ secondary1 (127.0.0.1:15354) — SOA serial: 2026042501
✓ secondary2 (127.0.0.1:15355) — SOA serial: 2026042501
...
✓ Все зоны синхронизированы.
```

---

## Тестовые зоны

### `example.test`

Полноценная зона с разными типами записей. Используйте для проверки резолвинга.

| Запись                          | Тип    | Значение                   |
|---------------------------------|--------|----------------------------|
| `example.test`                  | A      | 172.30.0.10                |
| `example.test`                  | MX     | 10 mail.example.test       |
| `example.test`                  | TXT    | v=spf1 a mx ~all           |
| `www.example.test`              | A      | 172.30.0.10                |
| `www.example.test`              | AAAA   | ::1                        |
| `ftp.example.test`              | CNAME  | www.example.test           |
| `api.example.test`              | A      | 172.30.0.10                |
| `ns1/ns2/ns3.example.test`      | A      | 172.30.0.10/11/12          |
| `_dmarc.example.test`           | TXT    | v=DMARC1; p=none; …        |
| `_xmpp-client._tcp.example.test`| SRV    | 5 0 5222 xmpp.example.test |

### `playground.local`

Инфра-зона с TXT-маркером для проверки синхронизации.

| Запись                     | Тип   | Значение                          |
|----------------------------|-------|-----------------------------------|
| `sync.playground.local`    | TXT   | "если одинаково — синхронизировано" |
| `primary.playground.local` | A     | 172.30.0.10                      |
| `slave1.playground.local`  | A     | 172.30.0.11                      |
| `cache.playground.local`   | A     | 172.30.0.11                      |

### `db.test`

Зона для симуляции рассинхрона (используется скриптом `simulate-desync.sh`).

| Запись                      | Тип   | Значение               |
|-----------------------------|-------|------------------------|
| `primary.db.test`           | A     | 172.30.0.10            |
| `postgres.db.test`          | CNAME | primary.db.test        |
| `_postgresql._tcp.db.test`  | SRV   | 0 0 5432 postgres.db.test |

---

## Как работает репликация

### Нормальный режим: NOTIFY + AXFR

```
1. Зона изменилась на primary (новый serial в SOA)
2. Knot primary: knotc zone-notify <zone>
3. Primary → UDP NOTIFY → secondary1, secondary2
   (аутентифицированный TSIG-подписью)
4. Secondary получает NOTIFY, сравнивает serial
5. Если serial выше → инициирует TCP AXFR запрос
   (с TSIG-ключом для авторизации)
6. Primary проверяет TSIG → отдаёт зону
7. Secondary сохраняет зону в /var/lib/knot
```

### Защита при потере связи: refresh-min-interval

```
1. Связь между secondary и primary потеряна
2. NOTIFY не доходят
3. Каждые 4 часа (refresh-min-interval: 14400) secondary
   самостоятельно опрашивает primary: «текущий serial?»
4. Когда связь восстанавливается → AXFR при следующем опросе
```

### Принудительная синхронизация

```bash
# С primary (NOTIFY → все secondary сами сделают AXFR)
docker exec knot-primary knotc zone-notify example.test

# С secondary (немедленный AXFR без ожидания NOTIFY)
docker exec knot-secondary1 knotc zone-refresh example.test
```

---

## Скрипты

Все скрипты находятся в `scripts/`. Запускать из каталога `playground/`:

```bash
bash scripts/<script>.sh [аргументы]
```

### `check.sh` — быстрая проверка

Общая проверка: доступность серверов, сверка serial, примеры резолвинга.

```bash
./check.sh
```

---

### `scripts/dig-queries.sh` — DNS-запросы

Выполняет набор dig-запросов ко всем трём серверам: SOA, A, AAAA, MX, TXT, CNAME, SRV.
Наглядно показывает что все серверы отвечают одинаково.

```bash
bash scripts/dig-queries.sh
```

---

### `scripts/axfr-test.sh` — тест AXFR

Проверяет корректность TSIG-авторизации при zone transfer:
- AXFR с правильным ключом → должен пройти
- AXFR без ключа → должен быть отклонён (REFUSED/NOTAUTH)
- AXFR с неверным ключом → должен быть отклонён (BADSIG)
- Полный дамп зоны `example.test`

```bash
bash scripts/axfr-test.sh
```

---

### `scripts/knotc.sh` — управление через knotc

```bash
bash scripts/knotc.sh status        # статус всех зон на всех серверах
bash scripts/knotc.sh notify        # NOTIFY всех зон с primary
bash scripts/knotc.sh notify db.test # NOTIFY конкретной зоны
bash scripts/knotc.sh refresh       # принудительный AXFR на secondary
bash scripts/knotc.sh refresh db.test
bash scripts/knotc.sh stats         # статистика (запросы, transfers)
bash scripts/knotc.sh conf-check    # валидация конфига на всех серверах
bash scripts/knotc.sh keys          # показать TSIG-ключи
bash scripts/knotc.sh flush db.test # zone-purge + refresh
```

---

### `scripts/simulate-desync.sh` — симуляция рассинхрона

Полный сценарий:
1. Изолирует `secondary1` от сети
2. Меняет зону `db.test` на primary (новый serial + TXT-маркер)
3. Показывает рассинхрон
4. Восстанавливает сеть и запускает принудительный refresh
5. Откатывает zone-файл в исходное состояние

```bash
bash scripts/simulate-desync.sh
```

---

### `scripts/force-sync.sh` — принудительная синхронизация

Отправляет NOTIFY со всего primary + zone-refresh на всех secondary.
Использовать когда нужна немедленная синхронизация без ожидания.

```bash
bash scripts/force-sync.sh
```

---

### `scripts/api.sh` — запросы к dnsadmin API

Требует запущенного dnsadmin. Укажите URL через `DNSADMIN_URL`.

```bash
# Через kubectl port-forward (k8s продакшн)
kubectl --context=summersite port-forward -n dns-knot svc/dnsadmin 8080:80 &
DNSADMIN_URL=http://localhost:8080 DNSADMIN_PASSWORD=<пароль> bash scripts/api.sh

# Все доступные команды
bash scripts/api.sh all               # выполнить всё
bash scripts/api.sh login             # только получить токен
bash scripts/api.sh health            # GET /api/dns-health
bash scripts/api.sh zones             # GET /api/zones
bash scripts/api.sh zone example.test # GET /api/zones/example.test
bash scripts/api.sh sync-status       # GET /api/zones/sync-status
bash scripts/api.sh instances         # GET /api/instances
bash scripts/api.sh validate db.test  # POST /api/zones/db.test/validate
bash scripts/api.sh knot-conf         # GET /api/knot-conf
bash scripts/api.sh knot-validate     # POST /api/knot-conf/validate
bash scripts/api.sh axfr-status       # GET /api/knot-conf/axfr-status
```

---

## Ручные команды

### dig

```bash
# SOA (serial) на всех трёх серверах
dig @127.0.0.1 -p 15353 example.test SOA +short   # primary
dig @127.0.0.1 -p 15354 example.test SOA +short   # secondary1
dig @127.0.0.1 -p 15355 example.test SOA +short   # secondary2

# Стандартные запросы
dig @127.0.0.1 -p 15353 www.example.test A +short
dig @127.0.0.1 -p 15353 example.test MX +short
dig @127.0.0.1 -p 15353 example.test TXT +short
dig @127.0.0.1 -p 15353 _xmpp-client._tcp.example.test SRV +short
dig @127.0.0.1 -p 15353 ftp.example.test CNAME +short

# AXFR с TSIG (zone transfer)
TSIG="hmac-sha256:playground-tsig:cGxheWdyb3VuZC10c2lnLWtleS1mb3ItdGVzdGluZy1vbmx5IQ=="
dig @127.0.0.1 -p 15353 example.test AXFR -y "$TSIG"

# AXFR без ключа (ожидаем REFUSED)
dig @127.0.0.1 -p 15353 example.test AXFR

# Версия и NSID сервера
dig @127.0.0.1 -p 15353 version.bind TXT CH +short
dig @127.0.0.1 -p 15353 +nsid example.test SOA
```

### docker exec / knotc

```bash
# Статус всех зон
docker exec knot-primary    knotc zone-status
docker exec knot-secondary1 knotc zone-status
docker exec knot-secondary2 knotc zone-status

# Отправить NOTIFY
docker exec knot-primary knotc zone-notify example.test
docker exec knot-primary knotc zone-notify playground.local
docker exec knot-primary knotc zone-notify db.test

# Принудительный AXFR на secondary
docker exec knot-secondary1 knotc zone-refresh example.test
docker exec knot-secondary2 knotc zone-refresh example.test

# Статистика зоны
docker exec knot-primary knotc zone-stats example.test

# Проверка конфига
docker exec knot-primary knotc conf-check

# Просмотр конфига (live)
docker exec knot-primary knotc conf-read server
docker exec knot-primary knotc conf-read zone
docker exec knot-primary knotc conf-read key

# Список ACL
docker exec knot-primary knotc conf-read acl
```

### Логи

```bash
# Все три сервера в реальном времени
docker compose logs -f

# Только один сервер
docker logs -f knot-primary
docker logs -f knot-secondary1

# Фильтр по AXFR/NOTIFY событиям
docker logs knot-primary 2>&1 | grep -i "transfer\|notify\|axfr"
```

---

## Сценарии тестирования

### Сценарий 1: Изменение зоны и автоматическая репликация

```bash
# 1. Проверяем serial до
dig @127.0.0.1 -p 15353 example.test SOA +short

# 2. Открываем zones/example.test.zone в редакторе
# Меняем serial (например 2026042501 → 2026042502)
# Добавляем запись: test-record IN A 172.30.0.99

# 3. Перезапускаем primary (применяет новый файл)
docker compose restart knot-primary

# 4. Отправляем NOTIFY (primary сам его отправит при старте,
#    но можно явно)
docker exec knot-primary knotc zone-notify example.test

# 5. Через 3-5 секунд проверяем репликацию
./check.sh
```

### Сценарий 2: Проверка рассинхрона

```bash
bash scripts/simulate-desync.sh
```

### Сценарий 3: Восстановление после падения secondary

```bash
# Останавливаем secondary1
docker stop knot-secondary1

# Меняем зону (bump serial)
# ... редактируем zones/example.test.zone ...
docker compose restart knot-primary

# Запускаем обратно
docker start knot-secondary1

# secondary1 автоматически сделает AXFR при старте
sleep 5
./check.sh
```

### Сценарий 4: Тест 4-часового refresh

```bash
# Изолируем secondary1
docker network disconnect knot-playground_dns knot-secondary1

# Меняем зону на primary
# ... bump serial ...
docker compose restart knot-primary

# secondary1 отстаёт — NOTIFY не дошёл
dig @127.0.0.1 -p 15354 example.test SOA +short  # старый serial

# Восстанавливаем сеть
docker network connect knot-playground_dns knot-secondary1

# Эмулируем срабатывание 4-часового таймера
docker exec knot-secondary1 knotc zone-refresh example.test
sleep 3

# Зоны синхронизированы
dig @127.0.0.1 -p 15354 example.test SOA +short  # новый serial
```

---

## API dnsadmin

Полный список эндпоинтов (v0.4.0):

| Метод  | Путь                                | Описание                          |
|--------|-------------------------------------|-----------------------------------|
| POST   | `/api/auth/login`                   | Получить JWT-токен                |
| GET    | `/health`                           | Health check                      |
| GET    | `/api/dns-health`                   | SOA-проба DNS-сервера             |
| GET    | `/api/instances`                    | Список Knot-инстансов             |
| GET    | `/api/zones`                        | Список зон                        |
| GET    | `/api/zones/sync-status`            | Сверка SOA serial по инстансам    |
| GET    | `/api/zones/{zone}`                 | Содержимое зоны                   |
| PUT    | `/api/zones/{zone}`                 | Сохранить зону (текст)            |
| PUT    | `/api/zones/{zone}/form`            | Сохранить зону (форма)            |
| POST   | `/api/zones/{zone}/validate`        | Валидировать zone-файл            |
| POST   | `/api/zones/{zone}/parse-form`      | Разобрать zone-файл на поля       |
| POST   | `/api/zones/{zone}/render-form`     | Собрать zone-файл из полей        |
| PATCH  | `/api/zones/{zone}/dnssec`          | Включить/выключить DNSSEC         |
| GET    | `/api/zones/{zone}/dnssec-ds`       | Получить DS-записи                |
| GET    | `/api/knot-conf`                    | Получить knot.conf                |
| POST   | `/api/knot-conf/validate`           | Валидировать knot.conf            |
| PUT    | `/api/knot-conf`                    | Сохранить knot.conf               |
| GET    | `/api/knot-conf/axfr`               | Получить AXFR-секрет              |
| PUT    | `/api/knot-conf/axfr`               | Сохранить AXFR-секрет             |
| GET    | `/api/knot-conf/axfr-status`        | Диагностика AXFR-секрета          |
| POST   | `/api/knot-conf/axfr/parse-fragment`| Разобрать AXFR YAML-фрагмент     |
| POST   | `/api/knot-conf/axfr/render-model`  | Собрать AXFR YAML из модели       |
| POST   | `/api/knot-conf/axfr/generate-tsig` | Сгенерировать новый TSIG-ключ     |

### Пример: полный curl-сценарий

```bash
BASE="http://localhost:8080"

# 1. Авторизация
TOKEN=$(curl -sf -X POST "$BASE/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"change-me"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

AUTH="Authorization: Bearer $TOKEN"

# 2. Список зон
curl -sf -H "$AUTH" "$BASE/api/zones" | python3 -m json.tool

# 3. Сверка синхронизации
curl -sf -H "$AUTH" "$BASE/api/zones/sync-status" | python3 -m json.tool

# 4. Получить зону
curl -sf -H "$AUTH" "$BASE/api/zones/example.test" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['content'])"

# 5. Валидировать knot.conf
CONF=$(curl -sf -H "$AUTH" "$BASE/api/knot-conf" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['raw'])")

curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"content\": $(python3 -c "import sys,json; print(json.dumps('$CONF'))")}" \
  "$BASE/api/knot-conf/validate" | python3 -m json.tool
```

---

## Конфигурация KNOT\_INSTANCES

Для того чтобы dnsadmin знал обо всех трёх серверах (вкладка «Серверы» в редакторе зон),
установите переменную окружения `KNOT_INSTANCES` в Deployment dnsadmin:

```json
[
  {
    "id": "ns1",
    "label": "ns1 (primary)",
    "ip": "37.230.115.233",
    "role": "primary",
    "configmap": "knot-config-ns1",
    "deployment": "knot-ns1"
  },
  {
    "id": "ns2",
    "label": "ns2 (secondary)",
    "ip": "1.2.3.4",
    "role": "secondary",
    "configmap": "knot-config-ns2",
    "deployment": "knot-ns2"
  },
  {
    "id": "ns3",
    "label": "ns3 (secondary)",
    "ip": "5.6.7.8",
    "role": "secondary",
    "configmap": "knot-config-ns3",
    "deployment": "knot-ns3"
  }
]
```

```bash
# Установить через kubectl
kubectl --context=summersite set env deployment/dnsadmin -n dns-knot \
  'KNOT_INSTANCES=[{"id":"ns1","label":"ns1 (primary)","ip":"37.230.115.233","role":"primary","configmap":"knot-config","deployment":"knot"}]'

# Добавить второй инстанс — используйте scripts/deploy-knot-node.sh
bash ../scripts/deploy-knot-node.sh
```

Для playground (если хотите тестировать sync-status UI локально):
```bash
# В .env или в команде запуска dnsadmin
KNOT_INSTANCES='[
  {"id":"primary","label":"primary","ip":"127.0.0.1","role":"primary"},
  {"id":"secondary1","label":"secondary1","ip":"127.0.0.1","role":"secondary"},
  {"id":"secondary2","label":"secondary2","ip":"127.0.0.1","role":"secondary"}
]'
# Примечание: порты 15353/15354/15355 — поэтому sync-status с localhost работать не будет
# (dnsadmin шлёт на port 53). В продакшне это реальные IP нод.
```

---

## Устранение проблем

### Secondary не получает AXFR после старта

```bash
# Проверить логи secondary
docker logs knot-secondary1 2>&1 | tail -30

# Принудительный refresh
docker exec knot-secondary1 knotc zone-refresh example.test

# Проверить связность
docker exec knot-secondary1 ping -c 2 172.30.0.10
```

### AXFR отклоняется (NOTAUTH / REFUSED)

```bash
# Проверить конфиг ACL на primary
docker exec knot-primary knotc conf-read acl

# Проверить TSIG-ключи
docker exec knot-primary knotc conf-read key

# Выполнить conf-check
bash scripts/knotc.sh conf-check
```

### Серверы не отвечают на dig

```bash
# Проверить что контейнеры запущены
docker compose ps

# Перезапустить все
docker compose restart

# Проверить биндинг порта
docker exec knot-primary ss -ulnp | grep 53
```

### Очистить всё и начать заново

```bash
docker compose down -v   # -v удаляет тома с данными Knot
docker compose up -d
sleep 10
./check.sh
```

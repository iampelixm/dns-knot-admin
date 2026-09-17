# Kubernetes: примеры и эксплуатация (dns-knot)

Каталог **`k8s/`** — часть репозитория **dnsadmin-ui** (корень клона: рядом `backend/`, `src/`). Всё здесь версионируется вместе с админкой и не ссылается на внешние URL. Ниже — порядок развёртывания и справка по Knot + dnsadmin без отсылок к чужим деревьям файлов.

## Порядок применения манифестов (вручную)

1. `00-namespace.yaml`
2. `10-knot-pvc.yaml` — PVC под `/var/lib/knot`
3. `20-knot-axfr-secret.example.yaml` → Secret с TSIG/ACL (имя `knot-axfr`, ключ `axfr.conf` должны совпасть с Deployment)
4. `30-knot-configmap.example.yaml` — `knot.conf` и минимальная зона; **listen** и пробы должны быть согласованы с вашей сетью
5. `40-knot-deployment.example.yaml` — **readiness/liveness tcpSocket.host** замените на тот же адрес, что в `server.listen`, если используете `hostNetwork` и bind на конкретный IP ноды
6. `50-knot-service.yaml`
7. `60-dnsadmin-rbac.yaml`
8. `70-dnsadmin-auth-secret.example.yaml` — логин/пароль UI и `JWT_SECRET`
9. `80-dnsadmin-deployment.example.yaml` — образ dnsadmin и при необходимости `imagePullSecrets`
10. `90-dnsadmin-service.yaml`
11. `100-dnsadmin-ingress.example.yaml` — хост и IngressClass под ваш кластер

Файлы с суффиксом **`.example.yaml`** содержат плейсхолдеры: скопируйте, переименуйте при необходимости и подставьте свои значения.

## Kustomize

Файл `kustomization.example.yaml` задаёт список ресурсов из **этого же каталога**. Чтобы добавить зоны из файлов, расширьте `kustomization` блоком `configMapGenerator` с `behavior: merge` для ключа `knot-config` и перечислите свои `*.zone` в `files:` — синтаксис стандартный для Kustomize, без ссылки на другие каталоги репозитория.

## Образ из приватного registry

Инструкции в виде комментариев: `registry-pull-secret.howto.yaml` (не для `kubectl apply`).

---

## Где что лежит (ConfigMap / Secret / pod)

- **Основной конфиг Knot:** ConfigMap `knot-config`, ключ `knot.conf` (в dnsadmin: вкладки «Форма» / «YAML» для `knot.conf`).
- **TSIG и ACL для AXFR:** Secret `knot-axfr`, ключ `axfr.conf` (YAML-фрагменты `key:` и `acl:`). В `knot.conf` обычно есть `include` на путь внутри контейнера, например `/etc/knot/conf.d/axfr.conf`. Скрипт генерации фрагмента (Docker + `keymgr`): `../scripts/generate-axfr-tsig.sh` относительно этого каталога `k8s/`.
- **Pod Knot** (см. пример `40-knot-deployment.example.yaml` в этом каталоге) монтирует:
  - `knot.conf` из ConfigMap (subPath);
  - каталог зон из того же ConfigMap в `/zones`;
  - Secret `knot-axfr` в один файл `axfr.conf` (subPath).

Путь `file:` у записей `key:` в `axfr.conf` должен соответствовать тому, как Secret смонтирован в контейнере (часто один файл, а не каталог с отдельными ключами).

## Проверка конфигурации (dnsadmin)

dnsadmin вызывает **`knotc conf-check`** во временном каталоге: подставляются файлы зон из ConfigMap и содержимое Secret `knot-axfr` (или текст с вкладки «AXFR» при проверке с полем `axfr_override` в API).

Переменные окружения контейнера dnsadmin (опционально):

- `KNOT_AXFR_SECRET_NAME` (по умолчанию `knot-axfr`)
- `KNOT_AXFR_SECRET_KEY` (по умолчанию `axfr.conf`)

## RBAC

У ServiceAccount `dnsadmin` нужны права на ConfigMap `knot-config`, Deployment Knot, список pod, а также на Secret **`knot-axfr`** (`get`, `patch`, `update`) — см. `60-dnsadmin-rbac.yaml` в этом каталоге.

## Вторички у другого провайдера

1. В **ACL** (Secret `axfr.conf` или основной конфиг) разрешить `transfer` с IP вторичек и/или по TSIG.
2. На **primary** в зоне указать `notify` на вторички.
3. Между площадками должны проходить **TCP/53** (AXFR) и **UDP/53** для NOTIFY.

## Примечание про systemd-resolved и :53

Если на ноде порт 53 на loopback занят resolver’ом, в `knot.conf` часто выбирают привязку `listen` на конкретный внешний IP ноды, а не на `0.0.0.0@53`. Тогда пробы `tcpSocket` в Deployment должны бить в **тот же** адрес и порт, где реально слушает Knot.

## cert-manager DNS01 (webhook-провайдер)

Начиная с версии 0.4.12, dnsadmin реализует **cert-manager webhook DNS-01-провайдера**.  
Это альтернатива RFC2136: dnsadmin вместо TSIG-ключа напрямую добавляет `_acme-challenge` TXT-записи в зоны Knot через ConfigMap.

### Как это работает

```
cert-manager → APIService (v1.dnsadmin.knot.io) → dnsadmin:8443 (HTTPS webhook)
```

1. **build.sh** при `DNS01=yes` + `DNS01_MODE=webhook`:
   - Генерирует самоподписанный CA + серверный сертификат для `dnsadmin.{ns}.svc`
   - Создаёт манифесты: TLS Secret, APIService, ClusterIssuer (webhook)
   - Патчит deployment dnsadmin (порт 8443, TLS volume mount, env vars)
2. dnsadmin слушает HTTP на 8080 (UI) + HTTPS на 8443 (webhook)
3. cert-manager обнаруживает webhook через APIService и отправляет запросы
4. dnsadmin создаёт/удаляет `_acme-challenge.{zone}` TXT-запись в ConfigMap → Knot

### Переменные окружения (dnsadmin)

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `TLS_CERT_PATH` | – | Путь к TLS-сертификату (webhook) |
| `TLS_KEY_PATH` | – | Путь к TLS-ключу |
| `WEBHOOK_GROUP_NAME` | `dnsadmin.knot.io` | GroupName для cert-manager webhook |

### Порт 8443

Когда `DNS01=yes`, в deployment добавляется контейнерный порт `https-webhook:8443`,  
а в service — порт `443 → 8443`. APIService указывает на `dnsadmin.{ns}.svc:8443`.

### TLS-сертификат

Генерируется автоматически в `build.sh` (CA + server cert для `dnsadmin.{ns}.svc`),  
упаковывается в Secret `dnsadmin-webhook-tls`.

### Обновление cert-manager Deployment

cert-manager требует аргументы `--dns01-recursive-nameservers` и `--dns01-recursive-nameservers-only`,  
чтобы не ходить в публичные DNS при верификации `_acme-challenge` TXT.  
Файл `cert-manager-args-patch.yaml` добавляет их — применяется скриптом `deploy.sh`.

### Сравнение RFC2136 vs webhook

| Аспект | RFC2136 | Webhook |
|--------|---------|---------|
| Аутентификация | TSIG-ключ (HMAC-SHA256) | HTTPS + сертификат |
| Путь к Knot | Через TSIG-обновление зоны | Через ConfigMap (Kubernetes API) |
| Сложность | Нужен TSIG-ключ в cert-manager | Нужен TLS-сертификат + APIService |
| Безопасность | Ключ в Secret cert-manager | Сертификат под контролем build.sh |

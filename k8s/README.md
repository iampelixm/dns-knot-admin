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

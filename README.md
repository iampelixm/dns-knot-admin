# dnsadmin (источники UI + backend)

Один контейнер: статика Vue + FastAPI (uvicorn).

- **Фронт:** Vue 3 + Vite + Element Plus (`src/`), в образе — `/app/static`.
- **Бэкенд:** FastAPI (`backend/app/`), Kubernetes API (ConfigMap `knot-config`, перезапуск Deployment Knot).
- **Авторизация:** `POST /api/auth/login` → JWT (Bearer), секрет **`JWT_SECRET`** в Secret `dnsadmin-auth`.

В контейнере один процесс **uvicorn** отдаёт SPA, `/api/*` и `/health`.

## Разработка (только фронт, hot reload)

```bash
docker compose up
```

Откройте в браузере порт, который выдал Vite (часто `5173`). Прокси к API настраивается в `vite.config` под ваш локальный бэкенд.

Параллельно API локально:

```bash
cd dnsadmin-ui && npm run build && cd backend && pip install -r requirements.txt
export JWT_SECRET="$(openssl rand -hex 32)" ADMIN_USERNAME=admin ADMIN_PASSWORD=<пароль>
export STATIC_DIR="$(cd .. && pwd)/dist"
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Без собранного `dist` и без `STATIC_DIR` поднимется только REST.

## Сборка и публикация образа

Укажите **свой** реестр и имя образа. Версия в теге обычно совпадает с полем `version` в `package.json`.

```bash
export REGISTRY=<your-registry.example.com>
export IMAGE=<your-namespace/dnsadmin>
ver=$(node -p "require('./package.json').version")
docker build -t "${REGISTRY}/${IMAGE}:${ver}" .
docker push "${REGISTRY}/${IMAGE}:${ver}"
```

Скрипты автоматизации — в родительском каталоге `configs/dns-knot/scripts/`; переменные реестра — в `scripts/dnsadmin-env.sh`.

`imagePullSecrets` задаётся в манифесте Deployment (см. `k8s/80-dnsadmin-deployment.example.yaml` и `k8s/registry-pull-secret.howto.yaml`).

## Миграция со схемы «отдельно API и UI»

```bash
kubectl delete deploy dnsadmin-api dnsadmin-ui svc dnsadmin-api svc dnsadmin-ui -n dns-knot --ignore-not-found
kubectl delete cm dnsadmin-app -n dns-knot --ignore-not-found
kubectl apply -f dnsadmin-secret.yaml
kubectl apply -k ..
```

Далее соберите и запушьте образ dnsadmin и примените манифесты.

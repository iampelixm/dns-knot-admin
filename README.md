# dnsadmin (источники UI + backend)

Один образ **`registry.summersite.ru/dns-knot/dnsadmin`**:

- **Фронт:** Vue 3 + Vite + Element Plus (`src/`), в образе попадает в `/app/static`.
- **Бэкенд:** FastAPI + uvicorn (`backend/app/`), Kubernetes API (ConfigMap `knot-config`, restart Deployment `knot`).
- **Авторизация:** `POST /api/auth/login` → JWT (Bearer), секрет подписи `JWT_SECRET` в Secret `dnsadmin-auth`.

В контейнере один процесс **uvicorn** отдаёт и SPA, и `/api/*`, и `/health`.

## Разработка (только фронт, hot reload)

```bash
docker compose up
```

Откройте http://localhost:5173 — Vite проксирует `/api` и `/health` на `127.0.0.1:8080`.

Параллельно поднимите API локально:

```bash
cd dnsadmin-ui && npm run build && cd backend && pip install -r requirements.txt
export JWT_SECRET="$(openssl rand -hex 32)" ADMIN_USERNAME=admin ADMIN_PASSWORD=ваш-пароль
export STATIC_DIR="$(cd .. && pwd)/dist"
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Без собранного `dist` и без `STATIC_DIR` поднимется только REST (вход через `/api/auth/login` всё равно работает).

## Сборка и публикация образа

Рекомендуется **скрипты** из родительского каталога `configs/dns-knot`: версия берётся из `package.json` автоматически, без ввода тега вручную — см. `README.md` в `configs/dns-knot`, раздел «Скрипты релиза dnsadmin».

Вручную (контекст сборки — каталог `dnsadmin-ui`, где лежит `Dockerfile`):

```bash
docker login registry.summersite.ru
ver=$(node -p "require('./package.json').version")
docker build -t "registry.summersite.ru/dns-knot/dnsadmin:${ver}" .
docker push "registry.summersite.ru/dns-knot/dnsadmin:${ver}"
```

`imagePullSecrets` задаётся в `dnsadmin-deployment.yaml`.

## Миграция со схемы «отдельно nginx+UI и dnsadmin-api»

Один раз:

```bash
kubectl delete deploy dnsadmin-api dnsadmin-ui svc dnsadmin-api svc dnsadmin-ui -n dns-knot --ignore-not-found
kubectl delete cm dnsadmin-app -n dns-knot --ignore-not-found
kubectl apply -f dnsadmin-secret.yaml   # чтобы был ключ JWT_SECRET
kubectl apply -k ..
```

Далее соберите и запушьте образ `dnsadmin`, примените манифесты.
# dns-knot-admin

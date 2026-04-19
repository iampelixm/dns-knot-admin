# syntax=docker/dockerfile:1
# База cznic/knot — тот же knotc, что в кластере (conf-check).
FROM node:22-alpine AS frontend
WORKDIR /frontend

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit; else npm install --no-audit; fi

COPY . .
RUN npm run build

FROM cznic/knot:3.4
WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/static

COPY backend/requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /frontend/dist /app/static

EXPOSE 8080

WORKDIR /app/backend
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

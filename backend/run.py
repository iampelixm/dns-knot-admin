"""Запуск uvicorn: HTTP на 8080 + опционально HTTPS на 8443 для webhook."""

import os
import sys

import uvicorn

from app.main import app  # noqa: F401 — импорт для uvicorn

HOST = os.environ.get("UVICORN_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("UVICORN_HTTP_PORT", "8080"))
HTTPS_PORT = int(os.environ.get("UVICORN_HTTPS_PORT", "8443"))
TLS_CERT = os.environ.get("TLS_CERT_PATH", "")
TLS_KEY = os.environ.get("TLS_KEY_PATH", "")

config_http = uvicorn.Config("app.main:app", host=HOST, port=HTTP_PORT, log_level="info")
server_http = uvicorn.Server(config_http)

if TLS_CERT and TLS_KEY:
    config_https = uvicorn.Config(
        "app.main:app", host=HOST, port=HTTPS_PORT,
        ssl_certfile=TLS_CERT, ssl_keyfile=TLS_KEY, log_level="info",
    )
    server_https = uvicorn.Server(config_https)
    print(f"HTTPS on {HOST}:{HTTPS_PORT} (cert={TLS_CERT})", file=sys.stderr)
else:
    server_https = None
    print(f"No TLS cert — HTTPS disabled", file=sys.stderr)

print(f"HTTP on {HOST}:{HTTP_PORT}", file=sys.stderr)

import asyncio


async def main():
    tasks = [server_http.serve()]
    if server_https:
        tasks.append(server_https.serve())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
"""Чтение Secret AXFR из Kubernetes с пояснением ошибок."""

from __future__ import annotations

import base64
import re
import secrets
import subprocess
from dataclasses import dataclass
from typing import Any, List, Tuple

from kubernetes.client.rest import ApiException

_TSIG_KEY_ID = re.compile(r"^(?=.{1,63}$)(?!-)[a-zA-Z0-9-]+(?<!-)$")


@dataclass(frozen=True)
class AxfrSecretRead:
    """Результат чтения Secret `knot-axfr` (или аналога)."""

    content: str | None
    code: str
    message: str
    hints: List[str]
    http_status: int | None
    keys_in_data: List[str]


def _hints_for_code(
    code: str,
    *,
    namespace: str,
    secret_name: str,
    secret_key: str,
    keys_in_data: List[str],
) -> List[str]:
    if code == "ok":
        return []
    out: List[str] = []
    if code == "not_found":
        out.append(
            f"Создайте Secret, например: kubectl -n {namespace} create secret generic {secret_name} "
            f"--from-file={secret_key}=axfr.conf  (файл axfr.conf — вывод scripts/generate-axfr-tsig.sh "
            "или вкладка «AXFR» → «Сгенерировать TSIG» → сохранить)."
        )
        out.append("Пример манифеста в репозитории: k8s/20-knot-axfr-secret.example.yaml")
    elif code == "forbidden":
        out.append(
            f"Проверьте RBAC для ServiceAccount dnsadmin: kubectl auth can-i get secret/{secret_name} "
            f"--as=system:serviceaccount:{namespace}:dnsadmin -n {namespace}"
        )
        out.append("В манифесте dnsadmin-ui: k8s/60-dnsadmin-rbac.yaml — resourceNames для knot-axfr.")
    elif code == "missing_key":
        out.append(
            f"В Secret {secret_name!r} нет ключа {secret_key!r} в data. "
            f"Сейчас в .data есть: {', '.join(keys_in_data) or '(пусто)'}. "
            f"Переименуйте ключ или задайте KNOT_AXFR_SECRET_KEY."
        )
    elif code == "empty_value":
        out.append(f"Ключ {secret_key!r} в Secret есть, но после декодирования получилась пустая строка.")
    elif code == "bad_encoding":
        out.append("Значение в Secret не является корректным base64 UTF-8 текстом; пересоздайте Secret.")
    elif code == "unknown":
        out.append("Не удалось прочитать Secret (сеть API, таймаут). Смотрите логи dnsadmin / kube-apiserver.")
    return out


def read_axfr_secret(
    core: Any,
    *,
    namespace: str,
    secret_name: str,
    secret_key: str,
) -> AxfrSecretRead:
    """
    Прочитать содержимое AXFR-файла из Secret.

    `core` — kubernetes.client.CoreV1Api().
    """
    try:
        sec = core.read_namespaced_secret(secret_name, namespace)
    except ApiException as e:
        if e.status == 404:
            msg = f"Secret {secret_name!r} в namespace {namespace!r} не найден"
            return AxfrSecretRead(
                content=None,
                code="not_found",
                message=msg,
                hints=_hints_for_code(
                    "not_found",
                    namespace=namespace,
                    secret_name=secret_name,
                    secret_key=secret_key,
                    keys_in_data=[],
                ),
                http_status=404,
                keys_in_data=[],
            )
        if e.status == 403:
            msg = f"Доступ запрещён (403) к Secret {secret_name!r}"
            return AxfrSecretRead(
                content=None,
                code="forbidden",
                message=msg,
                hints=_hints_for_code(
                    "forbidden",
                    namespace=namespace,
                    secret_name=secret_name,
                    secret_key=secret_key,
                    keys_in_data=[],
                ),
                http_status=403,
                keys_in_data=[],
            )
        msg = f"Kubernetes API: {e.status or '?'} {e.reason or ''}".strip()
        return AxfrSecretRead(
            content=None,
            code="unknown",
            message=msg,
            hints=_hints_for_code(
                "unknown",
                namespace=namespace,
                secret_name=secret_name,
                secret_key=secret_key,
                keys_in_data=[],
            ),
            http_status=e.status,
            keys_in_data=[],
        )
    except OSError as e:
        return AxfrSecretRead(
            content=None,
            code="unknown",
            message=str(e),
            hints=_hints_for_code(
                "unknown",
                namespace=namespace,
                secret_name=secret_name,
                secret_key=secret_key,
                keys_in_data=[],
            ),
            http_status=None,
            keys_in_data=[],
        )

    keys_sorted = sorted((sec.data or {}).keys())
    raw_b64 = (sec.data or {}).get(secret_key)
    if not raw_b64:
        msg = f"В Secret нет ключа data[{secret_key!r}]"
        return AxfrSecretRead(
            content=None,
            code="missing_key",
            message=msg,
            hints=_hints_for_code(
                "missing_key",
                namespace=namespace,
                secret_name=secret_name,
                secret_key=secret_key,
                keys_in_data=keys_sorted,
            ),
            http_status=None,
            keys_in_data=keys_sorted,
        )
    try:
        text = base64.b64decode(raw_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return AxfrSecretRead(
            content=None,
            code="bad_encoding",
            message="Не удалось base64-декодировать или декодировать UTF-8",
            hints=_hints_for_code(
                "bad_encoding",
                namespace=namespace,
                secret_name=secret_name,
                secret_key=secret_key,
                keys_in_data=keys_sorted,
            ),
            http_status=None,
            keys_in_data=keys_sorted,
        )
    if not text.strip():
        return AxfrSecretRead(
            content=None,
            code="empty_value",
            message="Содержимое ключа после декодирования пустое",
            hints=_hints_for_code(
                "empty_value",
                namespace=namespace,
                secret_name=secret_name,
                secret_key=secret_key,
                keys_in_data=keys_sorted,
            ),
            http_status=None,
            keys_in_data=keys_sorted,
        )
    return AxfrSecretRead(
        content=text,
        code="ok",
        message="Secret прочитан",
        hints=[],
        http_status=None,
        keys_in_data=keys_sorted,
    )


def axfr_diag_public_dict(st: AxfrSecretRead, *, namespace: str, secret_name: str, secret_key: str) -> dict:
    """Словарь для JSON-ответов (без секрета)."""
    return {
        "readable": st.code == "ok",
        "code": st.code,
        "message": st.message,
        "hints": st.hints,
        "namespace": namespace,
        "secret_name": secret_name,
        "secret_key": secret_key,
        "keys_in_data": st.keys_in_data,
        "http_status": st.http_status,
    }


def validate_tsig_key_id(key_id: str) -> str:
    kid = key_id.strip()
    if not kid:
        raise ValueError("Пустой идентификатор ключа")
    if not _TSIG_KEY_ID.match(kid):
        raise ValueError(
            "Идентификатор TSIG: 1–63 символа, буквы/цифры/дефис, без дефиса в начале и в конце"
        )
    return kid


def default_tsig_key_id() -> str:
    return f"axfr-{secrets.token_hex(4)}"


def generate_tsig_yaml_fragment(
    key_id: str | None = None,
    *,
    with_acl: bool = True,
    acl_id: str = "axfr-allowed",
) -> Tuple[str, str]:
    """
    Сгенерировать YAML `key:` (+ опционально `acl:`) через `keymgr -t` из образа Knot.

    Возвращает (yaml_fragment, использованный key_id).
    """
    raw = (key_id or "").strip()
    kid = validate_tsig_key_id(raw) if raw else default_tsig_key_id()
    try:
        proc = subprocess.run(
            ["keymgr", "-t", kid, "hmac-sha256"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"keymgr не выполнен: {e}") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"keymgr завершился с кодом {proc.returncode}: {err}")

    lines: List[str] = []
    for ln in proc.stdout.splitlines():
        if ln.strip().startswith("#"):
            continue
        lines.append(ln)
    key_yaml = "\n".join(lines).strip()
    if "key:" not in key_yaml:
        raise RuntimeError(f"Неожиданный вывод keymgr: {proc.stdout!r}")

    if not with_acl:
        return key_yaml, kid

    acl_yaml = (
        f"\nacl:\n"
        f"  - id: {acl_id}\n"
        f"    action: transfer\n"
        f"    address:\n"
        f"      - 127.0.0.1\n"
        f"    key: {kid}\n"
    )
    return key_yaml + acl_yaml, kid

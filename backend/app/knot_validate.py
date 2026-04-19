"""Проверка knot.conf через knotc conf-check во временном каталоге."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.knot_yaml import parse_knot_conf, serialize_knot_conf


@dataclass
class KnotcCheckResult:
    ok: bool
    message: str
    ran: bool


def _zone_file_basenames_from_doc(doc: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(doc, dict):
        return out
    zones = doc.get("zone")
    if not isinstance(zones, list):
        return out
    for z in zones:
        if not isinstance(z, dict):
            continue
        fp = z.get("file")
        if isinstance(fp, str) and fp.strip():
            out.append(fp.strip().split("/")[-1])
    return out


def _rewrite_paths_for_check(doc: Any, tmp: Path) -> None:
    """Подмена путей file и include на файлы внутри tmp."""
    if not isinstance(doc, dict):
        return
    zones_dir = tmp / "zones"
    zones_dir.mkdir(parents=True, exist_ok=True)
    conf_d = tmp / "conf.d"
    conf_d.mkdir(parents=True, exist_ok=True)

    zones = doc.get("zone")
    if isinstance(zones, list):
        for z in zones:
            if not isinstance(z, dict):
                continue
            fp = z.get("file")
            if isinstance(fp, str) and fp.startswith("/zones/"):
                name = fp[len("/zones/") :].lstrip("/") or fp.split("/")[-1]
                z["file"] = str((zones_dir / name).resolve())

    inc = doc.get("include")
    if isinstance(inc, str):
        if inc.startswith("/etc/knot/"):
            newp = str((conf_d / Path(inc).name).resolve())
            doc["include"] = newp
    elif isinstance(inc, list):
        new_list = []
        for p in inc:
            if isinstance(p, str) and p.startswith("/etc/knot/"):
                new_list.append(str((conf_d / Path(p).name).resolve()))
            else:
                new_list.append(p)
        doc["include"] = new_list


def run_knotc_conf_check(
    knot_conf_text: str,
    zone_files: dict[str, str],
    *,
    axfr_yaml: str | None,
    knotc_bin: str | None = None,
) -> KnotcCheckResult:
    """
    zone_files: ключ «имя файла зоны» (например k3s.local.zone) -> содержимое.
    axfr_yaml: содержимое include-файла (key/acl), если None — файлы conf.d не создаются.
    """
    bin_path = knotc_bin or os.environ.get("KNOTC_BIN", "knotc")
    zdoc = parse_knot_conf(knot_conf_text)
    if not knotc_available(bin_path):
        return KnotcCheckResult(
            ok=True,
            message="knotc не найден в PATH — проверка только на уровне приложения пропущена",
            ran=False,
        )

    needs_axfr = False
    if isinstance(zdoc, dict):
        inc = zdoc.get("include")
        if isinstance(inc, str) and inc.startswith("/etc/knot/"):
            needs_axfr = True
        elif isinstance(inc, list):
            needs_axfr = any(isinstance(x, str) and x.startswith("/etc/knot/") for x in inc)

    if needs_axfr and not (axfr_yaml and axfr_yaml.strip()):
        return KnotcCheckResult(
            ok=False,
            message="В конфиге есть include из /etc/knot/, но содержимое axfr (Secret) недоступно — knotc не запускался",
            ran=False,
        )

    doc = copy.deepcopy(parse_knot_conf(knot_conf_text))
    tmp = Path(tempfile.mkdtemp(prefix="knotcheck-"))
    try:
        _rewrite_paths_for_check(doc, tmp)

        # Записать зоны из ConfigMap
        if isinstance(zdoc, dict):
            for name in _zone_file_basenames_from_doc(zdoc):
                key = name if name.endswith(".zone") else f"{name}.zone"
                content = zone_files.get(key)
                if content is None:
                    # минимальная заглушка, если файла нет в CM (conf-check может ругнуться)
                    apex = name[: -len(".zone")] if name.endswith(".zone") else name
                    content = (
                        f"$ORIGIN {apex}.\n$TTL 60\n"
                        f"@ IN SOA ns.{apex}. hostmaster.{apex}. 1 7200 900 1209600 60\n"
                        f"@ IN NS ns.{apex}.\n"
                    )
                dest = tmp / "zones" / Path(name).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

        # axfr.conf в conf.d
        conf_d = tmp / "conf.d"
        if axfr_yaml is not None and axfr_yaml.strip():
            for p in conf_d.glob("*.conf"):
                p.unlink()
            # имя файла из первого include в оригинале или axfr.conf
            inc_name = "axfr.conf"
            raw_inc = zdoc.get("include") if isinstance(zdoc, dict) else None
            if isinstance(raw_inc, str):
                inc_name = Path(raw_inc).name
            elif isinstance(raw_inc, list) and raw_inc:
                inc_name = Path(str(raw_inc[0])).name
            (conf_d / inc_name).write_text(axfr_yaml, encoding="utf-8")

        main = tmp / "knot.conf"
        main.write_text(serialize_knot_conf(doc), encoding="utf-8")

        proc = subprocess.run(
            [bin_path, "-c", str(main), "conf-check"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        msg = "\n".join(x for x in (out, err) if x)
        if proc.returncode == 0:
            return KnotcCheckResult(ok=True, message=msg or "Configuration is valid", ran=True)
        return KnotcCheckResult(ok=False, message=msg or f"knotc exited {proc.returncode}", ran=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def knotc_available(bin_path: str | None = None) -> bool:
    p = bin_path or os.environ.get("KNOTC_BIN", "knotc")
    try:
        r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

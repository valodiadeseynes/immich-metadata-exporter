"""Téléchargement d'un asset Immich et intégration des métadonnées via exiftool."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from .api import ImmichClient
from .metadata import Options, build_tags, utc_datetime

MODE_EMBED = "embed"      # métadonnées écrites dans le fichier
MODE_SIDECAR = "sidecar"  # fichier original intact + fichier .xmp à côté
MODE_BOTH = "both"

# Formats pour lesquels on n'écrit pas dans le fichier (RAW : on préfère un sidecar)
RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".nrw", ".orf", ".raf", ".rw2", ".pef", ".srw", ".dng", ".3fr", ".iiq", ".x3f"}


@dataclass
class ExportSettings:
    dest_dir: str
    mode: str = MODE_EMBED
    opts: Options = field(default_factory=Options)
    set_mtime: bool = True
    skip_existing: bool = False


@dataclass
class ExportResult:
    asset_id: str
    path: str | None = None
    ok: bool = False
    warning: str = ""
    error: str = ""


def exiftool_path() -> str | None:
    return shutil.which("exiftool")


def _unique_path(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    path = os.path.join(directory, filename)
    n = 1
    while os.path.exists(path):
        path = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return path


def _probe(path: str) -> tuple[int, tuple[int, int] | None]:
    """Lit l'orientation EXIF et les dimensions stockées du fichier."""
    try:
        out = subprocess.run(
            ["exiftool", "-j", "-n", "-Orientation", "-ImageWidth", "-ImageHeight", path],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout)[0]
    except Exception:
        return 1, None
    w, h = data.get("ImageWidth"), data.get("ImageHeight")
    try:
        orientation = int(data.get("Orientation") or 1)
    except (TypeError, ValueError):
        orientation = 1
    return orientation, ((int(w), int(h)) if w and h else None)


def _run_exiftool(tags: dict, args: list[str]) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump([{"SourceFile": "*", **tags}], f, ensure_ascii=False)
        json_path = f.name
    try:
        p = subprocess.run(
            ["exiftool", "-m", "-charset", "iptc=UTF8", f"-json={json_path}", *args],
            capture_output=True, text=True, timeout=300,
        )
        msg = (p.stderr or "").strip()
        return p.returncode == 0, msg
    finally:
        os.unlink(json_path)


def export_asset(client: ImmichClient, asset_id: str, s: ExportSettings, cancel=None) -> ExportResult:
    res = ExportResult(asset_id)
    try:
        asset = client.asset(asset_id)
        filename = os.path.basename(asset.get("originalFileName") or f"{asset_id}.bin")
        target = os.path.join(s.dest_dir, filename)
        if s.skip_existing and os.path.exists(target):
            res.ok, res.path, res.warning = True, target, "déjà présent, ignoré"
            return res
        target = _unique_path(s.dest_dir, filename)

        res.path = target
        tmp = target + ".part"
        client.download_original(asset_id, tmp, cancel=cancel)
        os.replace(tmp, target)

        faces = client.faces(asset_id) if (s.opts.write_faces or s.opts.people_as_keywords) else []
        albums = client.albums_of_asset(asset_id) if s.opts.albums_as_keywords else []
        orientation, size = _probe(target)

        ext = os.path.splitext(target)[1].lower()
        mode = s.mode
        if mode == MODE_EMBED and ext in RAW_EXT:
            mode = MODE_SIDECAR
            res.warning = "fichier RAW : métadonnées placées dans un .xmp à côté"

        warnings = []
        if mode in (MODE_EMBED, MODE_BOTH):
            tags = build_tags(asset, faces, albums, s.opts, size, orientation)
            ok, msg = _run_exiftool(tags, ["-overwrite_original", target])
            if not ok:
                # Format non inscriptible par exiftool : repli sur un sidecar
                warnings.append(f"écriture dans le fichier impossible ({msg.splitlines()[-1] if msg else '?'}), "
                                "repli sur un .xmp")
                mode = MODE_SIDECAR
        if mode in (MODE_SIDECAR, MODE_BOTH):
            xmp = target + ".xmp"
            if os.path.exists(xmp):
                os.unlink(xmp)
            tags = build_tags(asset, faces, albums, s.opts, size, orientation, xmp_only=True)
            ok, msg = _run_exiftool(tags, ["-o", xmp, target])
            if not ok:
                raise RuntimeError(f"exiftool (sidecar) : {msg}")

        if s.set_mtime:
            dt = utc_datetime(asset)
            if dt:
                ts = dt.timestamp()
                os.utime(target, (ts, ts))
                if os.path.exists(target + ".xmp"):
                    os.utime(target + ".xmp", (ts, ts))

        if warnings:
            res.warning = "; ".join(filter(None, [res.warning, *warnings]))
        res.ok = True
    except InterruptedError:
        res.error = "annulé"
        _cleanup(res)
    except Exception as e:  # noqa: BLE001
        res.error = str(e)
        _cleanup(res)
    return res


def _cleanup(res: ExportResult):
    for p in filter(None, [res.path and res.path + ".part"]):
        if os.path.exists(p):
            os.unlink(p)

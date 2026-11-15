"""Configuration (~/.config/immich-export/config.json) et stockage de la clé API.

La clé API est rangée dans le trousseau du bureau (libsecret / gnome-keyring)
si disponible ; sinon dans un fichier lisible uniquement par l'utilisateur.
"""

from __future__ import annotations

import json
import os

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "immich-export")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
KEY_FILE = os.path.join(CONFIG_DIR, "api_key")

DEFAULTS = {
    "url": "",
    "verify_tls": True,
    "dest_dir": os.path.expanduser("~/Images"),
    "mode": "embed",
    "people_as_keywords": True,
    "albums_as_keywords": False,
    "write_faces": True,
    "set_mtime": True,
    "skip_existing": False,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- clé API
def _secret():
    try:
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import Secret

        schema = Secret.Schema.new(
            "fr.local.ImmichExport", Secret.SchemaFlags.NONE, {"server": Secret.SchemaAttributeType.STRING}
        )
        return Secret, schema
    except (ImportError, ValueError):
        return None, None


def keyring_available() -> bool:
    return _secret()[0] is not None


def get_api_key(url: str) -> str:
    Secret, schema = _secret()
    if Secret:
        try:
            v = Secret.password_lookup_sync(schema, {"server": url}, None)
            if v:
                return v
        except Exception:  # noqa: BLE001 - trousseau verrouillé ou absent
            pass
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def set_api_key(url: str, key: str) -> str:
    """Enregistre la clé ; renvoie « trousseau » ou « fichier » selon l'emplacement."""
    Secret, schema = _secret()
    if Secret:
        try:
            Secret.password_store_sync(
                schema, {"server": url}, Secret.COLLECTION_DEFAULT, f"Immich Export ({url})", key, None
            )
            if os.path.exists(KEY_FILE):
                os.unlink(KEY_FILE)
            return "trousseau"
        except Exception:  # noqa: BLE001
            pass
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return "fichier"

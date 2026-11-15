"""Client minimal pour l'API REST d'Immich (testé sur la structure de l'API v3.x).

Authentification par clé API (en-tête ``x-api-key``).
Permissions nécessaires pour la clé : asset.read, asset.view, asset.download,
album.read, person.read, face.read (et server.about en option).
"""

from __future__ import annotations

import requests

PAGE_SIZE = 250


class ImmichError(Exception):
    pass


class ImmichClient:
    def __init__(self, base_url: str, api_key: str, verify_tls: bool = True, timeout: int = 30):
        url = base_url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not url.endswith("/api"):
            url += "/api"
        self.base = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key.strip(), "Accept": "application/json"})
        self.session.verify = verify_tls

    # ------------------------------------------------------------------ bas niveau
    def _request(self, method: str, path: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        try:
            r = self.session.request(method, self.base + path, **kwargs)
        except requests.RequestException as e:
            raise ImmichError(f"Connexion impossible : {e}") from e
        if r.status_code >= 400:
            msg = r.text[:300]
            try:
                body = r.json()
                msg = body.get("message", msg)
                if body.get("errors"):  # format d'erreur Zod (v3)
                    msg += " : " + "; ".join(
                        f"{'.'.join(map(str, e.get('path', [])))} {e.get('message', '')}" for e in body["errors"]
                    )
            except ValueError:
                pass
            if r.status_code == 401:
                msg = "Clé API refusée (401). " + str(msg)
            elif r.status_code == 403:
                msg = "Permission manquante sur la clé API (403). " + str(msg)
            raise ImmichError(f"{method} {path} → HTTP {r.status_code} : {msg}")
        return r

    def _get(self, path, **params):
        return self._request("GET", path, params=params or None).json()

    def _post(self, path, body):
        return self._request("POST", path, json=body).json()

    # ------------------------------------------------------------------ serveur
    def server_version(self) -> str:
        v = self._get("/server/version")
        return f"{v.get('major')}.{v.get('minor')}.{v.get('patch')}"

    # ------------------------------------------------------------------ albums
    def albums(self) -> list[dict]:
        """Albums possédés + partagés avec l'utilisateur, dédoublonnés."""
        seen: dict[str, dict] = {}
        for params in ({}, {"isShared": "true"}):
            try:
                for a in self._get("/albums", **params):
                    seen.setdefault(a["id"], a)
            except ImmichError:
                if params:  # l'appel « partagés » est facultatif
                    continue
                raise
        return sorted(seen.values(), key=lambda a: (a.get("albumName") or "").lower())

    def albums_of_asset(self, asset_id: str) -> list[str]:
        try:
            return [a.get("albumName", "") for a in self._get("/albums", assetId=asset_id)]
        except ImmichError:
            return []

    # ------------------------------------------------------------------ personnes
    def people(self) -> list[dict]:
        out, page = [], 1
        while True:
            data = self._get("/people", withHidden="false", page=page, size=500)
            out.extend(p for p in data.get("people", []) if p.get("name"))
            if not data.get("hasNextPage"):
                break
            page += 1
        return sorted(out, key=lambda p: p["name"].lower())

    # ------------------------------------------------------------------ recherche
    @staticmethod
    def _assets_page(resp: dict) -> tuple[list[dict], int | None]:
        block = resp.get("assets", {})
        items = [a for a in block.get("items", []) if a.get("visibility") != "hidden"]
        nxt = block.get("nextPage")
        return items, (int(nxt) if nxt else None)

    def search_metadata(self, page: int = 1, **filters) -> tuple[list[dict], int | None]:
        body = {"page": page, "size": PAGE_SIZE, "order": "desc", "withExif": False}
        body.update({k: v for k, v in filters.items() if v not in (None, "", [])})
        return self._assets_page(self._post("/search/metadata", body))

    def search_smart(self, query: str, page: int = 1, **filters) -> tuple[list[dict], int | None]:
        body = {"query": query, "page": page, "size": 100}
        body.update({k: v for k, v in filters.items() if v not in (None, "", [])})
        return self._assets_page(self._post("/search/smart", body))

    # ------------------------------------------------------------------ assets
    def asset(self, asset_id: str) -> dict:
        return self._get(f"/assets/{asset_id}")

    def faces(self, asset_id: str) -> list[dict]:
        """Visages de l'asset (depuis la v3, ils ne sont plus dans la réponse de l'asset)."""
        try:
            return self._get("/faces", id=asset_id)
        except ImmichError:
            return []

    def thumbnail(self, asset_id: str, size: str = "thumbnail") -> bytes:
        return self._request("GET", f"/assets/{asset_id}/thumbnail", params={"size": size}).content

    def download_original(self, asset_id: str, dest_path: str, chunk: int = 1 << 20, cancel=None) -> None:
        r = self._request("GET", f"/assets/{asset_id}/original", stream=True, timeout=(30, 600))
        with open(dest_path, "wb") as f:
            for block in r.iter_content(chunk):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError
                f.write(block)

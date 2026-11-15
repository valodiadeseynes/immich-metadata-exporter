"""Conversion des métadonnées Immich en balises exiftool (format d'import JSON).

Le résultat est un dictionnaire {"Groupe:Balise": valeur} directement utilisable
avec ``exiftool -json=fichier.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Options:
    people_as_keywords: bool = True
    albums_as_keywords: bool = False
    write_faces: bool = True


# --------------------------------------------------------------------------- dates
def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def capture_datetime(asset: dict) -> tuple[datetime | None, str | None]:
    """Renvoie (heure locale « naïve » de la prise de vue, décalage « +HH:MM » ou None).

    Immich fournit :
      - exifInfo.dateTimeOriginal : instant réel en UTC ;
      - localDateTime : heure locale exprimée comme si c'était de l'UTC.
    Le décalage horaire est la différence entre les deux.
    """
    exif = asset.get("exifInfo") or {}
    utc = _parse_iso(exif.get("dateTimeOriginal")) or _parse_iso(asset.get("fileCreatedAt"))
    local = _parse_iso(asset.get("localDateTime"))
    if local is None and utc is None:
        return None, None
    if local is None:
        return utc.replace(tzinfo=None), "+00:00"
    local_naive = local.replace(tzinfo=None, microsecond=0)
    if utc is None or not exif.get("timeZone"):
        return local_naive, None
    delta = local.replace(tzinfo=None) - utc.replace(tzinfo=None)
    minutes = round(delta.total_seconds() / 60 / 15) * 15
    if abs(minutes) > 14 * 60:
        return local_naive, None
    sign = "+" if minutes >= 0 else "-"
    m = abs(minutes)
    return local_naive, f"{sign}{m // 60:02d}:{m % 60:02d}"


def utc_datetime(asset: dict) -> datetime | None:
    exif = asset.get("exifInfo") or {}
    return _parse_iso(exif.get("dateTimeOriginal")) or _parse_iso(asset.get("fileCreatedAt"))


# --------------------------------------------------------------------------- visages
def displayed_to_stored(cx: float, cy: float, w: float, h: float, orientation: int):
    """Convertit un rectangle normalisé (centre, largeur, hauteur) exprimé dans l'image
    affichée (orientation EXIF appliquée, comme l'analyse d'Immich) vers l'image stockée,
    qui est le repère imposé par la norme MWG pour les zones de visage."""
    o = orientation if orientation in range(1, 9) else 1
    x, y = {
        1: (cx, cy),
        2: (1 - cx, cy),
        3: (1 - cx, 1 - cy),
        4: (cx, 1 - cy),
        5: (cy, cx),
        6: (cy, 1 - cx),
        7: (1 - cy, 1 - cx),
        8: (1 - cy, cx),
    }[o]
    if o >= 5:
        w, h = h, w
    return x, y, w, h


def face_regions(faces: list[dict], stored_w: int, stored_h: int, orientation: int) -> list[dict]:
    regions = []
    for f in faces:
        person = f.get("person") or {}
        name = (person.get("name") or "").strip()
        iw, ih = f.get("imageWidth") or 0, f.get("imageHeight") or 0
        if not name or not iw or not ih:
            continue
        x1, x2 = f["boundingBoxX1"], f["boundingBoxX2"]
        y1, y2 = f["boundingBoxY1"], f["boundingBoxY2"]
        cx, cy = (x1 + x2) / 2 / iw, (y1 + y2) / 2 / ih
        w, h = (x2 - x1) / iw, (y2 - y1) / ih
        x, y, w, h = displayed_to_stored(cx, cy, w, h, orientation)
        clamp = lambda v: round(min(max(v, 0.0), 1.0), 6)  # noqa: E731
        regions.append(
            {
                "Area": {"X": clamp(x), "Y": clamp(y), "W": clamp(w), "H": clamp(h), "Unit": "normalized"},
                "Name": name,
                "Type": "Face",
            }
        )
    if not regions:
        return []
    return [{"AppliedToDimensions": {"W": stored_w, "H": stored_h, "Unit": "pixel"}, "RegionList": regions}]


# --------------------------------------------------------------------------- construction
def _dedup(seq):
    out = []
    for s in seq:
        if s and s not in out:
            out.append(s)
    return out


def build_tags(
    asset: dict,
    faces: list[dict] | None = None,
    albums: list[str] | None = None,
    opts: Options | None = None,
    stored_size: tuple[int, int] | None = None,
    orientation: int = 1,
    xmp_only: bool = False,
) -> dict:
    """Construit les balises à écrire.

    xmp_only=True : uniquement des balises XMP (fichier sidecar .xmp ou format
    ne supportant pas EXIF/IPTC).
    """
    opts = opts or Options()
    exif = asset.get("exifInfo") or {}
    is_video = asset.get("type") == "VIDEO"
    t: dict = {}

    def put(key, value, xmp=False):
        if value in (None, "", []):
            return
        if xmp_only and not (xmp or key.startswith("XMP")):
            return
        t[key] = value

    # Description
    desc = (exif.get("description") or "").strip()
    if desc:
        put("XMP-dc:Description", desc)
        if not is_video:
            put("EXIF:ImageDescription", desc)
            put("IPTC:Caption-Abstract", desc)

    # Date de prise de vue
    local, offset = capture_datetime(asset)
    if local:
        d = local.strftime("%Y:%m:%d %H:%M:%S")
        put("XMP-exif:DateTimeOriginal", d + (offset or ""))
        put("XMP-photoshop:DateCreated", d + (offset or ""))
        put("XMP-xmp:CreateDate", d + (offset or ""))
        if is_video:
            utc = utc_datetime(asset)
            if utc:
                put("QuickTime:CreateDate", utc.strftime("%Y:%m:%d %H:%M:%S"))
        else:
            put("EXIF:DateTimeOriginal", d)
            put("EXIF:CreateDate", d)
            if offset:
                put("EXIF:OffsetTimeOriginal", offset)
                put("EXIF:OffsetTimeDigitized", offset)

    # GPS
    lat, lon = exif.get("latitude"), exif.get("longitude")
    if lat is not None and lon is not None:
        put("XMP-exif:GPSLatitude", lat)
        put("XMP-exif:GPSLongitude", lon)
        if not is_video:
            put("EXIF:GPSLatitude", abs(lat))
            put("EXIF:GPSLatitudeRef#", "N" if lat >= 0 else "S")
            put("EXIF:GPSLongitude", abs(lon))
            put("EXIF:GPSLongitudeRef#", "E" if lon >= 0 else "W")
        else:
            put("Keys:GPSCoordinates", f"{lat} {lon}")

    # Lieu (géocodage inverse d'Immich)
    for key_xmp, key_iptc, src in (
        ("XMP-photoshop:City", "IPTC:City", "city"),
        ("XMP-photoshop:State", "IPTC:Province-State", "state"),
        ("XMP-photoshop:Country", "IPTC:Country-PrimaryLocationName", "country"),
    ):
        val = exif.get(src)
        put(key_xmp, val)
        if not is_video:
            put(key_iptc, val)

    # Note
    rating = exif.get("rating")
    if isinstance(rating, int) and -1 <= rating <= 5:
        put("XMP-xmp:Rating", rating)

    # Mots-clés : tags Immich (hiérarchiques avec « / »)
    leaves, hier, tagslist = [], [], []
    for tag in asset.get("tags") or []:
        value = (tag.get("value") or tag.get("name") or "").strip("/")
        if not value:
            continue
        tagslist.append(value)
        hier.append(value.replace("/", "|"))
        leaves.append(value.split("/")[-1])

    names = _dedup(
        [((p.get("name") or "").strip()) for p in (asset.get("people") or [])]
        + [((f.get("person") or {}).get("name") or "").strip() for f in (faces or [])]
    )
    if names:
        put("XMP-iptcExt:PersonInImage", names)
        if opts.people_as_keywords:
            leaves += names
            hier += [f"Personnes|{n}" for n in names]
            tagslist += [f"Personnes/{n}" for n in names]

    if albums and opts.albums_as_keywords:
        albums = _dedup([a.strip() for a in albums])
        leaves += albums
        hier += [f"Albums|{a}" for a in albums]
        tagslist += [f"Albums/{a}" for a in albums]

    leaves, hier, tagslist = _dedup(leaves), _dedup(hier), _dedup(tagslist)
    if leaves:
        put("XMP-dc:Subject", leaves)
        put("XMP-lr:HierarchicalSubject", hier)
        put("XMP-digiKam:TagsList", tagslist)
        if not is_video:
            put("IPTC:Keywords", leaves)

    # Zones de visage (norme MWG, lue par digiKam, Lightroom, Immich…)
    if opts.write_faces and faces and stored_size and not is_video:
        regions = face_regions(faces, stored_size[0], stored_size[1], orientation)
        if regions:
            put("XMP-mwg-rs:RegionInfo", regions[0])

    if "IPTC:Keywords" in t or "IPTC:Caption-Abstract" in t or "IPTC:City" in t:
        t["IPTC:CodedCharacterSet"] = "UTF8"
    return t

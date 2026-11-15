# CLAUDE.md — Immich Export

Application graphique pour Debian 13 / XFCE qui
télécharge des photos et vidéos depuis Immich en intégrant dans les fichiers les métadonnées
qu'Immich garde seulement dans sa base. Statut : fonctionnel, validé par l'utilisateur (15/11/2026).

## Choix techniques

- **Python 3 + GTK 3 (PyGObject)** : GTK 3 plutôt que GTK 4, car il reprend le thème XFCE.
- **API REST d'Immich uniquement**, jamais PostgreSQL : le schéma de la base change d'une version
  à l'autre, l'API est documentée et la clé API limite les droits.
- **exiftool** (`libimage-exiftool-perl`) pour écrire les métadonnées, appelé avec un fichier
  d'import JSON : `exiftool -m -charset iptc=UTF8 -json=<fichier.json> ...`,
  avec `"SourceFile": "*"` et des clés `Groupe:Balise`.
- **requests** pour le HTTP ; travail réseau dans des threads, retour vers GTK par `GLib.idle_add`.
- **Clé API** dans le trousseau (libsecret, `gir1.2-secret-1`), sinon dans
  `~/.config/immich-export/api_key` (droits 600). Configuration : `~/.config/immich-export/config.json`.

## Version d'Immich ciblée : v3.4.1

Changements de l'API v3 pris en compte :
- `people` de l'asset ne contient plus `faces` → visages via `GET /api/faces?id=<assetId>`.
- La réponse d'un album ne contient plus `assets` → contenu via `POST /api/search/metadata` avec `albumIds`.
- Si `visibility` est omis, la recherche couvre toutes les visibilités → les assets `visibility == "hidden"`
  (vidéo des Live Photos) sont filtrés côté client.
- Validation Zod : les erreurs sont dans un tableau `errors` ; ne pas envoyer de champs inconnus
  (ex. pas de `originalFileName` dans `/search/smart`).

Endpoints utilisés (en-tête `x-api-key`) : `GET /server/version`, `GET /albums` (+ `isShared=true`,
+ `assetId=`), `GET /people`, `POST /search/metadata`, `POST /search/smart`, `GET /assets/{id}`,
`GET /faces`, `GET /assets/{id}/thumbnail?size=thumbnail` (WebP → `webp-pixbuf-loader` requis),
`GET /assets/{id}/original`.

Permissions de la clé : `asset.read`, `asset.view`, `asset.download`, `album.read`, `person.read`, `face.read`.

## Structure

```
immich_export/
  __init__.py   version, APP_ID
  __main__.py   point d'entrée (python3 -m immich_export)
  api.py        ImmichClient (pagination : assets.nextPage, PAGE_SIZE = 250)
  metadata.py   conversion Immich → balises exiftool (build_tags), dates, zones de visage
  exporter.py   téléchargement (.part puis renommage), sondage du fichier, appel exiftool, repli sidecar
  config.py     configuration + stockage de la clé API
  gui.py        fenêtre GTK 3 (albums / personnes / recherche, vignettes IconView, export)
tests/test_metadata.py   tests unitaires (sans GTK ni exiftool)
install.sh               installation utilisateur (~/.local/share, ~/.local/bin, lanceur .desktop)
immich-export.desktop
```

## Correspondance des métadonnées (metadata.py)

- Description → `XMP-dc:Description`, `EXIF:ImageDescription`, `IPTC:Caption-Abstract`.
- Date : `exifInfo.dateTimeOriginal` est l'instant UTC, `localDateTime` l'heure locale « déguisée » en UTC ;
  le décalage = différence des deux, arrondi au quart d'heure, écrit seulement si `exifInfo.timeZone` existe.
  → `EXIF:DateTimeOriginal`/`CreateDate` + `OffsetTime*`, `XMP-exif:DateTimeOriginal`, `XMP-photoshop:DateCreated`.
  Vidéos : `QuickTime:CreateDate` en UTC.
- GPS → `EXIF:GPS*` (valeur absolue + `Ref#` N/S/E/W), `XMP-exif:GPS*` (décimal signé) ; vidéos `Keys:GPSCoordinates`.
- Ville/région/pays → `XMP-photoshop:*` + IPTC.
- Note → `XMP-xmp:Rating` (-1 à 5).
- Tags Immich (`a/b/c`) → `XMP-dc:Subject` + `IPTC:Keywords` (feuilles), `XMP-lr:HierarchicalSubject` (`a|b|c`),
  `XMP-digiKam:TagsList` (`a/b/c`).
- Personnes → `XMP-iptcExt:PersonInImage`, mots-clés `Personnes/Nom` (option), zones `XMP-mwg-rs:RegionInfo` (option, images seulement).
- Albums → mots-clés `Albums/Nom` (option).
- `xmp_only=True` pour les sidecars : uniquement des balises XMP.

**Zones de visage** : Immich donne les boîtes dans l'image affichée (orientation appliquée), la norme MWG
exige le repère de l'image stockée, avant orientation EXIF. `displayed_to_stored()` fait la conversion pour
les orientations 1 à 8 (largeur/hauteur échangées pour 5 à 8). `AppliedToDimensions` = dimensions stockées
lues dans le fichier téléchargé (`exiftool -j -n -Orientation -ImageWidth -ImageHeight`).

## Comportements de l'export (exporter.py)

- Modes : `embed` (défaut), `sidecar` (`<fichier>.<ext>.xmp`, original intact), `both`.
- RAW (`RAW_EXT`) → toujours sidecar. Échec d'écriture dans le fichier → repli automatique sur sidecar (avertissement).
- Collision de nom → suffixe ` (1)`, ` (2)`… ; option « ignorer les fichiers déjà présents ».
- Option : date du fichier (mtime) = date de prise de vue.

## Tests

```sh
python3 -m unittest discover tests
```
Le flux d'export peut se tester avec un faux client et un faux `exiftool` placé en tête du PATH.

## Limites connues / pistes

- Téléchargement de l'original uniquement, pas des retouches faites dans Immich (édition non destructive v3).
- Live Photos : la vidéo associée (`livePhotoVideoId`) n'est pas téléchargée.
- Pas de zones de visage pour les vidéos.
- La « Search v2 » (`searchAssets`, v3.2) n'est pas utilisée ; `search/metadata` et `search/smart` fonctionnent en v3.4.1.

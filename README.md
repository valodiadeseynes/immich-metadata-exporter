# Immich Export
## FAIT AVEC CLAUDE
Application GTK (Debian / XFCE) qui télécharge des photos et vidéos depuis Immich
en y intégrant les métadonnées connues d'Immich, qui ne sont pas dans le fichier original.

Écrit pour Immich v3.4 (API v3). Passe uniquement par l'API REST, aucun accès à PostgreSQL.

## Installation

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-secret-1 python3-requests \
                 libimage-exiftool-perl webp-pixbuf-loader
./install.sh
```

L'application est ensuite dans le menu *Graphisme*, ou se lance avec `~/.local/bin/immich-export`.
Sans installation : `python3 -m immich_export` depuis ce dossier.

Désinstallation : `rm -rf ~/.local/share/immich-export ~/.local/bin/immich-export ~/.local/share/applications/immich-export.desktop`
(et `~/.config/immich-export` pour la configuration).

## Clé API

Dans Immich : *Paramètres du compte → Clés API → Nouvelle clé*, avec les permissions
`asset.read`, `asset.view`, `asset.download`, `album.read`, `person.read`, `face.read`.
La clé est rangée dans le trousseau du bureau (gnome-keyring via libsecret). Si aucun trousseau
n'est disponible, elle est stockée dans `~/.config/immich-export/api_key` (droits 600).

## Utilisation

1. Choisir un album, une personne, ou faire une recherche (intelligente ou par nom de fichier / dates).
2. Sélectionner les photos (Ctrl / Maj + clic, ou « Tout sélectionner »). Double-clic : ouvre la photo dans Immich.
3. Choisir le dossier de destination et les options, puis « Télécharger ».

## Métadonnées écrites

| Immich | Balises écrites |
|---|---|
| Description | `XMP-dc:Description`, `EXIF:ImageDescription`, `IPTC:Caption-Abstract` |
| Date de prise de vue + fuseau | `EXIF:DateTimeOriginal`/`CreateDate` + `OffsetTimeOriginal`, `XMP-exif:DateTimeOriginal`, `XMP-photoshop:DateCreated` |
| Position GPS | `EXIF:GPS*`, `XMP-exif:GPS*` (vidéos : `Keys:GPSCoordinates`) |
| Ville / région / pays | `XMP-photoshop:City/State/Country`, IPTC équivalents |
| Note (étoiles) | `XMP-xmp:Rating` |
| Tags (hiérarchiques) | `XMP-dc:Subject`, `IPTC:Keywords`, `XMP-lr:HierarchicalSubject`, `XMP-digiKam:TagsList` |
| Personnes | `XMP-iptcExt:PersonInImage`, zones de visage `XMP-mwg-rs:RegionInfo` (option), mots-clés `Personnes/Nom` (option) |
| Albums | mots-clés `Albums/Nom` (option) |

Les zones de visage sont recalculées dans le repère de l'image stockée, comme l'exige la norme MWG
(les images avec une orientation EXIF sont donc gérées).

Modes : écriture dans le fichier (par défaut), fichier `.xmp` à côté (original intact), ou les deux.
Les fichiers RAW reçoivent toujours un `.xmp`. Si exiftool ne sait pas écrire un format, l'application
se replie automatiquement sur un `.xmp`.

## Limites connues

- Vidéos : description, date, GPS et mots-clés sont écrits (XMP/QuickTime), pas les zones de visage.
- Live Photos : seule la photo est téléchargée, pas la courte vidéo associée.
- C'est le fichier original qui est téléchargé, pas une version retouchée dans Immich.

## Tests

```sh
python3 -m unittest discover tests
```

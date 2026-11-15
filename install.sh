#!/bin/sh
# Installe Immich Export pour l'utilisateur courant (sans droits root).
# Dépendances système à installer avant (une seule fois) :
#   sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-secret-1 python3-requests \
#                    libimage-exiftool-perl webp-pixbuf-loader
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/immich-export"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

missing=""
python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' 2>/dev/null || missing="$missing python3-gi gir1.2-gtk-3.0"
python3 -c 'import requests' 2>/dev/null || missing="$missing python3-requests"
command -v exiftool >/dev/null || missing="$missing libimage-exiftool-perl"
python3 -c 'import gi; gi.require_version("Secret","1")' 2>/dev/null || missing="$missing gir1.2-secret-1"
ls /usr/lib/*/gdk-pixbuf-2.0/2.10.0/loaders/*webp* >/dev/null 2>&1 || missing="$missing webp-pixbuf-loader"
if [ -n "$missing" ]; then
  echo "Paquets manquants :$missing"
  echo "  sudo apt install$missing"
  exit 1
fi

mkdir -p "$DEST" "$BIN" "$APPS"
rm -rf "$DEST/immich_export"
cp -r "$SRC/immich_export" "$DEST/"
cat > "$BIN/immich-export" <<EOF
#!/bin/sh
exec env PYTHONPATH="$DEST" python3 -m immich_export "\$@"
EOF
chmod +x "$BIN/immich-export"
sed "s|^Exec=.*|Exec=$BIN/immich-export|" "$SRC/immich-export.desktop" > "$APPS/immich-export.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
echo "Installé. Lancez « Immich Export » depuis le menu (Graphisme) ou avec : $BIN/immich-export"

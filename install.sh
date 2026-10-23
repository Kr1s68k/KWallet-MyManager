#!/bin/sh
# Legt den Starter fuer KWallet-MyManager im Anwendungsmenue an (nur fuer
# den aktuellen Benutzer, kein root noetig). Erneut ausfuehren, wenn der
# Ordner verschoben wurde. Entfernen: ./install.sh --remove
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/de.deck.KWalletMyManager.desktop"

if [ "${1:-}" = "--remove" ]; then
    rm -f "$DESKTOP" && echo "Starter entfernt: $DESKTOP"
    exit 0
fi

missing=""
command -v python3 >/dev/null || missing="$missing python3"
command -v gpg >/dev/null     || missing="$missing gpg"
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' 2>/dev/null \
    || missing="$missing python-gobject+GTK4"
if [ -n "$missing" ]; then
    echo "Fehlt:$missing" >&2
    exit 1
fi

mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOT
[Desktop Entry]
Type=Application
Name=KWallet-MyManager
GenericName=Passwortverwaltung
Comment=Einträge in KWallet ansehen und bearbeiten – by Kr1s68k+Claude
Exec=python3 "$DIR/app.py"
Icon=kwalletmanager
Terminal=false
Categories=Utility;Security;
StartupWMClass=de.deck.KWalletMyManager
EOT
echo "Starter angelegt: $DESKTOP"
echo "Beim ersten Start fragt die App nach dem Sicherungsschlüssel (importieren oder neu anlegen)."

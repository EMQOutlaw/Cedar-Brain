#!/bin/sh
# Install CEDAR Brain for the current user: a private venv plus a launcher in ~/.local/bin.
# No root, no system packages, no uv. Re-run to upgrade.
#   scripts/install-user.sh            install / upgrade
#   scripts/install-user.sh --service  also install + start the systemd user unit
# Also installs the Mycelium cockpit, a cedar-brain-cockpit launcher and an app-menu entry.
set -eu
SRC=$(cd "$(dirname "$0")/.." && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/cedar-brain"
VENV="$DATA/venv"
BIN="$HOME/.local/bin"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || { echo "Python 3.12+ required" >&2; exit 1; }
mkdir -p "$DATA" "$BIN"
chmod 700 "$DATA"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade "$SRC"
cp "$SRC/README.md" "$DATA/README.md"
ln -sf "$VENV/bin/cedar-brain" "$BIN/cedar-brain"
echo "installed: $BIN/cedar-brain -> $VENV/bin/cedar-brain ($("$BIN/cedar-brain" --version))"

# Cockpit: copied into place so the app does not depend on this checkout.
rm -rf "$DATA/cockpit.new"
cp -R "$SRC/cockpit" "$DATA/cockpit.new"
rm -rf "$DATA/cockpit"
mv "$DATA/cockpit.new" "$DATA/cockpit"
printf '#!/bin/sh\nexport QT_QUICK_CONTROLS_STYLE="${QT_QUICK_CONTROLS_STYLE:-Basic}"\nexec qs -n -p "%s" "$@"\n' \
    "$DATA/cockpit" > "$BIN/cedar-brain-cockpit"
chmod 755 "$BIN/cedar-brain-cockpit"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
sed "s|^Exec=.*|Exec=$BIN/cedar-brain-cockpit|" "$SRC/packaging/cedar-brain.desktop" > "$APPS/cedar-brain.desktop"
cp "$SRC/packaging/cedar-brain.svg" "$ICONS/cedar-brain.svg"
if command -v update-desktop-database >/dev/null; then update-desktop-database -q "$APPS" || true; fi
echo "installed: $BIN/cedar-brain-cockpit (app menu: CEDAR Brain)"

if [ "${1:-}" = "--service" ]; then
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT_DIR"
    cp "$SRC/packaging/cedar-brain.service" "$UNIT_DIR/cedar-brain.service"
    systemctl --user daemon-reload
    systemctl --user enable cedar-brain.service >/dev/null
    systemctl --user restart cedar-brain.service
    echo "service: $(systemctl --user is-active cedar-brain.service)"
fi

#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$APP_DIR/.venv"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/pimodoro.desktop"

echo "Installing PiModoro..."

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip

if [ -f "$APP_DIR/requirements.txt" ]; then
    "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"
fi

chmod +x "$APP_DIR/run.sh"

mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=PiModoro
Comment=Pomodoro and life scheduling
Exec=$APP_DIR/run.sh
Icon=$APP_DIR/pomo.png
Terminal=false
Type=Application
Categories=Office;Utility;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE"

echo "PiModoro installed."
echo "You can now launch PiModoro from your applications menu."

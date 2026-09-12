#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$APP_DIR/Src"
VENV="$APP_DIR/.venv"

DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/pimodoro.desktop"

FONT_DIR="$HOME/.local/share/fonts"
MIGHTY_FONT_FILE="$APP_DIR/misc/Mighty-X34Z2.ttf"
HEAD_FONT_FILE="$APP_DIR/misc/Head.ttf"
FLIGHTY_FONT_FILE="$APP_DIR/misc/Flighty.ttf"
EMOJI_FONT_FILE="$APP_DIR/misc/NotoColorEmoji-Regular.ttf"
ICON_FILE="$APP_DIR/misc/pomo.png"

echo "Installing PiModoro..."

mkdir -p "$SRC_DIR"
if [ -f "$APP_DIR/app.py" ]; then
    echo "Moving app.py into Src/..."
    mv -f "$APP_DIR/app.py" "$SRC_DIR/app.py"
fi

if [ ! -f "$SRC_DIR/app.py" ]; then
    echo "Error: Src/app.py was not found."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not installed."
    exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating Python virtual environment..."
    if ! python3 -m venv "$VENV"; then
        echo "Error: Could not create the virtual environment."
        echo "On Linux Mint/Ubuntu run: sudo apt install python3-venv"
        exit 1
    fi
fi

echo "Installing PySide6..."
"$VENV/bin/python" -m pip install PySide6

# Install bundled fonts for the current user.
mkdir -p "$FONT_DIR"

for FONT_FILE in "$MIGHTY_FONT_FILE" "$HEAD_FONT_FILE" "$FLIGHTY_FONT_FILE" "$EMOJI_FONT_FILE"; do
    if [ -f "$FONT_FILE" ]; then
        echo "Installing $(basename "$FONT_FILE")..."
        cp -f "$FONT_FILE" "$FONT_DIR/"
    else
        echo "Warning: $(basename "$FONT_FILE") was not found."
    fi
done

if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f >/dev/null 2>&1 || true
else
    echo "Warning: fontconfig is not installed; the font cache could not be refreshed."
    echo "On Linux Mint/Ubuntu: sudo apt install fontconfig"
fi

chmod +x "$APP_DIR/run.sh"

mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" <<EOF_DESKTOP
[Desktop Entry]
Name=PiModoro
Comment=Pomodoro and life scheduling
Exec=$APP_DIR/run.sh
Icon=$ICON_FILE
Terminal=false
Type=Application
Categories=Office;Utility;
StartupNotify=true
EOF_DESKTOP

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo "PiModoro installed."
echo "Launch it from your applications menu."

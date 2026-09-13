#!/bin/bash
set -e

# Run this installer from a flat folder containing:
# app.py, pimodoro_db.py, recurrence.py, the bundled fonts, and pomo.png.
PACKAGE_ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$PACKAGE_ROOT/PiModoro (Linux)"
SRC_DIR="$INSTALL_DIR/Src"
MISC_DIR="$INSTALL_DIR/misc"
VENV_DIR="$INSTALL_DIR/.venv"

DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/pimodoro.desktop"

FONT_DIR="$HOME/.local/share/fonts"
ICON_FILE="$MISC_DIR/pomo.png"

PYTHON_FILES=(
    "app.py"
    "pimodoro_db.py"
    "recurrence.py"
)

ASSET_FILES=(
    "Flighty.ttf"
    "Head.ttf"
    "Mighty-X34Z2.ttf"
    "NotoColorEmoji-Regular.ttf"
    "pomo.png"
)

echo "Installing PiModoro..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is not installed."
    exit 1
fi

# Validate the complete flat package before moving anything.
for FILE_NAME in "${PYTHON_FILES[@]}"; do
    if [ ! -f "$PACKAGE_ROOT/$FILE_NAME" ] && [ ! -f "$SRC_DIR/$FILE_NAME" ]; then
        echo "Error: $FILE_NAME was not found beside installation.sh."
        exit 1
    fi
done

for FILE_NAME in "${ASSET_FILES[@]}"; do
    if [ ! -f "$PACKAGE_ROOT/$FILE_NAME" ] && [ ! -f "$MISC_DIR/$FILE_NAME" ]; then
        echo "Error: $FILE_NAME was not found beside installation.sh."
        exit 1
    fi
done

mkdir -p "$SRC_DIR" "$MISC_DIR"

# Move the root payload into the finished application structure.
for FILE_NAME in "${PYTHON_FILES[@]}"; do
    if [ -f "$PACKAGE_ROOT/$FILE_NAME" ]; then
        echo "Moving $FILE_NAME into PiModoro (Linux)/Src/..."
        mv -f "$PACKAGE_ROOT/$FILE_NAME" "$SRC_DIR/$FILE_NAME"
    fi
done

for FILE_NAME in "${ASSET_FILES[@]}"; do
    if [ -f "$PACKAGE_ROOT/$FILE_NAME" ]; then
        echo "Moving $FILE_NAME into PiModoro (Linux)/misc/..."
        mv -f "$PACKAGE_ROOT/$FILE_NAME" "$MISC_DIR/$FILE_NAME"
    fi
done

if [ -f "$PACKAGE_ROOT/README.md" ]; then
    mv -f "$PACKAGE_ROOT/README.md" "$MISC_DIR/README.md"
fi

# Remove the empty accidental nesting created by the older installer, if present.
rmdir "$INSTALL_DIR/PiModoro (Linux)/Src" 2>/dev/null || true
rmdir "$INSTALL_DIR/PiModoro (Linux)" 2>/dev/null || true

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Creating Python virtual environment..."
    if ! python3 -m venv "$VENV_DIR"; then
        echo "Error: Could not create the virtual environment."
        echo "On Linux Mint/Ubuntu run: sudo apt install python3-venv"
        exit 1
    fi
fi

echo "Installing PySide6..."
"$VENV_DIR/bin/python" -m pip install PySide6

# Install the bundled fonts for the current Linux user.
mkdir -p "$FONT_DIR"
for FILE_NAME in "${ASSET_FILES[@]:0:4}"; do
    echo "Installing $FILE_NAME..."
    cp -f "$MISC_DIR/$FILE_NAME" "$FONT_DIR/$FILE_NAME"
done

if command -v fc-cache >/dev/null 2>&1; then
    fc-cache -f >/dev/null 2>&1 || true
else
    echo "Warning: fontconfig is unavailable, so the font cache was not refreshed."
fi

# Generate the launcher inside the installed Linux folder.
cat > "$INSTALL_DIR/run.sh" <<'EOF_RUN'
#!/bin/bash
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$APP_DIR/.venv/bin/python" "$APP_DIR/Src/app.py"
EOF_RUN
chmod +x "$INSTALL_DIR/run.sh"

mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_FILE" <<EOF_DESKTOP
[Desktop Entry]
Name=PiModoro
Comment=Pomodoro and life scheduling
Exec="$INSTALL_DIR/run.sh"
Icon=$ICON_FILE
Terminal=false
Type=Application
Categories=Office;Utility;
StartupNotify=true
EOF_DESKTOP
chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo "PiModoro installed in: $INSTALL_DIR"
echo "The original payload files were moved out of the package root."
echo "Launch PiModoro from your applications menu."

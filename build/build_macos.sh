#!/bin/bash
# PaperPilot macOS build script.
# Produces a .app bundle in dist/ using PyInstaller.
# Do NOT use --onefile — CustomTkinter requires unpacked files to load themes.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_ROOT/dist"
VENV_DIR="$PROJECT_ROOT/venv"

echo "==================================="
echo "PaperPilot macOS Build"
echo "Project root: $PROJECT_ROOT"
echo "==================================="
echo ""

cd "$PROJECT_ROOT"

# Use the project's existing venv, or create one if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo "Python: $(python3 --version)"
echo "venv: $VENV_DIR"
echo ""

# Install / refresh dependencies
echo "Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r "$PROJECT_ROOT/requirements.txt" --quiet
pip install pyinstaller --quiet
echo "Dependencies installed."
echo ""

# Locate CustomTkinter package directory for bundling theme files
CTK_DIR=$(python3 -c "import customtkinter, os; print(os.path.dirname(customtkinter.__file__))")
echo "CustomTkinter: $CTK_DIR"

# Optional app icon
ICON_ARG=""
ICON_PATH="$PROJECT_ROOT/assets/icon.icns"
if [ -f "$ICON_PATH" ]; then
    ICON_ARG="--icon $ICON_PATH"
    echo "Icon: $ICON_PATH"
else
    echo "Note: no icon.icns found in assets/ — using default PyInstaller icon."
    echo "To add one: create a 1024×1024 PNG, convert to .icns, save to assets/icon.icns"
fi
echo ""

# Build
echo "Building PaperPilot.app..."
echo ""

pyinstaller \
    --name "PaperPilot" \
    --windowed \
    --noconfirm \
    $ICON_ARG \
    --add-data "$PROJECT_ROOT/assets:assets" \
    --add-data "$CTK_DIR:customtkinter" \
    --hidden-import "customtkinter" \
    --hidden-import "darkdetect" \
    --hidden-import "packaging" \
    --hidden-import "pkg_resources" \
    --hidden-import "sqlite3" \
    --hidden-import "httpx" \
    --hidden-import "httpx._transports.default" \
    --hidden-import "openai" \
    --hidden-import "dotenv" \
    --distpath "$DIST_DIR" \
    --workpath "$SCRIPT_DIR/work" \
    --specpath "$SCRIPT_DIR" \
    "$PROJECT_ROOT/main.py"

echo ""
echo "==================================="
echo "Build complete!"
echo "Output: $DIST_DIR/PaperPilot.app"
echo "==================================="
echo ""
echo "Test with:"
echo "  open $DIST_DIR/PaperPilot.app"
echo ""
echo "To distribute:"
echo "  cd $DIST_DIR && zip -r PaperPilot-macos.zip PaperPilot.app"
echo ""
echo "First-launch note for recipients:"
echo "  Right-click → Open (first time only, to bypass Gatekeeper)"
echo "  Or: xattr -dr com.apple.quarantine PaperPilot.app"

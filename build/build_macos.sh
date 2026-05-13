#!/bin/bash
set -e

echo "==================================="
echo "PaperPilot macOS Build Script"
echo "==================================="
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$SCRIPT_DIR"
DIST_DIR="$PROJECT_ROOT/dist"

cd "$PROJECT_ROOT"

# Check for Python
echo "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found. Install Python 3.11+ from python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Found Python $PYTHON_VERSION"

# Create virtual environment if it doesn't exist
VENV_DIR="$BUILD_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r "$PROJECT_ROOT/requirements.txt"

# Install additional PyInstaller dependencies
pip install pyinstaller

# Create icon if it doesn't exist
ICON_PATH="$PROJECT_ROOT/assets/icon.icns"
if [ ! -f "$ICON_PATH" ]; then
    echo "Note: No icon.icns found at $ICON_PATH"
    echo "Using default PyInstaller icon. To use a custom icon:"
    echo "  1. Create a 1024x1024 PNG icon"
    echo "  2. Convert to .icns format"
    echo "  3. Place at $ICON_PATH"
    ICON_ARG=""
else
    ICON_ARG="--icon $ICON_PATH"
fi

# Build the application
echo ""
echo "Building PaperPilot.app..."
echo ""

pyinstaller \
    --name "PaperPilot" \
    --windowed \
    --onefile \
    $ICON_ARG \
    --add-data "$PROJECT_ROOT/assets:assets" \
    --hidden-import "customtkinter" \
    --hidden-import "pkg_resources" \
    --hidden-import "sqlite3" \
    --hidden-import "httpx" \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR/build" \
    --specpath "$BUILD_DIR" \
    "$PROJECT_ROOT/main.py"

echo ""
echo "==================================="
echo "Build Complete!"
echo "==================================="
echo ""
echo "Output: $DIST_DIR/PaperPilot"
echo ""
echo "To run the app:"
echo "  $DIST_DIR/PaperPilot"
echo ""
echo "To create a .app bundle (macOS):"
echo "  1. Build with --windowed flag creates PaperPilot.app in dist/"
echo "  2. Or use the standalone binary at $DIST_DIR/PaperPilot"
echo ""

# Check if .app was created
if [ -d "$DIST_DIR/PaperPilot.app" ]; then
    echo ".app bundle created: $DIST_DIR/PaperPilot.app"
    echo ""
    echo "To install:"
    echo "  cp -R '$DIST_DIR/PaperPilot.app' /Applications/"
fi

echo ""
echo "Build completed successfully!"
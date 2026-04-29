#!/bin/bash
# ---------------------------------------------------------------
#  CMA Editor - one-click launcher (macOS / Linux)
#  First run installs all dependencies automatically.
# ---------------------------------------------------------------

set -e
cd "$(dirname "$0")"

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 is not installed."
    echo "  macOS:  brew install python"
    echo "  Ubuntu: sudo apt install python3 python3-venv"
    exit 1
fi

# Create venv if needed
if [ ! -f ".venv/bin/python" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Install on first run
if [ ! -f ".venv/.installed" ]; then
    echo "Installing Python packages (this takes 1-2 min the first time)..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo "Installing Chromium for Patchright (200MB download, one time only)..."
    python -m patchright install chromium
    touch .venv/.installed
fi

echo ""
echo "====================================================="
echo "  CMA Editor is starting..."
echo "  Open http://localhost:8000 in your browser"
echo "  Press Ctrl+C to stop"
echo "====================================================="
echo ""

python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

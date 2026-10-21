#!/usr/bin/env bash
# Run on macOS from the repo root. Produces dist/ThumbCutter.app
set -e
python3 -m pip install -r requirements-build.txt
python3 -m PyInstaller --noconfirm ThumbCutter.spec
echo "Done: dist/ThumbCutter.app  (first launch: right-click > Open, the app is unsigned)"

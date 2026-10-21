@echo off
REM Run on Windows from the repo root. Produces dist\ThumbCutter\ThumbCutter.exe
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm ThumbCutter.spec
echo Done: dist\ThumbCutter\ThumbCutter.exe  (Windows SmartScreen may warn, the app is unsigned)
pause

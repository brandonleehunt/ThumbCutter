# PyInstaller spec. Build with:  pyinstaller --noconfirm ThumbCutter.spec
import sys
from PyInstaller.utils.hooks import collect_all

datas = [("assets", "assets")]
binaries, hiddenimports = [], ["PIL._tkinter_finder"]
try:
    d, b, h = collect_all("tkinterdnd2")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass  # drag-and-drop is optional

a = Analysis(["app.py"], pathex=["."], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ThumbCutter",
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ThumbCutter")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="ThumbCutter.app", bundle_identifier="com.thumbcutter.app")

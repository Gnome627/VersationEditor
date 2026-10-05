#!/usr/bin/env python3
"""Build VersationEditor.exe as a single file.

    python build_exe.py            # dist/VersationEditor.exe
    python build_exe.py --install  # also copy it to the game root (one level up)
    python build_exe.py --console  # keep the console for debugging

Unused Qt modules are excluded to keep the size down; shiboken6 must stay.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "VersationEditor"

HIDDEN = ["ve.tab_dialogs", "ve.tab_quests", "ve.tab_events", "ve.preview", "ve.tab_models", "ve.glview", "ve.skinned", "ve.skinned_ui", "ve.appicon",
          "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PIL.DdsImagePlugin", "PIL.PngImagePlugin",
          "PIL.TgaImagePlugin"]
EXCLUDE = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtSvgWidgets", "PySide6.QtNetworkAuth",
    "PySide6.QtNetwork", "scipy", "numpy", "matplotlib", "pandas", "tkinter", "PyQt5", "PyQt6", "PySide2",
    "IPython", "pytest", "mcp", "notebook",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=f"собрать {NAME}.exe")
    ap.add_argument("--console", action="store_true", help="оставить консоль (для отладки)")
    ap.add_argument("--install", action="store_true", help="скопировать exe в корень игры")
    a = ap.parse_args()
    for mod in ("PyInstaller", "PySide6", "PIL"):
        try:
            __import__(mod)
        except ImportError:
            print(f"!! нужен модуль {mod}: pip install -r requirements.txt pyinstaller")
            return 1
    build, dist = HERE / "build", HERE / "dist"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--name", NAME,
           "--distpath", str(dist), "--workpath", str(build), "--specpath", str(build), "--paths", str(HERE)]
    cmd += [] if a.console else ["--windowed"]
    sys.path.insert(0, str(HERE))
    from ve import appicon                  # the icon is drawn in code: no binary file in the repository
    cmd += ["--icon", str(appicon.save_ico(build / "icon.ico"))]
    for m in HIDDEN:
        cmd += ["--hidden-import", m]
    for m in EXCLUDE:
        cmd += ["--exclude-module", m]
    cmd.append(str(HERE / "main.py"))
    r = subprocess.run(cmd)
    if r.returncode:
        return r.returncode
    exe = dist / f"{NAME}.exe"
    print(f"готово: {exe} ({exe.stat().st_size / 1e6:.0f} МБ)")
    if a.install:
        root = HERE.parent
        if (root / "data" / "maps").is_dir():
            shutil.copy2(exe, root / exe.name)
            print(f"скопировано в {root / exe.name}")
        else:
            print("!! уровнем выше нет data/maps — копию в игру не кладу")
    return 0


if __name__ == "__main__":
    sys.exit(main())

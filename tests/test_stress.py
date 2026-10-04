"""Zoom plus scene edits: this used to crash (stale QGraphicsScene index).

Saves nothing.    python tests/test_stress.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QPointF, Qt        # noqa: E402
from PySide6.QtGui import QWheelEvent                  # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

import ve.tab_events as te                             # noqa: E402
from ve.app import MainWindow, make_app, open_game     # noqa: E402


def main():
    q = make_app()
    app = open_game(q)
    w = MainWindow(app)
    w.resize(1440, 880)
    w.show()
    q.processEvents()

    def wheel(view, d, n=1):
        vp = view.viewport()
        c = QPointF(vp.rect().center())
        for _ in range(n):
            QApplication.sendEvent(vp, QWheelEvent(c, vp.mapToGlobal(c), QPoint(0, 0), QPoint(0, d), Qt.NoButton,
                                                   Qt.NoModifier, Qt.NoScrollPhase, False))
        q.processEvents()
        w.grab()

    # map: zoom in, add, zoom out, delete, switch map, undo
    w.show_tab(2)
    t = w.tabs[2]
    names = iter(f"stress_loc_{i}" for i in range(100))
    te.ask_text = lambda *a, **k: next(names)
    te.confirm = lambda *a, **k: True
    maps = [m for m in ("r1m1", "r1m2") if m in app.game.maps()]
    for i in range(4):
        wheel(t.view, 120, 4)
        t._create_at("loc", *t.map_center())
        wheel(t.view, -120, 6)
        town = t.map.add_town(t.map.unique("stress_town"), next(iter(app.towns.protos)), 800.0 + i, 800.0, "1008")
        t.view.rebuild()
        t.select(town)
        wheel(t.view, 120, 3)
        t._delete(town)
        wheel(t.view, -120, 2)
        t.open_map(maps[i % len(maps)])
        wheel(t.view, 120, 2)
        app.game.commit()
        w._history(app.game.undo)
        wheel(t.view, -120, 2)

    # graph: zoom in, add a reply, delete, switch folder
    w.show_tab(0)
    d = w.tabs[0]
    folders = [f for f in app.dialogs.folders if app.dialogs.in_folder(f)][:6]
    for f in folders:
        d.goto(f)
        wheel(d.graph, 120, 3)
        first = app.dialogs.in_folder(f)[0].get("name")
        d.graph.add_reply(first)
        wheel(d.graph, -120, 5)
        d.text.setPlainText("длинный текст " * 12)
        wheel(d.graph, 120, 2)
        d.graph.delete_selection()
        wheel(d.graph, -120, 2)
    assert app.game.dirty_docs()
    print("OK")


if __name__ == "__main__":
    main()

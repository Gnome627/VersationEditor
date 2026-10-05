"""Small usability guards, on a COPY of game files: dead-end replies, block clipboard, cutscenes among
triggers, map zoom after a path.

    python tests/test_usability.py
"""
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

import test_ui                                   # noqa: E402
from ve import lua                               # noqa: E402


def test():
    test_ui.COPY.extend(["data/maps/r1m1/camera_paths.xml", "data/maps/r1m1/strings.xml"])
    root = test_ui.make_copy()
    try:
        from PySide6.QtWidgets import QMessageBox
        from ve.app import MainWindow, make_app, open_game
        from ve.widgets import BlockList

        def no_dialog(box):
            raise AssertionError("dialog: " + box.text())
        QMessageBox.exec = no_dialog
        qapp = make_app()
        app = open_game(qapp, root)
        win = MainWindow(app)
        win.show()

        # --- a reply that leads nowhere and does not end the conversation ---
        d = app.dialogs
        dt = win.tabs[0]
        folder = d.folder_of[next(iter(d.items))]
        name = d.unique_name("ve_end_test")
        d.add(name, "NPC", folder, "Тест")
        assert d.unfinished(name)
        dt.graph.show_folder(folder, name)
        dt._select(name)
        assert not dt.end_btn.isHidden()
        dt._finish(name)
        assert not d.unfinished(name) and "EndConversation()" in d.items[name].get("scriptResult")
        assert dt.end_btn.isHidden()
        kid = d.unique_name("ve_end_kid")
        d.add(kid, "PLAYER", folder, "Ответ")
        d.set(name, "scriptResult", "")
        d.link(name, kid)
        assert not d.unfinished(name) and d.unfinished(kid)       # answers follow: only the last one is a dead end
        originals = sum(d.unfinished(n) for n in d.items)
        assert originals < 40                                       # the stock game ends nearly every branch

        # --- copy / cut / paste of blocks ---
        bl = BlockList(app.index, "act", "trigger")
        bl.set_blocks(lua.parse_actions('TActivate("a")\nTDeactivate("b")'))
        bl._copy(0)
        bl._paste(1)
        assert len(bl.blocks) == 3 and bl.blocks[2] == bl.blocks[0] and bl.blocks[2] is not bl.blocks[0]
        bl._copy(1, cut=True)
        assert len(bl.blocks) == 2
        other = BlockList(app.index, "act", "trigger")
        other.set_blocks([])
        other._paste()
        assert 'TDeactivate' in lua.render_actions(other.blocks) and len(other.blocks) == 1, lua.render_actions(other.blocks)
        cond = BlockList(app.index, "cond", "dialog")
        cond.set_blocks([])
        cond._paste()
        assert cond.blocks == []                                    # actions do not paste into conditions

        # --- cutscenes are listed among the triggers ---
        win.show_tab(2)
        et = win.tabs[2]
        assert set(et.mode_btns) == {"places", "trigs", "paths"}
        m = et.map
        et.set_mode("trigs")
        scenes = m.cutscenes()
        assert scenes
        tops = [et.tm.item(r) for r in range(et.tm.rowCount())]
        folded = sum(it.rowCount() for it in tops)
        assert folded > 0 and len(tops) + folded == len(m.triggers())
        start = next(s for s in scenes if m.family(s))
        et.select(start)
        assert et.mode == "trigs" and et.stack.currentIndex() == 4          # timeline
        et._as_trigger(start, start)
        assert et.stack.currentIndex() == 3                                 # the same trigger, plain
        et._as_trigger(None, start)
        assert et.stack.currentIndex() == 4
        # one switch shows the whole script as text, for a cutscene too
        et._toggle_code(True)
        assert et.stack.currentIndex() == 3 and not et.t_code.isHidden() and et.t_blocks.isHidden()
        assert "StartCinematic" in et.t_code.toPlainText()
        et._toggle_code(False)
        assert et.stack.currentIndex() == 4
        # dropdowns: picking the current item again (a double click does that) rebuilds nothing;
        # a real change is reported once, after the popup is gone
        from ve.widgets import Choice
        ev = et.t_ev
        et._as_trigger(start, start)
        fills = []
        real_fill = ev._fill
        ev._fill = lambda: (fills.append(1), real_fill())[1]
        box = next(ev.lay.itemAt(0).layout().itemAt(i).widget() for i in range(3)
                   if isinstance(ev.lay.itemAt(0).layout().itemAt(i).widget(), Choice))
        box.activated.emit(box.currentIndex())
        box.activated.emit(box.currentIndex())
        qapp.processEvents()
        assert not fills
        other = (box.currentIndex() + 1) % box.count()
        keep_events = [dict(e) for e in ev.events]
        box.setCurrentIndex(other)
        box.activated.emit(other)
        assert not fills                    # not from inside the click
        for _ in range(3):
            qapp.processEvents()
        assert len(fills) == 1
        m.set_events(start, keep_events)
        ev._fill = real_fill
        et._as_trigger(None, start)
        part = m.family(start)[0][2]
        et.select(part)
        assert et.stack.currentIndex() == 3 and et.cur is part

        # --- looking at a path does not leave the map zoomed ---
        v = et.view
        v.resize(700, 600)
        v.fit()
        before = v.transform().m11()
        cams = m.paths("cam")
        if cams:
            et.set_mode("paths")
            et.select_path("cam", cams[0])
            assert abs(v.transform().m11() - before) < 1e-9         # picking a path never zooms
            et.set_mode("places")
            assert abs(v.transform().m11() - before) < 1e-9
        assert not app.game.dirty_docs() or win.save()
        win.close()
        print("usability: ok")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test()
    print("OK")

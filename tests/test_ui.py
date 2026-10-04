"""End-to-end window test on a COPY of game files: edits, saving, undo.

Runs on the Qt offscreen platform; the real data/ is not touched.

    python tests/test_ui.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from ve import lua, xmlrt                       # noqa: E402
from ve.game import find_root                   # noqa: E402

COPY = ["data/if/diz", "data/gamedata/quests.xml", "data/gamedata/gameobjects/towns.xml",
        "data/gamedata/gameobjects/questitems.xml", "data/gamedata/gameobjects/wares.xml",
        "data/if/ico/modelicons.xml", "data/if/ico_hd/modelicons.xml", "data/if/strings", "data/if/map/r1m1.dds",
        "data/maps/r1m1/dynamicscene.xml", "data/maps/r1m1/triggers.xml", "data/maps/r1m1/cinematriggers.xml",
        "data/maps/r1m1/object_names.xml", "data/maps/r1m1/displace.bin",
        "data/maps/r1m2/dynamicscene.xml", "data/maps/r1m2/triggers.xml", "data/maps/r1m2/object_names.xml"]


def make_copy() -> Path:
    src = find_root(HERE)
    dst = Path(tempfile.mkdtemp(prefix="versation_test_"))
    for rel in COPY + [str(p.relative_to(src)).replace("\\", "/") for p in (src / "data/if/frames").glob("*_hd")]:
        s, d = src / rel, dst / rel
        if s.is_dir():
            shutil.copytree(s, d)
        elif s.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    return dst


def read(root, rel):
    return (root / rel).read_bytes().decode("cp1251")


def main():
    root = make_copy()
    os.environ["VERSATION_GAME_ROOT"] = str(root)
    from ve.app import MainWindow, make_app, open_game
    qapp = make_app()
    app = open_game(qapp, root)
    win = MainWindow(app)
    win.resize(1440, 880)
    win.show()
    pe = qapp.processEvents
    pe()
    g = app.game
    assert not g.dirty_docs()

    # --- dialogs: new branch with a loop, conditions and results as blocks ---
    dt = win.tabs[0]
    dt.goto("Root/r1m1/Опциональные/Казуал в деревне про Госпиталь", "Medik_hellodlg0")
    pe()
    gv = dt.graph
    n0 = len(app.dialogs.items)
    gv.add_reply("Medik_hellodlg0")                    # player answer
    pl = gv.current()
    assert app.dialogs.items[pl].get("role") == "PLAYER" and pl in app.dialogs.next_of("Medik_hellodlg0")
    dt.text.setPlainText("Скажи ещё раз")
    app.dialogs.link(pl, "Medik_hellodlg0")            # back to the start: a loop
    gv.show_folder(gv.folder, pl)                      # layout with a loop does not hang
    pe()
    assert any(e.back for e in gv.edges)
    dt.cond._add(lua.new_block(lua.COND_BY["money"]))
    dt.res._add(lua.new_block(lua.ACT_BY["trigger"]))
    dt.res.blocks[0]["trigger"] = "HOSPITAL"
    dt.res.changed.emit()
    dt.res._add(lua.new_block(lua.ACT_BY["end"]))
    el = app.dialogs.items[pl]
    assert el.get("scriptCondition") == "GetPlayerMoney() >= 100", el.get("scriptCondition")
    assert el.get("scriptResult") == "TActivate('HOSPITAL'); EndConversation()", el.get("scriptResult")
    dt.name.setText("Medik_again")
    dt._rename()
    assert "Medik_again" in app.dialogs.next_of("Medik_hellodlg0") and dt.cur == "Medik_again"
    assert len(app.dialogs.items) == n0 + 1

    # the preview walks the loop without hanging
    from ve.preview import PreviewDialog
    pv = PreviewDialog(app, "Medik_hellodlg0", win)
    for _ in range(3):
        btns = [pv.options.itemAt(i).widget() for i in range(pv.options.count())]
        again = [b for b in btns if hasattr(b, "click") and b.text() == "Скажи ещё раз"]
        assert again, [getattr(b, "text", lambda: "")() for b in btns]
        again[0].click()
        pe()
        if pv.world.ended:
            break
    assert "TActivate" in pv.log.toPlainText() or "Триггер" in pv.log.toPlainText(), pv.log.toPlainText()
    pv.close()

    # --- quests ---
    qt = win.tabs[1]
    win.show_tab(1)
    app.quests.add("ve_ui_Quest", "Root/r1m1", brief="Проверка окна")
    qt.goto("ve_ui_Quest")
    qt.full.setPlainText("Описание — с тире и «ёлочками».")
    qt.auto.click()
    qt.prec.set_values(["Buyer_Quest1"])
    qt.prec.changed.emit()
    qt._cond("any", "taken")
    qt.markers.set_rows([("r1m1", "TheTown")])
    qt.markers.changed.emit()
    qt.hooks["OnComplete"]._add(lua.new_block(lua.ACT_BY["money"]))
    app.quests.add("ve_ui_Sub", parent="ve_ui_Quest", brief="Шаг")
    qt.goto("ve_ui_Quest")
    assert qt.sub.isVisibleTo(qt)
    q = app.quests.items["ve_ui_Quest"]
    assert (q.get("Automatic"), q.get("ConditionToGive"), q.get("PrecedingQuests"), q.get("OnComplete")) == \
        ("1", "any taken", "Buyer_Quest1", "AddPlayerMoney(100)"), q.attrs

    # --- environment ---
    et = win.tabs[2]
    win.show_tab(2)
    pe()
    m = et.map
    assert m.name == "r1m1"
    loc = m.add_location("ve_ui_loc", 2100.0, 2050.0, 35.0)
    et.view.rebuild()
    et.select(loc)
    et.view.moved.emit(loc, 2200.0, 2100.0)
    et.view.resized.emit(loc, 55.0)
    y = g.height("r1m1", 2200.0, 2100.0)
    assert loc.get("Pos") == f"2200.000 {y:.3f} 2100.000" and loc.get("Radius") == "55.000", loc.attrs
    npc = m.add_npc(loc, "ve_ui_npc")
    et.select(npc)
    et.n_full.setText("Проверяющий")
    et._full(et.n_full)
    et.n_hello.set_values(["Medik_again"])
    et.n_hello.changed.emit()
    assert npc.get("helloReplyNames") == "Medik_again" and m.full_name("ve_ui_npc") == "Проверяющий"
    t = m.add_trigger("ve_ui_trigger")
    m.set_events(t, [{"eventid": "GE_OBJECT_ENTERS_LOCATION", "ObjName": "ve_ui_loc"}])
    et.select(t)
    et.t_active.click()
    et.t_blocks._add(lua.new_block(lua.ACT_BY["quest"]))
    et.t_blocks.blocks[0]["quest"] = "ve_ui_Quest"
    et.t_blocks.changed.emit()
    assert t.get("active") == "1"
    assert 'TakeQuest("ve_ui_Quest")' in m.script(t) and "trigger:Deactivate()" in m.script(t), m.script(t)
    # renaming a location drags the trigger event along
    et.select(loc)
    et.p_name.setText("ve_ui_loc2")
    et._rename_obj()
    assert m.events(t)[0]["ObjName"] == "ve_ui_loc2"
    town = m.add_town("ve_ui_town", "TownSouth", 900.0, 900.0, "1008", "Проверочный")
    app.towns.clone("TownSouth", "ve_ui_proto")
    m.set_attr(town, "Prototype", "ve_ui_proto")
    et.view.rebuild()
    et.select(town)
    pe()

    # --- saving ---
    dirty = sorted(d.rel for d in g.dirty_docs())
    print("изменено:", ", ".join(Path(d).name for d in dirty))
    g.commit()
    assert win.save(), "сохранение не прошло"
    assert not g.dirty_docs()
    for rel in dirty:
        xmlrt.parse(read(root, rel))                              # readable as cp1251
        assert (root / "VersationEditor_backup" / rel).is_file(), f"нет бэкапа {rel}"
    assert 'name="Medik_again"' in read(root, "data/if/diz/dialogsglobal.xml")
    assert "Описание — с тире и «ёлочками»." in read(root, "data/if/diz/questinfoglobal.xml")
    assert '"ve_ui_proto"' in read(root, "data/gamedata/gameobjects/towns.xml")
    assert 'id="ve_ui_proto"' in read(root, "data/if/ico/modelicons.xml")
    assert 'FullName="ПРОВЕРОЧНЫЙ"' in read(root, "data/maps/r1m1/object_names.xml")

    # a reopened game sees the same
    from ve.app import App
    from ve.game import Game
    app2 = App(Game(root))
    assert app2.dialogs.next_of("Medik_again") == ["Medik_hellodlg0"]
    assert app2.quests.parent_of("ve_ui_Sub") == "ve_ui_Quest"
    m2 = app2.map("r1m1")
    assert m2.kind(m2.objects["ve_ui_town"]) == "town" and [n.get("Name") for n in m2.npcs(m2.objects["ve_ui_loc2"])] == ["ve_ui_npc"]

    # --- undo ---
    before = app.dialogs.items["Medik_again"].get("text")
    win.show_tab(0)
    dt.goto(app.dialogs.folder_of["Medik_again"], "Medik_again")
    dt.text.setPlainText("Другой текст")
    g.commit()
    assert app.dialogs.items["Medik_again"].get("text") == "Другой текст"
    win._history(g.undo)
    assert app.dialogs.items["Medik_again"].get("text") == before and not g.dirty_docs()
    win._history(g.redo)
    assert app.dialogs.items["Medik_again"].get("text") == "Другой текст" and g.dirty_docs()
    win._history(g.undo)
    # undo of a deletion
    app.dialogs.delete("Medik_again")
    g.commit()
    dt.reload()
    assert "Medik_again" not in app.dialogs.items
    win._history(g.undo)
    assert "Medik_again" in app.dialogs.items and "Medik_again" in dt.graph.nodes

    shutil.rmtree(root, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()

"""Compound blocks and cutscenes: originals, new team, new cutscene, paths, messages.

The first part only reads real game files; the second works on a temp copy.

    python tests/test_scene.py
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

from ve import lua, scene, xmlrt                 # noqa: E402
from ve.game import find_root                    # noqa: E402


def norm(blocks):
    """Blank lines inside raw lua lose trailing spaces on write; meaning is unchanged."""
    out = []
    for b in blocks:
        b = dict(b)
        if "code" in b:
            b["code"] = "\n".join(l.rstrip() for l in b["code"].split("\n"))
        if "ops" in b:
            b["ops"] = norm(b["ops"])
        out.append(b)
    return out


def originals():
    root = find_root(HERE)
    n = kinds = 0
    count = {}
    for f in sorted((root / "data/maps").glob("r?m?/*riggers.xml")):
        tree = xmlrt.parse(f.read_bytes().decode("cp1251", errors="replace"))
        for t in tree.iter("trigger"):
            s = t.find("script")
            if s is None:
                continue
            b1 = lua.parse_actions(s.text)
            out = lua.render_actions(b1, quote='"', multiline=True)
            b2 = lua.parse_actions(out)
            assert norm(b1) == norm(b2), (f.name, t.get("Name"))
            assert "<" not in out.replace("<=", ""), t.get("Name")
            n += 1
            for b in b1:
                count[b["k"]] = count.get(b["k"], 0) + 1
    print(f"оригинал: {n} триггеров разобраны и записаны без изменения смысла")
    print("  блоков:", ", ".join(f"{k} {count[k]}" for k in ("obj", "team", "fly", "flylinked", "flyaround", "model",
                                                               "vehicle", "cinemsg", "lua")))
    assert count["team"] >= 50 and count["obj"] >= 700 and count["flyaround"] >= 30


def editing():
    import test_ui
    test_ui.COPY += ["data/maps/r1m1/camera_paths.xml", "data/maps/r1m1/external_paths.xml",
                     "data/maps/r1m1/strings.xml", "data/gamedata/gameobjects/vehicles.xml",
                     "data/gamedata/gameobjects/tactics.xml"]
    root = test_ui.make_copy()
    from ve.app import App, MainWindow, make_app, open_game
    from ve.cutscene_ui import add_member
    from ve.game import Game
    qapp = make_app()
    app = open_game(qapp, root)
    win = MainWindow(app)
    win.resize(1500, 950)
    win.show()
    win.show_tab(2)
    qapp.processEvents()
    et = win.tabs[2]
    m = et.map
    import ve.tab_events as te
    answers = iter(["ve_Rolik", "ve_cam01", "ve_drive01"])
    te.ask_text = lambda *a, **k: next(answers)

    # --- team with per-vehicle setup ---
    t = m.add_trigger("ve_spawn")
    et.set_mode("trigs")
    et.select(t)
    bl = et.t_blocks
    bl._add(scene.new_block("team"))
    team = next(b for b in bl.blocks if b["k"] == "team")
    team.update(name="ve_Gang", belong="1002", pos="2100.000 0 2050.000", protos=["Sml101", "Sml201"],
                walk="2200.000 0 2100.000", wares="1")
    from ve import scene_ui
    scene_ui._tune(bl, bl.blocks.index(team), team, "ve_Gang_vehicle_1")
    car = next(b for b in bl.blocks if b["k"] == "obj")
    car["ops"] += [{"k": "skin", "n": "2"}, {"k": "rot", "rot": scene.yaw_quat(90)},
                   {"k": "mod", "stat": "maxhp", "val": "= 2500"}, {"k": "mod", "stat": "hp", "val": "= 2500"},
                   {"k": "immortal", "on": "1"}, {"k": "gun", "gun": "hornet01", "n": "1", "cls": "5"}]
    scene_ui._tune(bl, bl.blocks.index(team), team, "ve_Gang")
    gang = [b for b in bl.blocks if b["k"] == "obj"][-1]
    gang["ops"] += [{"k": "tactic", "name": "TeamTacticTerroristsR1M1"}, {"k": "adjust"}]
    bl.changed.emit()
    code = m.script(t)
    print(code.replace("\r\n", "\n").replace("\t\t\t", "    "))
    for want in ('TeamCreate("ve_Gang", 1002, CVector(2100.000, 0, 2050.000), {"Sml101", "Sml201"}, '
                 'CVector(2200.000, 0, 2100.000), 1)',
                 'local obj = GetEntityByName("ve_Gang_vehicle_1")', "obj:SetSkin(2)", 'obj:AddModifier("maxhp", "= 2500")',
                 'AddVehicleGunsWithRandomAffix(obj, "hornet01", 1, 5)', "obj:setImmortalMode(1)",
                 'obj:SetProperty("TeamTacticPrototype", "TeamTacticTerroristsR1M1")', "obj:_AdjustBehaviour()",
                 "trigger:Deactivate()"):
        assert want in code, want
    for x, y in zip(norm(lua.parse_actions(code)), norm(bl.blocks)):
        assert x == y, (x, y)
    # renaming the team drags vehicle blocks along; removing a vehicle shifts indices
    team["name"] = "ve_Gang"
    scene_ui._drop_vehicle(bl, team, 0)
    assert car["ref"] == "ve_Gang_vehicle_0" and team["protos"] == ["Sml201"]
    et.handle_request("die", "ve_Gang")
    die = m.trigger("trve_GangDie")
    assert die is not None and m.events(die) == [{"eventid": "GE_OBJECT_DIE", "ObjName": "ve_Gang"}]
    assert 'TActivate("trve_GangDie")' in m.script(t)

    # --- new cutscene ---
    et._new_cutscene()
    start = m.trigger("ve_Rolik")
    assert et.mode == "scenes" and et.stack.currentIndex() == 4
    fam = m.family(start)
    assert [k for k, _a, _t in fam] == ["end"]
    fade = add_member(m, start, "fade", "")
    sb = lua.parse_actions(m.script(start))
    sb.insert(next(i for i, b in enumerate(sb) if b["k"] == "cinestart"), {"k": "cinemsg", "id": m.new_message_id(), "delay": "0.5"})
    m.set_blocks(start, sb)
    mid = next(b["id"] for b in sb if b["k"] == "cinemsg")
    m.set_message(mid, "Проверяющий", "Засада — всем стоять.", "5", "r1_man")
    add_member(m, start, "msg", mid)
    m.set_blocks(fade, [{"k": "vehicle", "proto": "Sml101", "name": "ve_Actor", "pos": "2150.000 300.000 2060.000",
                         "belong": "1002", "var": "", "local": True},
                        {"k": "selfoff"}])
    et.scene_view.rebuild()
    qapp.processEvents()
    print(m.script(start).replace("\r\n", "\n").replace("\t\t\t", "    "))
    end = next(t for k, _a, t in m.family(start) if k == "end")
    print(m.script(end).replace("\r\n", "\n").replace("\t\t\t", "    "))
    s = m.script(start)
    for want in ("SaveAllToleranceStatus(RS_NEUTRAL)", "local camObj = GetPlayerVehicle()", "FlyAround(1, 0, 25, 6, camPos, camObj:GetId(), 1, 1)",
                 "StartCinematic()", f'TActivate("{end.get("Name")}")', f'TActivate("{fade.get("Name")}")'):
        assert want in s, want
    e = m.script(end)
    assert "RestoreAllToleranceStatus()" in e and "SetCameraBehindPlayerVehicle()" in e and f'TDeactivate("{fade.get("Name")}")' in e
    assert [k for k, _a, _t in m.family(start)] == ["fade", "msg", "end"]
    # link blocks are hidden in the timeline but stay in the list
    start_list = et.scene_view.lists[0][1]
    assert any(b["k"] == "trigger" for b in start_list.blocks)
    assert not any(start_list.hidden(b) is False and b["k"] in ("trigger", "selfoff") for b in start_list.blocks)
    assert not et.scene_view._warnings(m.family(start))

    # --- paths ---
    et._new_path("cam")
    pv = et.path_view
    pv._add_at(2100.0, 2000.0)
    pv._add_at(2120.0, 2010.0)
    pv.move_point(0, "look", 2150.0, 2060.0)
    pts = m.path_points("cam", "ve_cam01")
    assert len(pts) == 2 and abs(pts[0]["y"] - (app.game.height("r1m1", 2100.0, 2000.0) + 6.0)) < 0.01
    lx, lz = pv.pts[0]["lx"], pv.pts[0]["lz"]
    assert abs(lx - 2150.0) < 0.01 and abs(lz - 2060.0) < 0.01
    from ve.model import quat_look
    bx, bz = quat_look((pts[0]["x"], pts[0]["y"], pts[0]["z"]), pts[0]["rot"], lambda x, z: app.game.height("r1m1", x, z))
    import math
    assert abs(math.atan2(bx - 2100, bz - 2000) - math.atan2(50, 60)) < 0.02, (bx, bz)   # heading preserved
    et._new_path("ext")
    pv._add_at(2100.0, 2000.0)
    pv._add_at(2180.0, 2040.0)
    assert m.path_points("ext", "ve_drive01") == [{"x": 2100.0, "z": 2000.0}, {"x": 2180.0, "z": 2040.0}]
    m.rename_path("cam", "ve_cam01", "ve_cam02")

    # --- save and reopen ---
    app.game.commit()
    dirty = sorted(Path(d.rel).name for d in app.game.dirty_docs())
    print("изменено:", ", ".join(dirty))
    assert win.save()
    for rel in ("data/maps/r1m1/camera_paths.xml", "data/maps/r1m1/external_paths.xml", "data/maps/r1m1/strings.xml",
                "data/maps/r1m1/triggers.xml", "data/maps/r1m1/cinematriggers.xml"):
        xmlrt.parse((root / rel).read_bytes().decode("cp1251"))
    m2 = App(Game(root)).map("r1m1")
    assert m2.message(mid) == {"who": "Проверяющий", "text": "Засада — всем стоять.", "time": "5", "model": "r1_man"}
    assert [k for k, _a, _t in m2.family(m2.trigger("ve_Rolik"))] == ["fade", "msg", "end"]
    assert "ve_cam02" in m2.paths("cam") and len(m2.path_points("ext", "ve_drive01")) == 2
    b = lua.parse_actions(m2.script(m2.trigger("ve_spawn")))
    assert [x["k"] for x in b] == ["team", "obj", "obj", "trigger", "selfoff"], [x["k"] for x in b]
    shutil.rmtree(root, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    originals()
    editing()

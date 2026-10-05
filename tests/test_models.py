"""Models tab on a COPY of game files: GAM round-trip, skin edits, saving, undo.

    python tests/test_models.py
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
from ve.gam import Gam                           # noqa: E402
from ve.game import find_root                    # noqa: E402

EXTRA = ["data/models/animmodels.xml", "data/models/modeltextures.xml", "data/models/vehicles/ural",
         "data/models/vehicles/shared", "data/models/vehicles/small_car", "data/gamedata/gameobjects/vehicles.xml",
         "data/gamedata/gameobjects/vehicleparts.xml", "data/models/masks/main", "data/models/masks/region1/man"]


def test_roundtrip():
    """Every model of the game re-serialises to the same bytes."""
    root = find_root(HERE)
    files = list((root / "data" / "models").rglob("*.gam"))
    assert len(files) > 100
    for p in files:
        g = Gam(p)
        assert g.dump() == g.data, p
        assert not g.dirty
    print(f"round-trip: {len(files)} files")


def test_edit():
    src = find_root(HERE)
    test_ui.COPY.extend(EXTRA)
    root = test_ui.make_copy()
    try:
        from PySide6.QtWidgets import QMessageBox
        from ve.app import MainWindow, make_app, open_game

        def no_dialog(box):         # a modal question cannot be answered offscreen: fail instead of hanging
            raise AssertionError("dialog: " + box.text())
        QMessageBox.exec = no_dialog
        qapp = make_app()
        app = open_game(qapp, root)
        win = MainWindow(app)
        win.show()
        win.show_tab(3)
        tab = win.tabs[3]
        assert tab.tm.rowCount() > 0
        assert tab.open("uralCab01", 2)
        rel = tab.rel
        g = tab.doc.gam
        before = (root / rel).read_bytes()
        assert len(g.skins) == 9 and tab.lib.action_skins(rel) == {8}
        assert tab.table.columnCount() == 9 and tab.table.rowCount() == g.n_mats

        # visibility: one variant per breakable part
        assert len(g.visible(g.config_to_choice(0))) < len(g.meshes)
        assert g.choice_to_config(g.config_to_choice(12345)) == 12345

        # new skin after skin 2: wrecked skin moves from 8 to 9 in animmodels.xml too
        tab._add_skin()
        assert len(g.skins) == 10 and tab.skin == 3
        g.skins[3][0].textures[0].name = "cab01_5.dds"
        tab._changed()
        assert tab.lib.action_skins(rel) == {9}
        assert app.game.dirty_docs()
        app.game.commit()
        assert win.save()
        g2 = Gam(root / rel)
        assert len(g2.skins) == 10 and g2.skins[3][0].tex(0) == "cab01_5.dds" and g2.skins[9][0].tex(0) == g.skins[9][0].tex(0)
        assert [len(m.tris) for m in g2.meshes] == [len(m.tris) for m in Gam(src / rel).meshes]
        anim = (root / "data/models/animmodels.xml").read_bytes().decode("cp1251")
        i = anim.index('id="uralCab01"')
        assert 'skin="9"' in anim[i:i + 400] and 'skin="8"' not in anim[i:i + 400]
        assert (root / "VersationEditor_backup" / rel).read_bytes() == before

        # undo brings the file's skins back, saving restores the original bytes
        app.game.undo()
        app.reload_all()
        assert len(tab.doc.gam.skins) == 9
        assert win.save()
        assert (root / rel).read_bytes() == before

        # removing the wrecked skin is refused, removing another one shifts it back
        tab.table.setCurrentCell(0, 1)
        tab._del_skin()
        assert len(tab.doc.gam.skins) == 8 and tab.lib.action_skins(rel) == {7}
        app.game.undo()
        app.reload_all()

        # mask: config number <-> variants, picking a look for an NPC
        got = []
        tab.pick("r1_man", "1", "172", "тест", lambda s, c: got.append((s, c)))
        mg = tab.doc.gam
        assert mg.config_count() == 192 and tab.skin == 1 and mg.choice_to_config(tab.choice) == 172
        tab._set_variant(next(i for i, gr in enumerate(mg.groups) if gr.name == "Glasses"), 0)
        qapp.processEvents()
        tab._apply_pick()
        assert got and got[0][0] == "1" and got[0][1] != "172"
        # whole vehicle: every distinct part model, equal skin counts after "equalize"
        assert "Sml4" in tab.lib.vehicles() and tab.open_vehicle("Sml4")
        kinds = [q.kind for q in tab.vparts]
        assert kinds.count("CHASSIS") == 1 and "CABIN" in kinds and "WHEEL" in kinds
        assert len({q.rel for q in tab.vparts}) == len(tab.vparts)
        cab = next(q for q in tab.vparts if q.kind == "CABIN")
        cg = tab.lib.doc(cab.rel).gam
        assert len(cg.skins) == 9
        dead_before = cg.skins[-1][0].tex(0)
        tab._equalize()
        assert len(cg.skins) == 17 and cg.skins[-1][0].tex(0) == dead_before
        assert tab.lib.action_skins(cab.rel) == {16}
        assert tab.table.rowCount() == len(tab.vparts)
        assert win.save() and len(Gam(root / cab.rel).skins) == 17
        from ve.models import infer_name
        assert infer_name(["cab01_0.dds", "cab01_1.dds"], 5) == "cab01_5.dds"
        assert infer_name(["color1.dds", "color2.dds"], 9) == "color10.dds"
        assert infer_name(["a.dds", "b.dds"], 2) is None
        # load points: move one, the file keeps its size, the point reads back, undo restores it
        app.game.commit()
        assert tab.open("uralCab01")
        tab.set_lp_mode(True)
        lg = tab.doc.gam
        ids = lg.load_points()
        assert ids and tab.lp_table.rowCount() == len(ids)
        tab._lp_pick(0)
        p0 = lg.node_pos(ids[0])
        size = (root / tab.rel).stat().st_size
        tab._lp_set(0, (p0[0] + 0.25, p0[1], p0[2] - 0.5))
        assert tab.doc.dirty and win.save()
        back = Gam(root / tab.rel)
        assert (root / tab.rel).stat().st_size == size
        assert all(abs(a - b) < 1e-5 for a, b in zip(back.node_pos(ids[0]), (p0[0] + 0.25, p0[1], p0[2] - 0.5)))
        assert back.materials_bytes() == lg.materials_bytes()
        assert [len(m.tris) for m in back.meshes] == [len(m.tris) for m in lg.meshes]
        app.game.undo()
        app.reload_all()
        assert all(abs(a - b) < 1e-6 for a, b in zip(tab.doc.gam.node_pos(ids[0]), p0))

        # rotation and angle limits: a new limits section appears in a file that had none
        import math
        lg = tab.doc.gam
        had = bool(lg.limits)
        lg.limits.pop(ids[0], None)
        tab._lp_turn(0, (0.0, 30.0, 0.0))
        tab._limit_toggle(ids[0], True)
        tab._limit_set(ids[0], 0, "-20")
        assert win.save()
        back = Gam(root / tab.rel)
        assert abs(back.node_euler(ids[0])[1] - 30.0) < 0.01 and all(abs(a - b) < 1e-5 for a, b in zip(back.node_pos(ids[0]), p0))
        assert abs(math.degrees(back.limits[ids[0]][0]) + 20.0) < 0.01 and len(back.sections) == len(Gam(src / tab.rel).sections) + (not had)
        assert back.materials_bytes() == lg.materials_bytes() and [len(m.tris) for m in back.meshes] == [len(m.tris) for m in lg.meshes]
        app.game.undo()
        app.reload_all()
        assert abs(tab.doc.gam.node_euler(ids[0])[1]) < 0.01
        assert tab.doc.gam.limits_bytes() == Gam(src / tab.rel).limits_bytes()
        tab.set_lp_mode(False)
        tab.open_vehicle("Ural")
        assert sum(q.kind == "CABIN" for q in tab.vparts) == 5
        # cabin and basket pickers over the view
        assert tab.vbar.isVisibleTo(tab) and tab.vbar_lay.count() == 2
        cab3 = [q.rel for q in tab.vparts if q.kind == "CABIN"][2]
        tab._vehicle_put("CABIN", cab3)
        assert tab.vshown["CABIN"] == cab3

        # conversion to Skinned: textures are written, materials switch in every ordinary skin, undo brings them back
        from ve import skinned
        app.game.commit()
        tab._wizard_open()
        wz = tab.wizard
        assert wz is not None and wz.jobs and any(j.checked for j in wz.jobs)
        cabjob = next(i for i, j in enumerate(wz.jobs) if j.title == "cab01" and j.checked)
        # the preview starts on the first paint; "as it was" shows the old skin of the same number, or the first
        assert wz.paint == "color1.dds" and tab.skin == 0
        wz._set_paint("color4.dds")
        r0, _m0, m0i = wz.targets[cabjob][0]
        wz._set_show("source")
        assert tab.skin == 3 and wz.mats(r0)(3)[m0i].tex(0) == "cab01_3.dds"
        wz._set_paint("color9.dds")
        assert tab.skin == 12 and wz.mats(r0)(12)[m0i].tex(0) == "cab01_0.dds"
        wz._set_show("result")
        wz._set_paint("color1.dds")
        wz._pick(cabjob)
        assert wz.jobs[cabjob].green == "cab01_2.dds"     # the teal-green Ural skin scores highest
        for j in wz.jobs:
            j.checked = j is wz.jobs[cabjob]
        rel0, _m, mi0 = wz.targets[cabjob][0]
        built = wz.source.build(wz.jobs[cabjob])
        mask = built[0]
        assert 0.02 < sum(mask.resize((32, 32)).getdata()) / (255 * 1024) < 0.9      # some paint, not everything
        assert built[1].mode == "RGBA" and built[2].getpixel((0, 0))[2:] == (255, 255)
        tab._wizard_apply()
        assert tab.wizard is None
        folder = (root / rel0).parent
        assert (folder / "cab01_detail.dds").is_file() and (folder / "cab01_lightmap.dds").is_file()
        cg = tab.lib.doc(rel0).gam
        m0 = cg.skins[0][mi0]
        assert m0.shader == "Skinned" and [t.type for t in m0.textures] == [0, 1, 2, 3, 4]
        assert m0.tex(0) == "color1.dds" and cg.skins[4][mi0].tex(0) == "color5.dds" and m0.tex(4) == "cab01_detail.dds"
        # one skin per paint plus the wrecked one, on every multi-skin part of the vehicle
        assert len(cg.skins) == 17 and cg.skins[15][mi0].tex(0) == "color12.dds" and tab.lib.action_skins(rel0) == {16}
        assert {len(tab.lib.doc(q.rel).gam.skins) for q in tab.vparts} <= {1, 17}
        other = next(i for i in range(cg.n_mats) if i != mi0 and skinned.is_old(cg.skins[0][i].shader))
        assert cg.skins[15][other].tex(0) != cg.skins[16][other].tex(0)     # numbering did not run into the wrecked texture
        assert cg.skins[-1][mi0].shader != "Skinned" and cg.skins[-1][mi0].tex(0) == "cab01_8.dds"   # wrecked skin kept
        assert tab.lib.find_texture(rel0, "cab01_detail.dds") is not None
        assert win.save() and Gam(root / rel0).skins[0][mi0].shader == "Skinned"

        # the converted set can be tuned again: settings come back, only the textures are rewritten
        assert (folder / "cab01_skinned.json").is_file()
        tab.open_vehicle("Ural")
        tab._wizard_open()
        wz = tab.wizard
        again = next(i for i, j in enumerate(wz.jobs) if j.retune and j.title == "cab01")
        wz._pick(again)
        ja = wz.jobs[again]
        assert ja.green == "cab01_2.dds" and ja.bump and not ja.checked
        before = (folder / "cab01_lightmap.dds").read_bytes()
        mats_before = tab.lib.doc(rel0).gam.materials_bytes()
        ja.set.rough = 2.0
        for j in wz.jobs:
            j.checked = j is ja
        tab._wizard_apply()
        assert (folder / "cab01_lightmap.dds").read_bytes() != before
        assert tab.lib.doc(rel0).gam.materials_bytes() == mats_before and not app.game.dirty_docs()
        import json
        assert json.loads((folder / "cab01_skinned.json").read_text(encoding="utf-8"))["set"]["rough"] == 2.0
        cg = tab.lib.doc(rel0).gam

        # a model converted earlier with only 9 skins is brought to 17 by one button
        del cg.skins[8:16]
        tab.lib.shift_action_skins(rel0, 8, -8)
        assert len(cg.skins) == 9 and tab.lib.action_skins(rel0) == {8}
        assert skinned.full_skins(tab.lib, rel0) == 8
        assert len(cg.skins) == 17 and cg.skins[8][mi0].tex(0) == "camo4.dds" and cg.skins[15][mi0].tex(0) == "color12.dds"
        assert cg.skins[16][mi0].tex(0) == "cab01_8.dds" and tab.lib.action_skins(rel0) == {16}
        app.game.undo()
        app.reload_all()
        assert skinned.is_old(tab.lib.doc(rel0).gam.skins[0][mi0].shader)
        assert any(q.kind == "SUSP" for q in tab.vparts)
        assert win.save() and not app.game.dirty_docs()
        win.close()
        print("edit: ok")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_roundtrip()
    test_edit()
    print("OK")

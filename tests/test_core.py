"""Core checks on real game files; writes nothing to data/.

    python tests/test_core.py
"""
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ve import lua, xmlrt                                    # noqa: E402
from ve.game import Game, find_root                          # noqa: E402
from ve.model import Dialogs, Index, MapData, Quests, Towns  # noqa: E402


def delta(doc):
    d = [l for l in difflib.unified_diff(doc.src.splitlines(), doc.text().splitlines(), lineterm="", n=0)
         if not l.startswith(("---", "+++", "@@"))]
    return d


def main():
    root = find_root()
    assert root, "не найден корень игры"
    g = Game(root)

    # 1. lossless round trip
    n = 0
    for p in list((root / "data/maps").glob("*/dynamicscene.xml")) + list((root / "data/maps").glob("*/triggers.xml")) \
            + [root / "data/if/diz/dialogsglobal.xml", root / "data/gamedata/quests.xml"]:
        src = p.read_bytes().decode("cp1251", errors="replace")
        assert xmlrt.parse(src).dump() == src, p
        n += 1
    print(f"круг без потерь: {n} файлов")

    # 2. dialogs
    d = Dialogs(g)
    assert not delta(d.doc)
    folder = next(f for f in d.folders if f.count("/") == 2 and d.in_folder(f))
    first = d.in_folder(folder)[0].get("name")
    d.add("ve_test_N01", "NPC", folder, "Привет — «тест»", after=first)
    d.add("ve_test_P01", "PLAYER", folder, "Пока")
    d.link("ve_test_N01", "ve_test_P01")
    d.link("ve_test_P01", "ve_test_N01")           # a loop is legal
    d.set("ve_test_P01", "scriptResult", lua.render_actions([{"k": "quest", "do": "take", "quest": "Buyer_Quest1"}, {"k": "end"}]))
    d.rename("ve_test_P01", "ve_test_P02")
    assert d.next_of("ve_test_N01") == ["ve_test_P02"]
    dl = delta(d.doc)
    assert all(l.startswith("+") for l in dl), dl
    print("диалоги: добавление\n  " + "\n  ".join(dl))
    xmlrt.parse(d.doc.text())
    d.delete("ve_test_P02")
    d.delete("ve_test_N01")
    assert not delta(d.doc), delta(d.doc)
    d.add_folder("Root/ve_test/новая")
    d.add("ve_test_X", "NPC", "Root/ve_test/новая")
    d.reindex()
    assert d.folder_of["ve_test_X"] == "Root/ve_test/новая"

    # 3. quests
    q = Quests(g)
    assert not delta(q.doc) and not delta(q.info_doc)
    q.add("ve_test_Quest", "Root/r1m1", brief="Проверка")
    q.add("ve_test_Sub", parent="ve_test_Quest", brief="Шаг")
    q.set("ve_test_Quest", "OnComplete", "AddPlayerMoney(50)")
    q.set_markers("ve_test_Quest", [("r1m1", "TheTown")])
    q.set_mutex("ve_test_Quest", ["Buyer_Quest1"])
    print("квесты:\n  " + "\n  ".join(delta(q.doc)))
    print("журнал:\n  " + "\n  ".join(delta(q.info_doc)))
    xmlrt.parse(q.doc.text())
    q.rename("ve_test_Quest", "ve_test_Quest2")
    assert q.parent_of("ve_test_Sub") == "ve_test_Quest2" and q.mutex_of("ve_test_Quest2") == ["Buyer_Quest1"]
    q.delete("ve_test_Quest2")
    assert delta(q.doc) == ["+	<!--Root/r1m1-->"], delta(q.doc)     # the folder stays declared
    assert not delta(q.info_doc), delta(q.info_doc)

    # 4. map
    towns = Towns(g)
    assert "TownSouth" in towns.protos
    m = MapData(g, "r1m1", towns)
    assert [t.get("Name") for t in m.towns_list()][:1] == ["TheTown"], [t.get("Name") for t in m.towns_list()]
    assert m.full_name("TheTown") == "ЮЖНЫЙ"
    loc = m.add_location("ve_test_loc", 2000, 2000, 40)
    npc = m.add_npc(loc, "ve_test_npc")
    m.set_attr(npc, "helloReplyNames", "Buyer_hellodlg0", optional=True)
    t = m.add_trigger("ve_test_trigger")
    m.set_events(t, [{"eventid": "GE_OBJECT_ENTERS_LOCATION", "ObjName": "ve_test_loc"}])
    m.set_blocks(t, [{"k": "quest", "do": "complete_if", "quest": "Buyer_Quest1"}, {"k": "lua", "code": "if x then\n\ty()\nend"}, {"k": "selfoff"}])
    print("сцена:\n  " + "\n  ".join(delta(m.scene)))
    print("триггеры:\n  " + "\n  ".join(delta(m.trig_docs[0])))
    assert lua.parse_actions(m.script(t))[0]["k"] == "quest"
    m.rename(loc, "ve_test_loc2")
    assert m.events(t)[0]["ObjName"] == "ve_test_loc2"
    town = m.add_town("ve_test_town", "TownSouth", 1000, 1000, "1008", "Тестовый")
    xmlrt.parse(m.scene.text())
    print("город:\n  " + "\n  ".join(delta(m.scene)[-48:]))
    m.remove(town)
    m.remove(m.objects["ve_test_loc2"])
    m.remove_trigger(t)
    assert not delta(m.scene), delta(m.scene)
    assert not delta(m.trig_docs[0]), delta(m.trig_docs[0])
    assert not delta(m.names), delta(m.names)
    towns.clone("TownSouth", "ve_test_proto")
    xmlrt.parse(towns.doc.text())
    assert towns.protos["ve_test_proto"].get("ModelFile") == "minin"
    towns.delete("ve_test_proto")
    assert not delta(towns.doc), delta(towns.doc)[:6]

    idx = Index(g, d, q, towns)
    assert "Buyer" in [n for _, n in idx.hello_users().get("Buyer_hellodlg0", [])]
    print("модели NPC:", idx.npc_models()[:6], "| белонги:", idx.belongs()[:3])
    print("OK")


if __name__ == "__main__":
    main()

"""Data model: dialogs, quests, map (places, NPCs, triggers), town prototypes.

The game has no single source of truth: everything is linked by names across
5-7 files. Renames and deletions here follow those links.
"""
from __future__ import annotations

import math
import re

from . import lua
from .game import Game, GameError, fmt, parse_vec
from .xmlrt import Comment, Element, clone

DIALOGS = "data/if/diz/dialogsglobal.xml"
QUESTS = "data/gamedata/quests.xml"
QUESTINFO = "data/if/diz/questinfoglobal.xml"
TOWNS = "data/gamedata/gameobjects/towns.xml"
ROOT = "Root"

REPLY_ORDER = ["name", "text", "role", "scriptCondition", "scriptResult", "nextReplies"]
QUEST_ORDER = ["Name", "Automatic", "SubQuestsCondition", "CheckAll", "ConditionToGive", "PrecedingQuests",
               "OnCanBeGiven", "OnTake", "OnComplete", "OnFail", "Levels"]
INFO_ORDER = ["questName", "briefDiz", "fullDiz", "isMainQuest"]


def _is_folder(node) -> bool:
    return isinstance(node, Comment) and node.text.strip().startswith(ROOT)


def _replace_quoted(text: str, old: str, new: str) -> str:
    return re.sub(r"(['\"])" + re.escape(old) + r"\1", lambda m: m.group(1) + new + m.group(1), text)


def _replace_word(lst: str, old: str, new: str | None) -> str:
    out = [(new if w == old else w) for w in lst.split()]
    return " ".join(w for w in out if w)


class Foldered:
    """Shared by dialogs and quests: old-editor folders are <!--Root/...--> comments."""

    tag = ""
    key = ""

    def __init__(self, doc):
        self.doc = doc
        self.items: dict[str, Element] = {}
        self.folder_of: dict[str, str] = {}
        self.folders: dict[str, Comment | None] = {}
        self.reindex()

    @property
    def root(self):
        return self.doc.root

    def reindex(self):
        self.items.clear()
        self.folder_of.clear()
        self.folders = {ROOT: None}
        cur = ROOT
        for c in self.root.children:
            if _is_folder(c):
                cur = c.text.strip()
                self._declare(cur, c)
            elif isinstance(c, Element) and c.tag == self.tag:
                self._index(c, cur)

    def _declare(self, path: str, node):
        parts = path.split("/")
        for i in range(1, len(parts)):
            self.folders.setdefault("/".join(parts[:i]), None)
        if self.folders.get(path) is None:
            self.folders[path] = node

    def _index(self, el: Element, folder: str):
        self.items[el.get(self.key)] = el
        self.folder_of[el.get(self.key)] = folder

    def in_folder(self, path: str) -> list[Element]:
        return [el for n, el in self.items.items() if self.folder_of[n] == path and el.parent is self.root]

    def subfolders(self, path: str) -> list[str]:
        pre = path + "/"
        return [f for f in self.folders if f.startswith(pre) and "/" not in f[len(pre):]]

    def add_folder(self, path: str):
        if path in self.folders and self.folders[path] is not None:
            return
        c = Comment(path)
        self._insert_top(c, None, blank=False)
        self._declare(path, c)
        self.doc.touch()

    def rename_folder(self, old: str, new: str):
        for c in self.root.children:
            if _is_folder(c):
                p = c.text.strip()
                if p == old or p.startswith(old + "/"):
                    c.text = new + p[len(old):]
        self.reindex()
        self.doc.touch()

    def folder_empty(self, path: str) -> bool:
        return not any(f == path or f.startswith(path + "/") for f in self.folder_of.values())

    def delete_folder(self, path: str):
        """Remove a folder; its content must already be gone."""
        for c in list(self.root.children):
            if _is_folder(c):
                p = c.text.strip()
                if p == path or p.startswith(path + "/"):
                    self.root.remove(c)
        self.reindex()
        self.doc.touch()

    def _section_end(self, folder: str):
        """Node after which a new folder element goes (its last element or comment)."""
        last, cur = None, ROOT
        for c in self.root.children:
            if _is_folder(c):
                cur = c.text.strip()
                if cur == folder:
                    last = c
            elif isinstance(c, Element) and c.tag == self.tag and cur == folder:
                last = c
        return last

    def _insert_top(self, node, folder: str | None, blank=True):
        ref = self._section_end(folder) if folder else None
        if isinstance(ref, Comment):
            # file header is a run of folder declarations: do not insert into it
            i = self.root.children.index(ref) + 1
            while i < len(self.root.children) and not isinstance(self.root.children[i], (Element, Comment)):
                i += 1
            if i < len(self.root.children) and isinstance(self.root.children[i], Comment):
                ref = None
        if folder and ref is None and folder != ROOT:
            c = Comment(folder)
            self._append_top(c, blank=False)
            self._declare(folder, c)
            ref = c
        if ref is not None:
            self.root.insert_after(ref, node, blank_line=blank and not isinstance(ref, Comment))
        else:
            self._append_top(node, blank)

    def _append_top(self, node, blank=True):
        self.root.append(node, blank_line=blank)

    def move_to_folder(self, name: str, folder: str):
        el = self.items[name]
        if el.parent is not self.root or self.folder_of[name] == folder:
            return
        self.root.remove(el)
        self._insert_top(el, folder)
        self.reindex()
        self.doc.touch()


class Dialogs(Foldered):
    tag, key = "Reply", "name"

    def __init__(self, game: Game):
        self.game = game
        super().__init__(game.doc(DIALOGS, "DialogsResource"))

    # --- read ---
    def next_of(self, name: str) -> list[str]:
        el = self.items.get(name)
        return el.get("nextReplies").split() if el is not None else []

    def incoming(self) -> dict[str, list[str]]:
        inc: dict[str, list[str]] = {}
        for n, el in self.items.items():
            for t in el.get("nextReplies").split():
                inc.setdefault(t, []).append(n)
        return inc

    def unique_name(self, base: str) -> str:
        if base not in self.items:
            return base
        m = re.match(r"(.*?)(\d+)$", base)
        stem, i = (m.group(1), int(m.group(2))) if m else (base + "_", 1)
        while f"{stem}{i}" in self.items:
            i += 1
        return f"{stem}{i}"

    def child_name(self, parent: str, role: str) -> str:
        stem = re.sub(r"_[NP]\d+[a-z]?$", "", parent)
        i = 1
        while f"{stem}_{'N' if role == 'NPC' else 'P'}{i:02d}" in self.items:
            i += 1
        return f"{stem}_{'N' if role == 'NPC' else 'P'}{i:02d}"

    # --- edit ---
    def add(self, name: str, role: str, folder: str, text: str = "", after: str | None = None) -> Element:
        if not name or name in self.items:
            raise GameError(f"реплика «{name}» уже есть")
        el = Element("Reply", {"name": name, "text": text, "role": role})
        el.inline = False
        ref = self.items.get(after) if after else None
        if ref is not None and ref.parent is self.root and self.folder_of.get(after) == folder:
            self.root.insert_after(ref, el)
        else:
            self._insert_top(el, folder)
        self._index(el, folder)
        self.doc.touch()
        return el

    def set(self, name: str, attr: str, value: str):
        el = self.items[name]
        if attr in ("name", "text", "role"):
            el.set(attr, value)
        else:
            el.set_opt(attr, value)
        el.reorder(REPLY_ORDER)
        self.doc.touch()

    def set_next(self, name: str, targets: list[str]):
        self.set(name, "nextReplies", " ".join(dict.fromkeys(targets)))

    def link(self, a: str, b: str):
        nx = self.next_of(a)
        if b not in nx:
            self.set_next(a, nx + [b])

    def unlink(self, a: str, b: str):
        self.set_next(a, [t for t in self.next_of(a) if t != b])

    def delete(self, name: str):
        el = self.items.pop(name)
        self.folder_of.pop(name, None)
        el.parent.remove(el)
        for n, other in self.items.items():
            if name in other.get("nextReplies").split():
                other.set_opt("nextReplies", _replace_word(other.get("nextReplies"), name, None))
        self.doc.touch()

    def rename(self, old: str, new: str):
        if new == old:
            return
        if not new or new in self.items:
            raise GameError(f"реплика «{new}» уже есть")
        el = self.items[old]
        el.set("name", new)
        for other in self.items.values():
            if old in other.get("nextReplies").split():
                other.set("nextReplies", _replace_word(other.get("nextReplies"), old, new))
        self.items = {(new if k == old else k): v for k, v in self.items.items()}
        self.folder_of[new] = self.folder_of.pop(old)
        self.doc.touch()

    def replace_ref(self, old: str, new: str):
        """A quest/trigger/object was renamed: fix reply scripts."""
        for el in self.items.values():
            for a in ("scriptCondition", "scriptResult"):
                v = el.get(a)
                if old in v:
                    nv = _replace_quoted(v, old, new)
                    if nv != v:
                        el.set(a, nv)
                        self.doc.touch()


class Quests(Foldered):
    tag, key = "quest", "Name"

    def __init__(self, game: Game):
        self.game = game
        self.info_doc = game.doc(QUESTINFO, "QuestInfoResource")
        self.infos: dict[str, Element] = {}
        super().__init__(game.doc(QUESTS, "quests"))

    def reindex(self):
        super().reindex()
        self.infos = {e.get("questName"): e for e in self.info_doc.root.elements("QuestInfo")}

    def _index(self, el: Element, folder: str):
        super()._index(el, folder)
        for sub in el.elements("quest"):
            self._index(sub, folder)

    def _append_top(self, node, blank=True):
        mx = self.root.find("MutuallyExclusives")
        if mx is not None:
            i = self.root.children.index(mx)
            if i > 0 and not isinstance(self.root.children[i - 1], (Element, Comment)):
                i -= 1
            self.root.insert_at(i, node, blank)
        else:
            self.root.append(node, blank_line=blank)

    # --- read ---
    def title(self, name: str) -> str:
        i = self.infos.get(name)
        return (i.get("briefDiz") if i is not None else "") or name

    def parent_of(self, name: str) -> str | None:
        p = self.items[name].parent
        return p.get("Name") if p is not None and p.tag == "quest" else None

    def children_of(self, name: str) -> list[str]:
        return [e.get("Name") for e in self.items[name].elements("quest")]

    def unique_name(self, base: str) -> str:
        if base not in self.items:
            return base
        i = 2
        while f"{base}{i}" in self.items:
            i += 1
        return f"{base}{i}"

    def mutex_of(self, name: str) -> list[str]:
        out = []
        mx = self.root.find("MutuallyExclusives")
        for m in (mx.elements("mutex") if mx is not None else []):
            qs = m.get("quests").split()
            if name in qs:
                out += [q for q in qs if q != name]
        return list(dict.fromkeys(out))

    # --- edit ---
    def add(self, name: str, folder: str = ROOT, parent: str | None = None, brief: str = "") -> Element:
        if not name or name in self.items:
            raise GameError(f"квест «{name}» уже есть")
        el = Element("quest", {"Name": name, "Automatic": "1" if parent else "0", "SubQuestsCondition": "and",
                               "CheckAll": "0", "ConditionToGive": "all complete"})
        el.inline = False
        if parent:
            self.items[parent].append(el)
            folder = self.folder_of[parent]
        else:
            self._insert_top(el, folder)
        self._index(el, folder)
        self.info(name, create=True).set("briefDiz", brief)
        self.doc.touch()
        return el

    def info(self, name: str, create: bool = False) -> Element | None:
        i = self.infos.get(name)
        if i is None and create:
            i = Element("QuestInfo", {"questName": name, "briefDiz": "", "isMainQuest": "0"})
            i.inline = False
            self.info_doc.root.append(i)
            self.infos[name] = i
            self.info_doc.touch()
        return i

    def set(self, name: str, attr: str, value: str):
        el = self.items[name]
        if attr in ("Automatic", "SubQuestsCondition", "CheckAll", "ConditionToGive"):
            el.set(attr, value)
        else:
            el.set_opt(attr, value)
        el.reorder(QUEST_ORDER)
        self.doc.touch()

    def set_info(self, name: str, attr: str, value: str):
        i = self.info(name, create=True)
        if attr == "fullDiz":
            i.set_opt(attr, value)
        else:
            i.set(attr, value)
        i.reorder(INFO_ORDER)
        self.info_doc.touch()

    def markers(self, name: str) -> list[tuple[str, str]]:
        i = self.infos.get(name)
        return [(m.get("name"), m.get("targetObjName")) for m in i.elements("Map")] if i is not None else []

    def set_markers(self, name: str, markers: list[tuple[str, str]]):
        i = self.info(name, create=True)
        for m in i.elements("Map"):
            i.remove(m)
        for mp, obj in markers:
            m = Element("Map", {"name": mp, "targetObjName": obj})
            m.inline = False
            i.append(m, blank_line=False)
        self.info_doc.touch()

    def set_mutex(self, name: str, others: list[str]):
        mx = self.root.find("MutuallyExclusives")
        if mx is not None:
            for m in mx.elements("mutex"):
                if name in m.get("quests").split():
                    mx.remove(m)
        if others:
            if mx is None:
                mx = Element("MutuallyExclusives")
                self.root.append(mx)
            m = Element("mutex", {"quests": " ".join([name] + others)})
            m.inline = False
            mx.append(m)
        self.doc.touch()

    def delete(self, name: str):
        el = self.items[name]
        gone = [name] + [e.get("Name") for e in el.iter("quest")]
        el.parent.remove(el)
        for g in gone:
            self.items.pop(g, None)
            self.folder_of.pop(g, None)
            i = self.infos.pop(g, None)
            if i is not None:
                self.info_doc.root.remove(i)
                self.info_doc.touch()
        mx = self.root.find("MutuallyExclusives")
        for other in self.items.values():
            pq = other.get("PrecedingQuests")
            if pq and set(pq.split()) & set(gone):
                other.set_opt("PrecedingQuests", " ".join(q for q in pq.split() if q not in gone))
        for m in (mx.elements("mutex") if mx is not None else []):
            qs = [q for q in m.get("quests").split() if q not in gone]
            if len(qs) < 2:
                mx.remove(m)
            else:
                m.set("quests", " ".join(qs))
        self.doc.touch()

    def rename(self, old: str, new: str):
        if new == old:
            return
        if not new or new in self.items:
            raise GameError(f"квест «{new}» уже есть")
        self.items[old].set("Name", new)
        i = self.infos.get(old)
        if i is not None:
            i.set("questName", new)
            self.info_doc.touch()
        for other in self.items.values():
            if old in other.get("PrecedingQuests").split():
                other.set("PrecedingQuests", _replace_word(other.get("PrecedingQuests"), old, new))
            for a in ("OnTake", "OnComplete", "OnFail", "OnCanBeGiven"):
                if old in other.get(a):
                    other.set(a, _replace_quoted(other.get(a), old, new))
        mx = self.root.find("MutuallyExclusives")
        for m in (mx.elements("mutex") if mx is not None else []):
            if old in m.get("quests").split():
                m.set("quests", _replace_word(m.get("quests"), old, new))
        self.reindex()
        self.doc.touch()

    def move(self, name: str, parent: str | None, folder: str = ROOT):
        """Make it a subquest of parent, or move to the top level of folder."""
        el = self.items[name]
        if parent and (parent == name or parent in [e.get("Name") for e in el.iter("quest")]):
            raise GameError("квест нельзя вложить в самого себя")
        el.parent.remove(el)
        if parent:
            self.items[parent].append(el)
        else:
            self._insert_top(el, folder)
        _reindent(el)
        self.reindex()
        self.doc.touch()


def _reindent(el: Element):
    """After moving to another depth rebuild the opening tags."""
    el._open = None
    for c in el.iter():
        c._open = None


# --- map ---

EVENTS = [
    ("GE_OBJECT_ENTERS_LOCATION", "въезд в локацию", "loc"),
    ("GE_OBJECT_LEAVES_LOCATION", "выезд из локации", "loc"),
    ("GE_OBJECT_IN_LOCATION", "объект в локации", "loc"),
    ("GE_TIME_PERIOD", "прошло секунд", "timeout"),
    ("GE_GAME_START", "начало игры", ""),
    ("GE_OBJECT_DIE", "объект уничтожен", "obj"),
    ("GE_VEHICLE_WITHOUT_HEALTH", "машина без здоровья", "obj"),
    ("GE_TARGET_REACHED", "цель достигнута", "obj"),
    ("GE_TALK_WITH_OBJECT", "разговор с объектом", "obj"),
    ("GE_LEAVE_TOWN", "выезд из города", "obj"),
    ("GE_TOWN_CONDITIONAL_CLOSING", "город закрывается", "obj"),
    ("GE_END_CINEMATIC", "ролик закончился", "obj"),
    ("GE_SKIP_CINEMATIC", "ролик пропущен", "obj"),
    ("GE_CINEMATIC_ENTER_FADE_IN", "fade-in ролика", "obj"),
    ("GE_START_CINEMATIC_FLY", "начался пролёт камеры", "fly"),
    ("GE_START_CINEMATIC_MSG", "началась реплика ролика", "msg"),
    ("GE_PLAYER_VEHICLE_HORN", "игрок сигналит", "obj"),
    ("GE_PLAYER_VEHICLE_CHANGED", "игрок сменил машину", "obj"),
    ("GE_UNDER_ATTACK", "объект атакован", "obj"),
    ("GE_OBJECT_ENTERS_OBJECT", "объект въехал в объект", "obj"),
    ("GE_OBJECT_LEAVES_OBJECT", "объект выехал из объекта", "obj"),
]
EVENT_BY = {e[0]: e for e in EVENTS}
SCENE_ORDER = ["Flags", "Name", "Belong", "Prototype", "Pos", "Rot", "Radius", "LookingTimeOut", "ModelName", "skin",
               "cfg", "NpcType", "helloReplyNames", "SpokenCount", "PointOfViewInInterface", "CaravansDest"]


class Towns:
    """Class="Town" prototypes and what is tied to them (map icons)."""

    ICONS = ["data/if/ico/modelicons.xml", "data/if/ico_hd/modelicons.xml"]

    def __init__(self, game: Game):
        self.game = game
        self.doc = game.doc(TOWNS, "Prototypes") if game.has(TOWNS) else None
        self.protos: dict[str, Element] = {}
        self.reindex()

    def reindex(self):
        self.protos = {}
        if self.doc:
            self.protos = {p.get("Name"): p for p in self.doc.root.iter("Prototype") if p.get("Class") == "Town"}

    def generators(self, cls: str) -> list[str]:
        return [p.get("Name") for p in self.doc.root.iter("Prototype") if p.get("Class") == cls] if self.doc else []

    def clone(self, src: str, new: str) -> Element:
        if new in self.protos or not new:
            raise GameError(f"прототип «{new}» уже есть")
        s = self.protos[src]
        c = clone(s)
        s.parent.insert_after(s, c)
        c._open = re.sub(r'(Name\s*=\s*")' + re.escape(src) + '"', lambda m: m.group(1) + new + '"', c._open, count=1)
        c.attrs["Name"] = new
        self.protos[new] = c
        self.doc.touch()
        for rel in self.ICONS:
            if not self.game.has(rel):
                continue
            d = self.game.doc(rel)
            for it in d.root.iter("Item"):
                if it.get("id") == src:
                    ic = clone(it)
                    ic._open = it._open.replace(f'"{src}"', f'"{new}"', 1)
                    ic.attrs["id"] = new
                    it.parent.insert_after(it, ic, blank_line=False)
                    d.touch()
                    break
        return c

    def delete(self, name: str):
        p = self.protos.pop(name)
        p.parent.remove(p)
        self.doc.touch()
        for rel in self.ICONS:
            if self.game.has(rel):
                d = self.game.doc(rel)
                for it in list(d.root.iter("Item")):
                    if it.get("id") == name:
                        it.parent.remove(it)
                        d.touch()

    def articles(self, name: str) -> list[Element]:
        return self.protos[name].elements("Article")

    def set_articles(self, name: str, rows: list[dict]):
        p = self.protos[name]
        for a in p.elements("Article"):
            p.remove(a)
        for r in rows:
            a = Element("Article", r)
            a.inline = False
            p.append(a)
        self.doc.touch()


class MapData:
    def __init__(self, game: Game, name: str, towns: Towns):
        self.game = game
        self.name = name
        self.towns = towns
        base = f"data/maps/{name}/"
        self.scene = game.doc(base + "dynamicscene.xml", "DynamicScene")
        self.names = game.doc(base + "object_names.xml", "ObjectNames")
        self.trig_docs = [game.doc(base + "triggers.xml", "triggers")]
        if game.has(base + "cinematriggers.xml"):
            self.trig_docs.append(game.doc(base + "cinematriggers.xml"))
        self.base = base
        self.objects: dict[str, Element] = {}
        self.reindex()

    # --- cutscene data: camera paths, drive paths, messages ---
    PATH_FILES = {"cam": "camera_paths.xml", "ext": "external_paths.xml"}

    def path_doc(self, kind: str):
        return self.game.doc(self.base + self.PATH_FILES[kind], "Paths")

    def paths(self, kind: str) -> list[str]:
        return [p.get("Name") for p in self.path_doc(kind).root.elements("Path")]

    def _path(self, kind: str, name: str) -> Element | None:
        return next((p for p in self.path_doc(kind).root.elements("Path") if p.get("Name") == name), None)

    def path_points(self, kind: str, name: str) -> list[dict]:
        p = self._path(kind, name)
        out = []
        for pt in (p.elements("Point") if p is not None else []):
            c = parse_vec(pt.get("coord"))
            if kind == "cam" and len(c) >= 3:
                out.append({"x": c[0], "y": c[1], "z": c[2], "rot": pt.get("rotation") or "0 0 0 1"})
            elif kind == "ext" and len(c) >= 2:
                out.append({"x": c[0], "z": c[-1]})
        return out

    def set_path_points(self, kind: str, name: str, pts: list[dict]):
        p = self._path(kind, name)
        if p is None:
            return
        for pt in p.elements("Point"):
            p.remove(pt)
        for d in pts:
            if kind == "cam":
                e = Element("Point", {"coord": f"{fmt(d['x'])} {fmt(d['y'])} {fmt(d['z'])}", "rotation": d["rot"]})
            else:
                e = Element("Point", {"coord": f"{fmt(d['x'])} {fmt(d['z'])}"})
            e.inline = True
            p.append(e, blank_line=False)
        self.path_doc(kind).touch()

    def add_path(self, kind: str, name: str) -> Element:
        if not name or self._path(kind, name) is not None:
            raise GameError(f"путь «{name}» уже есть")
        p = Element("Path", {"Name": name})
        p.inline = True
        d = self.path_doc(kind)
        d.root.append(p)
        d.touch()
        return p

    def remove_path(self, kind: str, name: str):
        p = self._path(kind, name)
        if p is not None:
            p.parent.remove(p)
            self.path_doc(kind).touch()

    def rename_path(self, kind: str, old: str, new: str):
        if new == old:
            return
        if not new or self._path(kind, new) is not None:
            raise GameError(f"путь «{new}» уже есть")
        self._path(kind, old).set("Name", new)
        self.path_doc(kind).touch()
        for t in self.triggers():
            self._replace_in_script(t, old, new)
            for ev in t.elements("event"):
                if ev.get("flypath") == old:
                    ev.set("flypath", new)
                    self._doc_of(t).touch()

    def str_doc(self):
        return self.game.doc(self.base + "strings.xml", "resource")

    def messages(self) -> dict[str, Element]:
        return {s.get("id"): s for s in self.str_doc().root.elements("string")}

    def message(self, mid: str) -> dict | None:
        s = self.messages().get(str(mid))
        if s is None:
            return None
        who, _, text = s.get("value").partition("|")
        if "|" not in s.get("value"):
            who, text = "", s.get("value")
        return {"who": who, "text": text, "time": s.get("time"), "model": s.get("modelName"),
                "skin": s.get("modelSkin"), "cfg": s.get("modelCfg"), "slot": s.get("modelSlot")}

    def set_message(self, mid: str, who: str, text: str, time: str = "", model: str = "", skin: str = "",
                    cfg: str = "", slot: str = ""):
        d = self.str_doc()
        s = self.messages().get(str(mid))
        if s is None:
            s = Element("string", {"id": str(mid)})
            s.inline = False
            d.root.append(s)
        s.set_opt("modelName", model)
        s.set_opt("modelSkin", skin)
        s.set_opt("modelCfg", cfg)
        s.set_opt("modelSlot", slot)
        s.set("value", f"{who}|{text}")
        s.set_opt("time", time)
        s.reorder(["id", "modelName", "modelSkin", "modelCfg", "modelSlot", "value", "sound", "time"])
        d.touch()

    def new_message_id(self) -> str:
        ids = [int(i) for i in self.messages() if i.isdigit()]
        return str(max(ids, default=0) + 1)

    # --- cutscenes: start trigger and its family ---
    @staticmethod
    def _code(script: str) -> str:
        return "\n".join(ln for ln in script.split("\n") if not ln.strip().startswith("--"))

    def is_cutscene(self, t: Element) -> bool:
        return "StartCinematic" in self._code(self.script(t))

    def cutscenes(self) -> list[Element]:
        return [t for t in self.triggers() if self.is_cutscene(t)]

    def trigger_kind(self, t: Element) -> tuple[str, str] | None:
        """Trigger role in a cutscene by its events: end, fade, fly, msg, time."""
        evs = self.events(t)
        ids = {e.get("eventid") for e in evs}
        if not ids:
            return None
        if ids <= {"GE_END_CINEMATIC", "GE_SKIP_CINEMATIC"}:
            return ("end", "")
        if ids == {"GE_CINEMATIC_ENTER_FADE_IN"}:
            return ("fade", "")
        if ids == {"GE_START_CINEMATIC_FLY"}:
            return ("fly", evs[0].get("flypath", ""))
        if ids == {"GE_START_CINEMATIC_MSG"}:
            return ("msg", evs[0].get("msgid", ""))
        if ids == {"GE_TIME_PERIOD"}:
            return ("time", evs[0].get("timeout", "0").strip())
        return None

    def activated_by(self, t: Element) -> list[str]:
        return re.findall(r"TActivate\s*\(\s*['\"](\w+)['\"]", self._code(self.script(t)))

    def family(self, start: Element) -> list[tuple[str, str, Element]]:
        """Cutscene triggers activated by start (transitively), without start itself."""
        out, seen, queue = [], {start.get("Name")}, [start]
        while queue:
            t = queue.pop(0)
            for name in self.activated_by(t):
                if name in seen:
                    continue
                m = self.trigger(name)
                kind = self.trigger_kind(m) if m is not None else None
                if kind is None:
                    continue
                seen.add(name)
                out.append((kind[0], kind[1], m))
                if kind[0] != "end":      # the end trigger activates gameplay triggers: stop here
                    queue.append(m)
        order = {"fade": 0, "fly": 1, "msg": 2, "time": 3, "end": 4}
        return sorted(out, key=lambda x: order[x[0]])

    def reindex(self):
        self.objects = {}
        for o in self.scene.root.iter("Object"):
            n = o.get("Name")
            if n and n not in self.objects:
                self.objects[n] = o

    # --- classification ---
    def kind(self, el: Element) -> str:
        p = el.get("Prototype")
        if p == "genericLocation":
            return "loc"
        if p == "NPC":
            return "npc"
        if p in self.towns.protos:
            return "town"
        return p if p in ("bar", "shop", "workshop") else "other"

    def towns_list(self) -> list[Element]:
        return [o for o in self.scene.root.elements("Object") if self.kind(o) == "town"]

    def locations(self, parent: Element | None = None) -> list[Element]:
        return [o for o in (parent or self.scene.root).elements("Object") if self.kind(o) == "loc"]

    def npcs(self, el: Element) -> list[Element]:
        """NPCs of a place: in the bar for a town, directly inside for a location."""
        out = []
        for c in el.elements("Object"):
            k = self.kind(c)
            if k == "npc":
                out.append(c)
            elif k == "bar":
                out += [n for n in c.elements("Object") if self.kind(n) == "npc"]
        return out

    def pos(self, el: Element) -> tuple[float, float, float] | None:
        v = parse_vec(el.get("Pos"))
        return (v[0], v[1], v[2]) if len(v) >= 3 else None

    def full_name(self, name: str) -> str:
        for o in self.names.root.elements("Object"):
            if o.get("Name") == name:
                return o.get("FullName")
        return ""

    def set_full_name(self, name: str, full: str):
        for o in self.names.root.elements("Object"):
            if o.get("Name") == name:
                if full:
                    o.set("FullName", full)
                else:
                    self.names.root.remove(o)
                self.names.touch()
                return
        if full:
            o = Element("Object", {"Name": name, "FullName": full})
            o.inline = False
            self.names.root.append(o)
            self.names.touch()

    def unique(self, base: str) -> str:
        names = set(self.objects) | {t.get("Name") for t in self.triggers()}
        if base not in names:
            return base
        i = 2
        while f"{base}{i}" in names:
            i += 1
        return f"{base}{i}"

    # --- places ---
    def _y(self, x: float, z: float, default: float = 0.0) -> float:
        h = self.game.height(self.name, x, z)
        return default if h is None else h

    def set_pos(self, el: Element, x: float, z: float):
        old = self.pos(el)
        y = self._y(x, z, old[1] if old else 0.0)
        el.set("Pos", f"{fmt(x)} {fmt(y)} {fmt(z)}")
        self.scene.touch()

    def move(self, el: Element, x: float, z: float):
        """Move a place; child locations of a town move with it."""
        old = self.pos(el)
        self.set_pos(el, x, z)
        if old:
            for c in el.iter("Object"):
                p = self.pos(c)
                if p:
                    self.set_pos(c, p[0] + x - old[0], p[2] + z - old[2])

    def _loc_el(self, name: str, belong: str, x: float, z: float, radius: float, rot: str = "") -> Element:
        a = {"Flags": "21", "Name": name, "Belong": belong, "Prototype": "genericLocation",
             "Pos": f"{fmt(x)} {fmt(self._y(x, z))} {fmt(z)}"}
        if rot:
            a["Rot"] = rot
        a["Radius"] = fmt(radius)
        a["LookingTimeOut"] = fmt(radius)
        e = Element("Object", a)
        e.inline = False
        return e

    def add_location(self, name: str, x: float, z: float, radius: float = 30.0, belong: str = "1100") -> Element:
        if not name or name in self.objects:
            raise GameError(f"объект «{name}» уже есть на карте")
        e = self._loc_el(name, belong, x, z, radius)
        locs = self.locations()
        if locs:
            self.scene.root.insert_after(locs[-1], e)
        else:
            self.scene.root.append(e)
        self.objects[name] = e
        self.scene.touch()
        return e

    def add_npc(self, place: Element, name: str, model: str = "r1_man", cfg: str = "", skin: str = "") -> Element:
        if not name or name in self.objects:
            raise GameError(f"объект «{name}» уже есть на карте")
        host = place
        if self.kind(place) == "town":
            host = next((c for c in place.elements("Object") if self.kind(c) == "bar"), None)
            if host is None:
                host = self._child(place, place.get("Name") + "_Bar", "bar", first=True)
        a = {"Name": name, "Belong": place.get("Belong", "1100"), "Prototype": "NPC", "ModelName": model}
        if skin:
            a["skin"] = skin
        if cfg:
            a["cfg"] = cfg
        a["SpokenCount"] = "0"
        e = Element("Object", a)
        e.inline = False
        host.append(e)
        self.objects[name] = e
        self.scene.touch()
        return e

    def _child(self, parent: Element, name: str, proto: str, first: bool = False, **extra) -> Element:
        e = Element("Object", {"Name": name, "Belong": parent.get("Belong"), "Prototype": proto, **extra})
        e.inline = False
        if first and parent.children:
            parent.insert_at(0, e, blank_line=False)
        else:
            parent.append(e)
        self.objects[name] = e
        return e

    def add_town(self, name: str, proto: str, x: float, z: float, belong: str, full: str = "",
                 barman_model: str = "r1_man") -> Element:
        if not name or name in self.objects:
            raise GameError(f"объект «{name}» уже есть на карте")
        y = self._y(x, z)
        t = Element("Object", {"Name": name, "Belong": belong, "Prototype": proto,
                               "Pos": f"{fmt(x)} {fmt(y)} {fmt(z)}", "Rot": "0.000 0.000 0.000 1.000",
                               "PointOfViewInInterface": "50.000 50.000 50.000", "CaravansDest": ""})
        t.inline = False
        towns = self.towns_list()
        if towns:
            self.scene.root.insert_after(towns[-1], t)
        else:
            self.scene.root.append(t)
        self.objects[name] = t
        shop = self._child(t, name + "_Shop", "shop")
        shop.append(Element("GunsAndGadgets"), blank_line=False)
        ws = self._child(t, name + "_Workshop", "workshop")
        ws.append(Element("CabinsAndBaskets"), blank_line=False)
        ws.append(Element("Vehicles"), blank_line=False)
        bar = self._child(t, name + "_Bar", "bar")
        bm = Element("Object", {"Name": name + "_Bar_Barman", "Belong": belong, "Prototype": "NPC",
                                "ModelName": barman_model, "NpcType": "BARMAN", "SpokenCount": "0"})
        bm.inline = False
        bar.append(bm)
        self.objects[bm.get("Name")] = bm
        for suffix, dx, dz, r in (("_deploy", 0.0, -60.0, 10.0), ("_defend", 0.0, 0.0, 50.0), ("_enter", 0.0, -25.0, 35.0)):
            le = self._loc_el(name + suffix, belong, x + dx, z + dz, r)
            t.append(le)
            self.objects[le.get("Name")] = le
        for tag in ("Parts", "EntryPath", "ExitPath"):
            t.append(Element(tag), blank_line=False)
        up = (full or name).upper()
        for n, f in ((name, up), (name + "_enter", up), (name + "_Bar", "Бар"), (name + "_Workshop", "Мастерская"),
                     (name + "_Shop", "Магазин"), (name + "_Bar_Barman", "Бармен")):
            self.set_full_name(n, f)
        self.scene.touch()
        return t

    def set_attr(self, el: Element, attr: str, value: str, optional: bool = False):
        if optional:
            el.set_opt(attr, value)
        else:
            el.set(attr, value)
        el.reorder(SCENE_ORDER)
        self.scene.touch()

    def set_belong(self, el: Element, belong: str):
        """A place's belong is also the belong of all its parts."""
        el.set("Belong", belong)
        for c in el.iter("Object"):
            if c.get("Belong"):
                c.set("Belong", belong)
        self.scene.touch()

    def remove(self, el: Element):
        gone = [el.get("Name")] + [c.get("Name") for c in el.iter("Object")]
        el.parent.remove(el)
        for g in gone:
            self.objects.pop(g, None)
            if self.full_name(g):
                self.set_full_name(g, "")
        self.scene.touch()

    def rename(self, el: Element, new: str, dialogs: Dialogs | None = None, quests: Quests | None = None):
        old = el.get("Name")
        if new == old:
            return
        if not new or new in self.objects:
            raise GameError(f"объект «{new}» уже есть на карте")
        pairs = [(old, new)]
        if self.kind(el) == "town":   # town parts are prefixed with its name
            for c in el.iter("Object"):
                cn = c.get("Name")
                if cn.startswith(old + "_"):
                    pairs.append((cn, new + cn[len(old):]))
        for o, n in pairs:
            obj = self.objects.pop(o)
            obj.set("Name", n)
            self.objects[n] = obj
            full = self.full_name(o)
            if full:
                self.set_full_name(o, "")
                self.set_full_name(n, full)
            for t in self.triggers():
                for ev in t.elements("event"):
                    if ev.get("ObjName") == o:
                        ev.set("ObjName", n)
                        self._doc_of(t).touch()
                self._replace_in_script(t, o, n)
            if quests:
                for i in quests.infos.values():
                    for m in i.elements("Map"):
                        if m.get("name") == self.name and m.get("targetObjName") == o:
                            m.set("targetObjName", n)
                            quests.info_doc.touch()
            if dialogs:
                dialogs.replace_ref(o, n)
        self.scene.touch()

    # --- triggers ---
    def triggers(self) -> list[Element]:
        out = []
        for d in self.trig_docs:
            out += d.root.elements("trigger")
        return out

    def trigger(self, name: str) -> Element | None:
        return next((t for t in self.triggers() if t.get("Name") == name), None)

    def _doc_of(self, t: Element):
        return next(d for d in self.trig_docs if t.parent is d.root)

    def unique_trigger(self, base: str) -> str:
        if self.trigger(base) is None and base not in self.objects:
            return base
        i = 2
        while self.trigger(f"{base}{i}") is not None:
            i += 1
        return f"{base}{i}"

    def add_trigger(self, name: str, doc_index: int = 0, after: Element | None = None) -> Element:
        if not name or self.trigger(name) is not None:
            raise GameError(f"триггер «{name}» уже есть")
        t = Element("trigger", {"Name": name, "active": "0"})
        t.inline = True
        if after is not None:
            d = self._doc_of(after)
            d.root.insert_after(after, t)
        else:
            d = self.trig_docs[min(doc_index, len(self.trig_docs) - 1)]
            d.root.append(t)
        s = Element("script")
        t.append(s, blank_line=False)
        s.text = d.root.nl + "\t\t\ttrigger:Deactivate()" + d.root.nl + "\t\t"
        d.touch()
        return t

    def set_trigger(self, t: Element, attr: str, value: str):
        t.set(attr, value)
        self._doc_of(t).touch()

    def events(self, t: Element) -> list[dict]:
        return [dict(e.attrs) for e in t.elements("event")]

    def set_events(self, t: Element, events: list[dict]):
        for e in t.elements("event"):
            t.remove(e)
        at = 0
        for ev in events:
            a = {}
            for k in ("timeout", "flypath", "msgid", "eventid", "ObjName"):
                if ev.get(k, "") != "":
                    a[k] = ev[k]
            a.update({k: v for k, v in ev.items() if k not in a and v != ""})
            e = Element("event", a)
            e.inline = True
            t.insert_at(at, e, blank_line=False)
            at += 2
        self._doc_of(t).touch()

    def script(self, t: Element) -> str:
        s = t.find("script")
        return s.text if s is not None else ""

    def set_script(self, t: Element, code: str):
        s = t.find("script")
        if s is None:
            s = Element("script")
            t.append(s, blank_line=False)
        s.text = code
        self._doc_of(t).touch()

    def set_blocks(self, t: Element, blocks: list[dict]):
        self.set_script(t, lua.render_actions(blocks, quote='"', multiline=True, nl=t.nl))

    def remove_trigger(self, t: Element):
        d = self._doc_of(t)
        d.root.remove(t)
        d.touch()

    def rename_trigger(self, t: Element, new: str, dialogs: Dialogs | None = None, quests: Quests | None = None):
        old = t.get("Name")
        if new == old:
            return
        if not new or self.trigger(new) is not None:
            raise GameError(f"триггер «{new}» уже есть")
        t.set("Name", new)
        self._doc_of(t).touch()
        for other in self.triggers():
            self._replace_in_script(other, old, new)
        if dialogs:
            dialogs.replace_ref(old, new)
        if quests:
            for q in quests.items.values():
                for a in ("OnTake", "OnComplete", "OnFail", "OnCanBeGiven"):
                    if old in q.get(a):
                        q.set(a, _replace_quoted(q.get(a), old, new))
                        quests.doc.touch()

    def _replace_in_script(self, t: Element, old: str, new: str):
        code = self.script(t)
        if old in code:
            nc = _replace_quoted(code, old, new)
            if nc != code:
                self.set_script(t, nc)


def look_quat(cam: tuple, target: tuple) -> str:
    """Camera rotation looking from cam to target (x, y, z).

    Derived from original paths: q = (sin(p/2)cos(y/2), cos(p/2)sin(y/2),
    sin(p/2)sin(y/2), cos(p/2)cos(y/2)); y is heading from +Z to +X, p is
    elevation (negative looks down).
    """
    dx, dy, dz = target[0] - cam[0], target[1] - cam[1], target[2] - cam[2]
    yaw = math.atan2(dx, dz)
    pitch = math.atan2(dy, math.hypot(dx, dz) or 1e-6)
    sy, cy, sp, cp = math.sin(yaw / 2), math.cos(yaw / 2), math.sin(pitch / 2), math.cos(pitch / 2)
    return f"{sp * cy:.3f} {cp * sy:.3f} {sp * sy:.3f} {cp * cy:.3f}"


def quat_look(cam: tuple, rot: str, ground) -> tuple[float, float]:
    """Where the camera looks: ground point along the view ray (or 40 m ahead)."""
    v = parse_vec(rot)
    if len(v) < 4:
        return cam[0], cam[2] + 40.0
    qx, qy, qz, qw = v[:4]
    yaw = 2 * math.atan2(qy, qw)
    c, s = math.cos(yaw / 2), math.sin(yaw / 2)
    sp = qx / c if abs(c) > 0.3 else (qz / s if abs(s) > 1e-6 else 0.0)
    pitch = 2 * math.asin(max(-1.0, min(1.0, sp)))
    g = ground(cam[0], cam[2])
    h = cam[1] - (g if g is not None else cam[1])
    d = 40.0
    if pitch < math.radians(-2) and h > 0.5:
        d = min(max(h / math.tan(-pitch), 5.0), 400.0)
    return cam[0] + d * math.sin(yaw), cam[2] + d * math.cos(yaw)


def yaw_to_rot(deg: float) -> str:
    a = math.radians(deg) / 2
    return f"0.000 {math.sin(a):.3f} 0.000 {math.cos(a):.3f}"


def rot_to_yaw(rot: str) -> float:
    v = parse_vec(rot)
    if len(v) < 4:
        return 0.0
    return math.degrees(2 * math.atan2(v[1], v[3])) % 360


class Index:
    """Names for pickers; computed lazily and reset after saving."""

    def __init__(self, game: Game, dialogs: Dialogs, quests: Quests, towns: Towns):
        self.game, self.dialogs, self.quests, self.towns = game, dialogs, quests, towns
        self._cache: dict = {}
        self.current_map: MapData | None = None
        self.pick = None          # fn(callback(x, z)): pick a map point; set by the environment tab
        self.center = None        # fn() -> (x, z): centre of the visible map area

    def reset(self):
        self._cache.clear()

    def _raw(self, rel: str) -> str:
        p = self.game.root / self.game.resolve(rel)
        return p.read_bytes().decode("cp1251", errors="replace") if p.is_file() else ""

    def _ids(self, rel: str, rx: str) -> list[str]:
        key = (rel, rx)
        if key not in self._cache:
            self._cache[key] = list(dict.fromkeys(re.findall(rx, self._raw(rel))))
        return self._cache[key]

    def hello_users(self) -> dict[str, list[tuple[str, str]]]:
        """reply -> [(map, NPC)]: who starts a conversation with it."""
        if "hello" not in self._cache:
            out: dict[str, list] = {}
            for m in self.game.maps():
                for npc, hello in re.findall(r'Name="([^"]+)"(?:(?!/>|<Object)[\s\S])*?helloReplyNames="([^"]*)"',
                                             self._raw(f"data/maps/{m}/dynamicscene.xml")):
                    for r in hello.split():
                        out.setdefault(r, []).append((m, npc))
            self._cache["hello"] = out
        return self._cache["hello"]

    def npc_models(self) -> list[str]:
        if "models" not in self._cache:
            c: dict[str, int] = {}
            for m in self.game.maps():
                for mm in re.findall(r'Prototype="NPC"\s+ModelName="([^"]+)"', self._raw(f"data/maps/{m}/dynamicscene.xml")):
                    c[mm] = c.get(mm, 0) + 1
            self._cache["models"] = sorted(c, key=lambda k: -c[k])
        return self._cache["models"]

    def belongs(self) -> list[tuple[str, str]]:
        if "belongs" not in self._cache:
            raw = self._raw("data/if/strings/clansdiz.xml")
            out = re.findall(r'id="Belong_(\d+)"\s+value="([^"]*)"', raw)
            self._cache["belongs"] = out
        return self._cache["belongs"]

    def choices(self, type_: str) -> list[str]:
        g = self.game
        if type_ == "quest":
            return list(self.quests.items)
        if type_ == "reply":
            return list(self.dialogs.items)
        if type_ == "trigger":
            return [t.get("Name") for t in self.current_map.triggers()] if self.current_map else self.all_triggers()
        if type_ == "npc_model":
            return self.npc_models()
        if type_ == "portrait":     # models used as message portraits on any map, masks first
            if "portraits" not in self._cache:
                raw = "".join(self._raw(f"data/maps/{m}/strings.xml") for m in g.maps())
                found = re.findall(r'modelName="([^"]+)"', raw)
                names = sorted(set(found), key=lambda k: (not k.startswith("mask_"), -found.count(k)))
                self._cache["portraits"] = names + [n for n in self.npc_models() if n not in names]
            return self._cache["portraits"]
        if type_ == "vehicle":
            return self._ids("data/gamedata/gameobjects/vehicles.xml", r'Class\s*=\s*"Vehicle"\s+Name\s*=\s*"(\w+)"')
        if type_ == "tactic":
            return self._ids("data/gamedata/gameobjects/tactics.xml", r'Class\s*=\s*"TeamTactic\w*"\s+Name\s*=\s*"(\w+)"')
        if type_ == "gun":
            out = []
            for f in ("smallguns", "bigguns", "giantguns", "sideguns"):
                out += self._ids(f"data/gamedata/gameobjects/{f}.xml", r'Class\s*=\s*"\w*Gun\w*"\s+Name\s*=\s*"(\w+)"')
            return out
        if type_ == "actor":
            if "actors" not in self._cache:
                raw = "".join(self._raw(f"data/maps/{m}/{f}") for m in g.maps() for f in ("triggers.xml", "cinematriggers.xml"))
                found = re.findall(r'CreateNewDummyObject\s*\(\s*"(\w+)"', raw)
                self._cache["actors"] = sorted(set(found), key=lambda k: (not k.startswith("dweller"), -found.count(k)))
            return self._cache["actors"]
        if type_ in ("campath", "extpath"):
            return self.current_map.paths("cam" if type_ == "campath" else "ext") if self.current_map else []
        if type_ == "strnum":
            return list(self.current_map.messages()) if self.current_map else []
        if type_.startswith("obj:"):
            return self.objects_of(type_[4:])
        if type_ == "loc":          # locations only: what location events can refer to
            m = self.current_map
            return [n for n, o in m.objects.items() if m.kind(o) == "loc"] if m else []
        if type_ == "obj":
            m = self.current_map
            if not m:
                return []
            pairs = [(n, o.get("Prototype")) for n, o in m.objects.items()]
            return self._useful(pairs) + self._scripted(m)
        if type_ == "qitem":
            return self._ids("data/gamedata/gameobjects/questitems.xml", r'\bName\s*=\s*"([^"]+)"')
        if type_ == "item":
            return (self._ids("data/gamedata/gameobjects/wares.xml", r'\bName\s*=\s*"([^"]+)"')
                    + self._ids("data/gamedata/gameobjects/questitems.xml", r'\bName\s*=\s*"([^"]+)"'))
        if type_ == "book":
            return [i for i in self._ids("data/if/strings/uibooks.xml", r'\bid="([^"]+)"') if not i.endswith("_diz")]
        if type_ == "history":
            return self._ids("data/if/strings/uihistory.xml", r'\bid="([^"]+)"')
        if type_ == "strid":
            return self._ids("data/if/strings/ui.xml", r'\bid="(fm_[^"]+)"') or ["fm_history_got"]
        if type_ == "map":
            return g.maps()
        if type_ == "belong":
            return [b for b, _ in self.belongs()]
        if type_ == "town_proto":
            return list(self.towns.protos)
        if type_ == "var":
            if "vars" not in self._cache:
                raw = self._raw(DIALOGS) + "".join(self._raw(f"data/maps/{m}/triggers.xml") for m in g.maps())
                self._cache["vars"] = list(dict.fromkeys(re.findall(r"[GS]etVar\(\s*['\"]([^'\"]+)", raw)))
            return self._cache["vars"]
        return []

    def objects_of(self, map_name: str) -> list[str]:
        """Map objects worth referencing (no trees or fences)."""
        key = ("objs", map_name)
        if key not in self._cache:
            raw = self._raw(f"data/maps/{map_name}/dynamicscene.xml")
            pairs = re.findall(r'Name="([^"]+)"\s+(?:Belong="[^"]*"\s+)?Prototype="([^"]+)"', raw)
            self._cache[key] = self._useful(pairs)
        return self._cache[key]

    DECOR = ("Breakable", "Cable", "lamppost", "r3_tropic", "r4_desert", "factory_box")
    PLACES = ("genericLocation", "NPC")

    def _useful(self, pairs: list[tuple[str, str]]) -> list[str]:
        """Drop scenery: trees, fences, wires and any prototype placed in bulk."""
        count: dict[str, int] = {}
        for _n, p in pairs:
            count[p] = count.get(p, 0) + 1
        return [n for n, p in pairs if n and (p in self.PLACES or p in self.towns.protos
                                              or (count[p] <= 12 and not p.startswith(self.DECOR)))]

    def _scripted(self, m) -> list[str]:
        """Names created by the map's scripts: teams, their vehicles, actors."""
        out = []
        for t in m.triggers():
            code = m.script(t)
            for name, protos in re.findall(r'(?:TeamCreate|CreateTeam|TeamCreateWithWarez)\s*\(\s*"(\w+)"[^{}]*\{([^{}]*)\}', code):
                out.append(name)
                out += [f"{name}_vehicle_{i}" for i in range(len(re.findall(r'"', protos)) // 2)]
            out += re.findall(r'CreateVehicleEx\s*\(\s*"\w+"\s*,\s*"(\w+)"', code)
            out += re.findall(r'CreateNewDummyObject\s*\(\s*"\w+"\s*,\s*"(\w+)"', code)
        return list(dict.fromkeys(out))

    def height(self, x: float, z: float) -> float | None:
        return self.game.height(self.current_map.name, x, z) if self.current_map else None

    def all_triggers(self) -> list[str]:
        if "trigs" not in self._cache:
            out = []
            for m in self.game.maps():
                out += re.findall(r'<trigger\s+Name="([^"]+)"', self._raw(f"data/maps/{m}/triggers.xml"))
            self._cache["trigs"] = list(dict.fromkeys(out))
        return self._cache["trigs"]

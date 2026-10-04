"""Trigger events, cutscene timeline and camera/drive path editor."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QScrollArea, QVBoxLayout, QWidget)

from . import lua, scene
from .game import GameError
from .model import EVENT_BY, EVENTS, look_quat, quat_look
from .widgets import (BlockList, Choice, NameBox, add_row, ask_text, clear_layout, confirm, head, minus_button,
                      plus_button, warn)

CINE_EVENTS = ("GE_END_CINEMATIC", "GE_SKIP_CINEMATIC", "GE_CINEMATIC_ENTER_FADE_IN", "GE_START_CINEMATIC_FLY",
               "GE_START_CINEMATIC_MSG")
SECTION_TITLES = {"fade": "Сразу после fade-in", "fly": "Когда начался пролёт {}", "msg": "Когда показана реплика {}",
                  "time": "Через {} с после запуска", "end": "Когда ролик закончился или пропущен"}


class EventsEditor(QWidget):
    """The 'when' rows: event and its parameter."""

    changed = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.map = None
        self.trigger = None
        self.events: list[dict] = []
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(3)

    def set_trigger(self, mapdata, t):
        self.map, self.trigger = mapdata, t
        self.events = mapdata.events(t) if t is not None else []
        self._fill()

    def _fill(self):
        clear_layout(self.lay)
        idx = self.app.index
        for i, ev in enumerate(self.events):
            l = QHBoxLayout()
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(3)
            eid = ev.get("eventid", "")
            c = Choice([(e[0], e[1]) for e in EVENTS], eid)
            for n, e in enumerate(EVENTS):
                c.setItemData(n, e[0], Qt.ToolTipRole)
            c.setToolTip("<event " + " ".join(f'{k}="{v}"' for k, v in ev.items()) + " />")
            c.picked.connect(lambda v, i=i: self._set(i, "eventid", v, True))
            l.addWidget(c)
            kind = EVENT_BY.get(eid, ("", "", "obj"))[2]
            if kind == "timeout":
                e = QLineEdit(ev.get("timeout", "0").strip())
                e.setFixedWidth(60)
                e.editingFinished.connect(lambda e=e, i=i: self._set(i, "timeout", e.text().strip() or "0"))
                l.addWidget(e)
                l.addStretch(1)
            elif kind == "fly":
                fp = NameBox(idx, "campath", ev.get("flypath", ""))
                fp.committed.connect(lambda v, i=i: self._set(i, "flypath", v))
                l.addWidget(fp, 1)
            elif kind == "msg":
                mi = NameBox(idx, "strnum", ev.get("msgid", ""))
                mi.committed.connect(lambda v, i=i: self._set(i, "msgid", v))
                l.addWidget(mi, 1)
            elif kind and eid not in CINE_EVENTS:
                box = NameBox(idx, "loc" if kind == "loc" else "obj", ev.get("ObjName", ""))
                box.committed.connect(lambda v, i=i: self._set(i, "ObjName", v))
                l.addWidget(box, 1)
            else:
                l.addStretch(1)
            rm = minus_button()
            rm.clicked.connect(lambda _=False, i=i: self._del(i))
            l.addWidget(rm)
            self.lay.addLayout(l)

    def _commit(self):
        self.map.set_events(self.trigger, self.events)
        self.changed.emit()

    def _set(self, i, key, value, refill=False):
        ev = self.events[i]
        ev[key] = value
        if key == "eventid":
            kind = EVENT_BY.get(value, ("", "", "obj"))[2]
            if kind == "timeout":
                ev.setdefault("timeout", "0")
                ev.pop("ObjName", None)
            else:
                ev.pop("timeout", None)
                if value in CINE_EVENTS:      # the engine sends cutscene events to the player
                    ev["ObjName"] = "Player1"
            if kind != "fly":
                ev.pop("flypath", None)
            if kind != "msg":
                ev.pop("msgid", None)
        self._commit()
        if refill:
            self._fill()

    def _del(self, i):
        del self.events[i]
        self._commit()
        self._fill()

    def add(self):
        if self.trigger is None:
            return
        self.events.append({"eventid": "GE_OBJECT_ENTERS_LOCATION", "ObjName": ""})
        self._commit()
        self._fill()


def block_marks(blocks: list[dict]) -> list[tuple[float, float, str]]:
    """Map points mentioned by blocks: team spawn, destination, actor position."""
    out = []

    def add(v, label):
        p = str(v or "").split()
        if len(p) >= 3:
            try:
                out.append((float(p[0]), float(p[2]), label))
            except ValueError:
                pass
    for b in blocks:
        k = b["k"]
        if k == "team":
            add(b.get("pos"), b.get("name", ""))
            add(b.get("walk"), "→ " + b.get("name", ""))
        elif k in ("model", "vehicle"):
            add(b.get("pos"), b.get("name", ""))
        elif k == "obj":
            for o in b.get("ops", []):
                if o["k"] in ("pos", "setpos", "dest"):
                    add(o.get("pos"), ("→ " if o["k"] == "dest" else "") + scene.ref_title(b.get("ref", "")))
    return out


class CutsceneView(QScrollArea):
    """A cutscene as one timeline: the start trigger and everything it activates.

    Stored as plain triggers; the links between them (TActivate, TDeactivate,
    trigger:Deactivate) are maintained here and hidden from the list.
    """

    changed = Signal()            # blocks edited: refresh map marks
    renamed = Signal()            # name or active flag changed: refresh the list

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.map = None
        self.start = None
        self.lists: list[tuple[object, BlockList]] = []
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 4, 0)
        self.lay.setSpacing(5)
        self.setWidget(self.body)

    def names(self) -> set[str]:
        return {t.get("Name") for _k, _a, t in self.map.family(self.start)} | {self.start.get("Name")}

    def all_blocks(self) -> list[dict]:
        return [b for _t, bl in self.lists for b in bl.blocks]

    def show_scene(self, mapdata, start):
        self.map, self.start = mapdata, start
        self.rebuild()

    def rebuild(self):
        clear_layout(self.lay)
        self.lists = []
        m, s = self.map, self.start
        fam = m.family(s)
        names = self.names()
        hide = lambda b: b["k"] in ("selfoff", "cinefilt", "gamefilt") or (b["k"] == "trigger" and b.get("trigger") in names)

        name = QLineEdit(s.get("Name"))
        name.setToolTip("Имя запускающего триггера")
        name.editingFinished.connect(lambda: self._rename(name))
        self.lay.addWidget(name)
        act = QCheckBox("Активен с начала игры")
        act.setChecked(s.get("active") == "1")
        act.clicked.connect(lambda on: (m.set_trigger(s, "active", "1" if on else "0"), self.renamed.emit()))
        self.lay.addWidget(act)
        for w in self._warnings(fam):
            l = QLabel(w)
            l.setWordWrap(True)
            l.setStyleSheet("color: #7a2e12;")
            self.lay.addWidget(l)

        ev = EventsEditor(self.app)
        ev.set_trigger(m, s)
        hr = QHBoxLayout()
        hr.addWidget(head("Когда"))
        hr.addStretch(1)
        ae = plus_button()
        ae.clicked.connect(ev.add)
        hr.addWidget(ae)
        self.lay.addLayout(hr)
        self.lay.addWidget(ev)

        self._section("Запуск ролика", s, hide, None)
        for kind, arg, t in fam:
            self._section(SECTION_TITLES[kind].format(arg), t, hide, t)
        add = plus_button("Добавить часть ролика")
        add.clicked.connect(lambda: self._add_menu(add))
        add_row(self.lay, add)
        self.lay.addStretch(1)

    def _section(self, title: str, t, hide, removable):
        hr = QHBoxLayout()
        h = head(title)
        evs = "\n".join("<event " + " ".join(f'{k}="{v}"' for k, v in e.items()) + " />" for e in self.map.events(t))
        h.setToolTip(f'<trigger Name="{t.get("Name")}" active="{t.get("active")}">\n{evs}')
        hr.addWidget(h)
        hr.addStretch(1)
        if removable is not None:
            rm = minus_button("Убрать эту часть ролика")
            rm.clicked.connect(lambda: self._remove(removable))
            hr.addWidget(rm)
        self.lay.addLayout(hr)
        bl = BlockList(self.app.index, "act", "trigger", self.app.quests.title, hide=hide)
        bl.set_blocks(lua.parse_actions(self.map.script(t)))
        bl.changed.connect(lambda t=t, bl=bl: (self.map.set_blocks(t, bl.blocks), self.changed.emit()))
        bl.request.connect(lambda what, arg: self.window().findChild(QWidget, "eventsTab").handle_request(what, arg))
        self.lay.addWidget(bl)
        self.lists.append((t, bl))

    def _warnings(self, fam) -> list[str]:
        out = []
        start_blocks = lua.parse_actions(self.map.script(self.start))
        ends = [t for k, _a, t in fam if k == "end"]
        if not ends:
            out.append("У ролика нет концовки: камера и отношения сами не вернутся.")
        elif any(b["k"] == "neutral" for b in start_blocks) and \
                not any(b["k"] == "restore" for t in ends for b in lua.parse_actions(self.map.script(t))):
            out.append("Запуск делает всех нейтральными, а концовка отношения не возвращает.")
        chain = [b for b in start_blocks if b["k"] in ("fly", "flylinked", "flyaround")]
        for n, (a, b) in enumerate(zip(chain, chain[1:]), 1):
            if str(a.get("fout")) == "1" and str(b.get("fin")) == "1":
                out.append(f"Пролёты {n} и {n + 1}: fade-in в конце одного и сразу в начале другого — в игре это ломается.")
        # critical blocks belong to the end trigger: other parts may not run when the cutscene is skipped
        own = {t.get("Name") for _k, _a, t in fam} | {self.start.get("Name")}
        for kind, arg, t in fam:
            if kind == "end":
                continue
            found = []
            for blk in lua.parse_actions(self.map.script(t)):
                if blk["k"] == "quest":
                    found.append("квест")
                elif blk["k"] == "team":
                    found.append("создание команды")
                elif blk["k"] == "trigger" and blk.get("do") == "on" and blk.get("trigger") not in own:
                    found.append("включение триггера " + str(blk.get("trigger")))
            if found:
                out.append(f"«{SECTION_TITLES[kind].format(arg)}»: {', '.join(dict.fromkeys(found))}. При пропуске ролика "
                           f"эта часть может не сработать — перенесите в «{SECTION_TITLES['end']}».")
        shots = {b.get("path") for b in start_blocks if b["k"] in ("fly", "flylinked")}
        for k, a, _t in fam:
            if k == "fly" and a not in shots:
                out.append(f"Пролёта «{a}» нет в запуске ролика — эта часть не сработает.")
        return out

    # --- cutscene parts ---
    def _add_menu(self, anchor):
        m = QMenu(self)
        fam = self.map.family(self.start)
        have = {(k, a) for k, a, _t in fam}
        blocks = self.all_blocks()
        if ("fade", "") not in have:
            m.addAction("Сразу после fade-in", lambda: self._add("fade", ""))
        paths = [b.get("path") for b in blocks if b["k"] in ("fly", "flylinked") and b.get("path")]
        paths = [p for p in dict.fromkeys(paths) if ("fly", p) not in have]
        if paths:
            sub = m.addMenu("Когда начался пролёт")
            for p in paths:
                sub.addAction(p, lambda p=p: self._add("fly", p))
        ids = [str(b.get("id")) for b in blocks if b["k"] == "cinemsg"]
        ids = [i for i in dict.fromkeys(ids) if ("msg", i) not in have]
        if ids:
            sub = m.addMenu("Когда показана реплика")
            for i in ids:
                sub.addAction(i, lambda i=i: self._add("msg", i))
        m.addAction("Через несколько секунд после запуска", self._add_timer)
        if not any(k == "end" for k, _a, _t in fam):
            m.addAction("Когда ролик закончился или пропущен", lambda: self._add("end", ""))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _add_timer(self):
        n = ask_text(self, "Часть ролика", "Через сколько секунд после запуска", "3")
        if n:
            self._add("time", n.replace(",", "."))

    def _add(self, kind: str, arg: str):
        add_member(self.map, self.start, kind, arg)
        self.rebuild()
        self.changed.emit()

    def _remove(self, t):
        if not confirm(self, f"Убрать часть ролика «{t.get('Name')}» вместе с её действиями?"):
            return
        remove_member(self.map, self.start, t)
        self.rebuild()
        self.changed.emit()

    def _rename(self, w):
        new = w.text().strip()
        if new == self.start.get("Name"):
            return
        try:
            self.map.rename_trigger(self.start, new, self.app.dialogs, self.app.quests)
        except GameError as e:
            w.setText(self.start.get("Name"))
            warn(self, str(e))
            return
        self.renamed.emit()


def _blocks(m, t) -> list[dict]:
    return lua.parse_actions(m.script(t))


def _insert_before_tail(blocks: list[dict], b: dict):
    i = len(blocks)
    while i and blocks[i - 1]["k"] == "selfoff":
        i -= 1
    blocks.insert(i, b)


def add_member(m, start, kind: str, arg: str):
    """Add a cutscene trigger of the given role and link it to start and end."""
    s = start.get("Name")
    base = {"fade": f"{s}_fade", "fly": f"{s}_{arg}", "msg": f"{s}_msg{arg}", "time": f"{s}_after{arg.replace('.', '_')}",
            "end": f"{s}_end"}[kind]
    fam = m.family(start)
    t = m.add_trigger(m.unique_trigger(base), after=fam[-1][2] if fam and m._doc_of(fam[-1][2]) is m._doc_of(start) else start)
    ev = {"fade": {"eventid": "GE_CINEMATIC_ENTER_FADE_IN", "ObjName": "Player1"},
          "fly": {"flypath": arg, "eventid": "GE_START_CINEMATIC_FLY", "ObjName": "Player1"},
          "msg": {"msgid": arg, "eventid": "GE_START_CINEMATIC_MSG", "ObjName": "Player1"},
          "time": {"timeout": arg, "eventid": "GE_TIME_PERIOD"}}
    if kind == "end":
        m.set_events(t, [{"eventid": "GE_END_CINEMATIC", "ObjName": "Player1"},
                         {"eventid": "GE_SKIP_CINEMATIC", "ObjName": "Player1"}])
        blocks = []
        if any(b["k"] == "neutral" for b in _blocks(m, start)):
            blocks.append({"k": "restore"})
        blocks.append({"k": "cambehind"})
        # on skip the other parts are no longer needed
        blocks += [{"k": "trigger", "do": "off", "trigger": x.get("Name")} for _k, _a, x in fam]
        blocks.append({"k": "selfoff"})
        m.set_blocks(t, blocks)
    else:
        m.set_events(t, [ev[kind]])
        for k, _a, e in fam:
            if k == "end":
                eb = _blocks(m, e)
                _insert_before_tail(eb, {"k": "trigger", "do": "off", "trigger": t.get("Name")})
                m.set_blocks(e, eb)
    sb = _blocks(m, start)
    at = max((i for i, b in enumerate(sb) if b["k"] in ("cinestart", "trigger")), default=-1)
    new = {"k": "trigger", "do": "on", "trigger": t.get("Name")}
    if at >= 0:
        sb.insert(at + 1, new)
    else:
        _insert_before_tail(sb, new)
    m.set_blocks(start, sb)
    return t


def remove_member(m, start, t):
    name = t.get("Name")
    for other in [start] + [x for _k, _a, x in m.family(start)]:
        if other is t:
            continue
        bs = _blocks(m, other)
        kept = [b for b in bs if not (b["k"] == "trigger" and b.get("trigger") == name)]
        if len(kept) != len(bs):
            m.set_blocks(other, kept)
    m.remove_trigger(t)


def new_cutscene(m, name: str):
    """Cutscene stub: stop the player, orbit the camera, restore everything."""
    start = m.add_trigger(name, doc_index=len(m.trig_docs) - 1)
    m.set_trigger(start, "active", "1")
    m.set_events(start, [{"eventid": "GE_OBJECT_ENTERS_LOCATION", "ObjName": ""}])
    m.set_blocks(start, [{"k": "neutral"}, scene.new_block("stop"), scene.new_block("flyaround"), {"k": "cinestart"},
                         {"k": "selfoff"}])
    add_member(m, start, "end", "")
    return start


class PathEditor(QScrollArea):
    """Camera or drive path: points as a list and on the map."""

    changed = Signal()
    renamed = Signal(str)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.map = None
        self.kind = self.name = ""
        self.pts: list[dict] = []
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 4, 0)
        self.lay.setSpacing(4)
        self.setWidget(self.body)

    def ground(self, x, z):
        return self.app.game.height(self.map.name, x, z)

    def show_path(self, mapdata, kind: str, name: str):
        self.map, self.kind, self.name = mapdata, kind, name
        self.pts = mapdata.path_points(kind, name)
        for p in self.pts:
            self._look(p)
        self.rebuild()

    def _look(self, p: dict):
        if self.kind == "cam":
            p["lx"], p["lz"] = quat_look((p["x"], p["y"], p["z"]), p["rot"], self.ground)

    def rebuild(self):
        clear_layout(self.lay)
        name = QLineEdit(self.name)
        name.setToolTip("Имя пути")
        name.editingFinished.connect(lambda: self._rename(name))
        self.lay.addWidget(name)
        cam = self.kind == "cam"
        for i, p in enumerate(self.pts):
            l = QHBoxLayout()
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(3)
            n = QLabel(str(i + 1))
            n.setObjectName("dim")
            n.setFixedWidth(16)
            l.addWidget(n)
            x, z = QLineEdit(f"{p['x']:.1f}"), QLineEdit(f"{p['z']:.1f}")
            for e in (x, z):
                e.setFixedWidth(64)
                l.addWidget(e)
            x.editingFinished.connect(lambda i=i, x=x, z=z: self._typed(i, x, z, None))
            z.editingFinished.connect(lambda i=i, x=x, z=z: self._typed(i, x, z, None))
            if cam:
                g = self.ground(p["x"], p["z"])
                h = QLineEdit(f"{p['y'] - (g if g is not None else p['y']):.1f}")
                h.setFixedWidth(46)
                h.setToolTip("Высота камеры над землёй")
                h.editingFinished.connect(lambda i=i, x=x, z=z, h=h: self._typed(i, x, z, h))
                up = QLabel("выше земли на")
                up.setObjectName("dim")
                l.addWidget(up)
                l.addWidget(h)
            l.addStretch(1)
            rm = minus_button()
            rm.clicked.connect(lambda _=False, i=i: self._del(i))
            l.addWidget(rm)
            self.lay.addLayout(l)
        add = plus_button("Добавить точку щелчком по карте")
        add.clicked.connect(self._add)
        add_row(self.lay, add)
        self.lay.addStretch(1)

    def _commit(self, rebuild=True):
        self.map.set_path_points(self.kind, self.name, self.pts)
        if rebuild:
            self.rebuild()
        self.changed.emit()

    def _typed(self, i, xe, ze, he):
        p = self.pts[i]
        try:
            x, z = float(xe.text().replace(",", ".")), float(ze.text().replace(",", "."))
            h = float(he.text().replace(",", ".")) if he is not None else None
        except ValueError:
            return
        self.move_point(i, "pt", x, z, h)

    def move_point(self, i: int, what: str, x: float, z: float, height: float | None = None):
        """Moving a point keeps the camera rotation; moving the look handle recomputes it."""
        p = self.pts[i]
        if what == "look":
            g = self.ground(x, z)
            p["rot"] = look_quat((p["x"], p["y"], p["z"]), (x, g if g is not None else p["y"], z))
            p["lx"], p["lz"] = x, z
        else:
            if self.kind == "cam":
                g0 = self.ground(p["x"], p["z"])
                above = height if height is not None else p["y"] - (g0 if g0 is not None else p["y"])
                g1 = self.ground(x, z)
                p["y"] = (g1 if g1 is not None else p["y"]) + above
            p["x"], p["z"] = x, z
            self._look(p)
        self._commit()

    def _del(self, i):
        del self.pts[i]
        self._commit()

    def _add(self):
        if self.app.index.pick:
            self.app.index.pick(self._add_at)

    def _add_at(self, x: float, z: float):
        if self.kind == "cam":
            g = self.ground(x, z) or 0.0
            p = {"x": x, "y": g + 6.0, "z": z}
            # a new point looks where the previous one does; the first looks 40 m north
            tx, tz = (self.pts[-1]["lx"], self.pts[-1]["lz"]) if self.pts else (x, z + 40.0)
            tg = self.ground(tx, tz)
            p["rot"] = look_quat((p["x"], p["y"], p["z"]), (tx, tg if tg is not None else g, tz))
            self._look(p)
        else:
            p = {"x": x, "z": z}
        self.pts.append(p)
        self._commit()

    def _rename(self, w):
        new = w.text().strip()
        if new == self.name:
            return
        try:
            self.map.rename_path(self.kind, self.name, new)
        except GameError as e:
            w.setText(self.name)
            warn(self, str(e))
            return
        self.name = new
        self.renamed.emit(new)

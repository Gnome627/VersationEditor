"""Field editors and rows of compound blocks."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from . import lua, scene
from .widgets import NameBox, add_row, minus_button, plus_button


def _num(text: str, default: float = 0.0) -> float:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return default


class VecEdit(QWidget):
    """World point: X and Z typed or picked on the map; height comes from terrain."""

    changed = Signal(str)

    def __init__(self, index, value: str = "", optional: bool = False):
        super().__init__()
        self.index, self.optional = index, optional
        self._y = "0"
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(3)
        self.x, self.z = QLineEdit(), QLineEdit()
        for e, ph in ((self.x, "X"), (self.z, "Z")):
            e.setPlaceholderText(ph)
            e.setFixedWidth(64)
            e.editingFinished.connect(self._typed)
            l.addWidget(e)
        self.pick = QPushButton("на карте")
        self.pick.setObjectName("flat")
        self.pick.setCursor(Qt.PointingHandCursor)
        self.pick.setToolTip("Указать точку щелчком по карте")
        self.pick.clicked.connect(self._pick)
        self.pick.setVisible(bool(index and index.pick))
        l.addWidget(self.pick)
        l.addStretch(1)
        self.set_value(value)

    def set_value(self, v: str):
        p = v.split()
        self._value = v if len(p) >= 3 else ""
        if len(p) >= 3:
            self._y = p[1]
            self.x.setText(f"{_num(p[0]):.1f}")
            self.z.setText(f"{_num(p[2]):.1f}")
        else:
            self.x.clear()
            self.z.clear()

    def _set(self, x: float, z: float):
        h = self.index.height(x, z) if self.index else None
        y = f"{h:.3f}" if h is not None else self._y
        self._y = y
        v = f"{x:.3f} {y} {z:.3f}"
        self.set_value(v)
        self.changed.emit(v)

    def _typed(self):
        if not self.x.text().strip() and not self.z.text().strip():
            if self.optional and self._value:
                self._value = ""
                self.changed.emit("")
            return
        x, z = _num(self.x.text()), _num(self.z.text())
        p = self._value.split()
        if len(p) >= 3 and abs(_num(p[0]) - x) < 0.05 and abs(_num(p[2]) - z) < 0.05:
            return
        self._set(x, z)

    def _pick(self):
        if self.index and self.index.pick:
            self.index.pick(self._set)


class QuatEdit(QWidget):
    """Rotation: yaw in degrees for a pure vertical rotation, raw quaternion otherwise."""

    changed = Signal(str)

    def __init__(self, value: str = ""):
        super().__init__()
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(3)
        self.e = QLineEdit()
        self.e.editingFinished.connect(self._typed)
        l.addWidget(self.e)
        self.unit = QLabel("°")
        l.addWidget(self.unit)
        l.addStretch(1)
        self.value = value or "0.000 0.000 0.000 1.000"
        yaw = scene.quat_yaw(self.value)
        self.raw = yaw is None
        self.unit.setVisible(not self.raw)
        self.e.setFixedWidth(170 if self.raw else 56)
        self.e.setText(self.value if self.raw else f"{yaw:.0f}")
        if self.raw:
            self.e.setToolTip("Кватернион x y z w")

    def _typed(self):
        t = self.e.text().strip()
        if self.raw:
            v = " ".join(t.replace(",", " ").split())
            if len(v.split()) != 4:
                return
        else:
            v = scene.yaw_quat(_num(t))
        if v != self.value:
            self.value = v
            self.changed.emit(v)


class ObjRefEdit(NameBox):
    """Object by name; empty means the player's vehicle."""

    picked = Signal(str)

    def __init__(self, index, ref: str, empty: str = "игрок", empty_value: str = scene.PLAYER):
        super().__init__(index, "obj", "" if ref in (scene.PLAYER, "") else ref)
        self.empty_value = empty_value
        self.setPlaceholderText(empty)
        self.committed.connect(lambda v: self.picked.emit(v if v else self.empty_value))


def check_box(label: str, on: bool, fn) -> QCheckBox:
    c = QCheckBox(label)
    c.setChecked(on)
    c.clicked.connect(fn)
    return c


def truthy(v) -> bool:
    return str(v).strip() in ("1", "true")


def field_editor(bl, b: dict, f: lua.Field):
    """Editor for the new field types, or None for ordinary ones."""
    def setv(val, name=f.name):
        b[name] = val
        bl._emit()
    if f.type == "vec":
        w = VecEdit(bl.index, str(b.get(f.name, "")))
        w.changed.connect(setv)
        return w
    if f.type == "quat":
        w = QuatEdit(str(b.get(f.name, "")))
        w.changed.connect(setv)
        return w
    if f.type == "check":
        style = "true" if str(b.get(f.name, f.default)) in ("true", "false") or f.default in ("true", "false") else "1"
        w = check_box(f.label, truthy(b.get(f.name, f.default)),
                      lambda on: setv(("true" if on else "false") if style == "true" else ("1" if on else "0")))
        return w
    return None


def _line(*widgets, stretch_last=False):
    l = QHBoxLayout()
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(4)
    for i, w in enumerate(widgets):
        if isinstance(w, str):
            w = QLabel(w)
        l.addWidget(w, 1 if (stretch_last and i == len(widgets) - 1) else 0)
    if not stretch_last:
        l.addStretch(1)
    return l


def _num_edit(b: dict, key: str, bl, width: int = 50, default: str = "0") -> QLineEdit:
    e = QLineEdit(str(b.get(key, default)))
    e.setFixedWidth(width)

    def done():
        v = e.text().strip().replace(",", ".") or default
        if v != b.get(key):
            b[key] = v
            bl._emit()
    e.editingFinished.connect(done)
    return e


def _fades(b: dict, bl):
    def setk(k):
        return lambda on: (b.__setitem__(k, "1" if on else "0"), bl._emit())
    return (check_box("fade-in в начале", truthy(b.get("fin")), setk("fin")),
            check_box("в конце", truthy(b.get("fout")), setk("fout")))


def build(bl, i: int, b: dict, col: QVBoxLayout) -> bool:
    """Build the row content of a compound block; False for ordinary blocks."""
    k = b["k"]
    if k == "obj":
        _obj(bl, b, col)
    elif k == "team":
        _team(bl, i, b, col)
    elif k == "flyaround":
        _flyaround(bl, b, col)
    elif k == "flylinked":
        _flylinked(bl, b, col)
    elif k == "cinemsg" and bl.index and bl.index.current_map is not None:
        _message(bl, b, col)
    else:
        return False
    return True


def _obj(bl, b: dict, col: QVBoxLayout):
    from .widgets import BlockList
    ref = ObjRefEdit(bl.index, b.get("ref", scene.PLAYER))

    def retarget(v):
        scene.retarget(b, v)
        bl._emit()
    ref.picked.connect(retarget)
    col.addLayout(_line("Обращение к объекту", ref, stretch_last=True))
    ops = BlockList(bl.index, "ops", bl.where, bl.quest_titles)
    ops.set_blocks(b.setdefault("ops", []))
    ops.changed.connect(bl._emit)
    ops.setContentsMargins(8, 0, 0, 0)
    col.addWidget(ops)


def _team(bl, i: int, b: dict, col: QVBoxLayout):
    idx = bl.index
    name = QLineEdit(b.get("name", ""))

    def rename():
        new = name.text().strip()
        old = b["name"]
        if not new or new == old:
            return
        b["name"] = new
        for o in bl.blocks:            # vehicle setup refers to the team name: follow the rename
            if o["k"] == "obj" and (o.get("ref") == old or str(o.get("ref", "")).startswith(old + "_vehicle_")):
                scene.retarget(o, new + o["ref"][len(old):])
        bl._rebuild()
        bl._emit()
    name.editingFinished.connect(rename)
    col.addLayout(_line("Создание команды", name, stretch_last=True))

    belong = NameBox(idx, "belong", str(b.get("belong", "")))
    belong.setFixedWidth(60)
    clan = QLabel(dict(idx.belongs()).get(str(b.get("belong")), "") if idx else "")

    def set_belong(v):
        b["belong"] = v or "1002"
        clan.setText(dict(idx.belongs()).get(b["belong"], ""))
        bl._emit()
    belong.committed.connect(set_belong)
    col.addLayout(_line("Группировка", belong, clan))

    pos = VecEdit(idx, b.get("pos", ""))
    pos.changed.connect(lambda v: (b.__setitem__("pos", v), bl._emit()))
    col.addLayout(_line("Точка появления", pos, stretch_last=True))
    walk = VecEdit(idx, b.get("walk", ""), optional=True)
    walk.changed.connect(lambda v: (b.__setitem__("walk", v), bl._emit()))
    col.addLayout(_line("Сразу едет в точку", walk, stretch_last=True))
    col.addWidget(check_box("Случайный товар в кузовах", b.get("wares") == "1",
                            lambda on: (b.__setitem__("wares", "1" if on else "0"), bl._emit())))

    protos = b.setdefault("protos", [])
    for n, p in enumerate(protos):
        box = NameBox(idx, "vehicle", p)
        box.committed.connect(lambda v, n=n: (protos.__setitem__(n, v), bl._emit()))
        tune = QPushButton("обращение к машине")
        tune.setObjectName("flat")
        tune.setCursor(Qt.PointingHandCursor)
        tune.setToolTip(f"Блок с настройками машины {b['name']}_vehicle_{n}")
        tune.clicked.connect(lambda _=False, n=n: _tune(bl, i, b, f"{b['name']}_vehicle_{n}"))
        rm = minus_button()
        rm.clicked.connect(lambda _=False, n=n: _drop_vehicle(bl, b, n))
        num = QLabel(str(n))
        num.setObjectName("dim")
        col.addLayout(_line(num, box, tune, rm))
    add = plus_button("Добавить машину")

    def add_vehicle():
        choices = idx.choices("vehicle") if idx else []
        protos.append(protos[-1] if protos else (choices[0] if choices else ""))
        bl._rebuild()
        bl._emit()
    add.clicked.connect(add_vehicle)
    team_ops = QPushButton("обращение к команде")
    team_ops.setObjectName("flat")
    team_ops.setCursor(Qt.PointingHandCursor)
    team_ops.clicked.connect(lambda: _tune(bl, i, b, b["name"]))
    die = QPushButton("триггер на уничтожение")
    die.setObjectName("flat")
    die.setCursor(Qt.PointingHandCursor)
    die.setToolTip("Триггер, который сработает, когда команда уничтожена")
    die.clicked.connect(lambda: bl.request.emit("die", b["name"]))
    col.addLayout(_line(add, team_ops, die))


def _tune(bl, i: int, team: dict, ref: str):
    """Add an object-access block for a team vehicle (or the team) right after it."""
    name = team["name"]
    at = i + 1
    while at < len(bl.blocks) and bl.blocks[at]["k"] == "obj" and \
            (bl.blocks[at].get("ref") == name or str(bl.blocks[at].get("ref", "")).startswith(name + "_vehicle_")):
        if bl.blocks[at].get("ref") == ref:
            return                       # already there
        at += 1
    blk = scene.new_block("obj")
    blk["ref"] = ref
    bl.blocks.insert(at, blk)
    bl._rebuild()
    bl._emit()


def _drop_vehicle(bl, team: dict, n: int):
    name = team["name"]
    del team["protos"][n]
    for o in list(bl.blocks):
        r = str(o.get("ref", "")) if o["k"] == "obj" else ""
        if r.startswith(name + "_vehicle_") and r[len(name) + 9:].isdigit():
            k = int(r[len(name) + 9:])
            if k == n:
                bl.blocks.remove(o)
            elif k > n:                  # vehicles after the removed one shift down
                scene.retarget(o, f"{name}_vehicle_{k - 1}")
    bl._rebuild()
    bl._emit()


def _flyaround(bl, b: dict, col: QVBoxLayout):
    ref = ObjRefEdit(bl.index, b.get("target", scene.PLAYER))
    ref.picked.connect(lambda v: (b.__setitem__("target", v), bl._emit()))
    col.addLayout(_line("Облёт камерой вокруг объекта", ref, stretch_last=True))
    row = ["радиус", _num_edit(b, "radius", bl, default="25"), "время, с", _num_edit(b, "time", bl, default="6")]
    if not b.get("posexpr"):
        row += ["подъём", _num_edit(b, "dy", bl, default="18.5")]
    col.addLayout(_line(*row))
    col.addLayout(_line(*_fades(b, bl)))


def _flylinked(bl, b: dict, col: QVBoxLayout):
    path = NameBox(bl.index, "campath", b.get("path", ""))
    path.committed.connect(lambda v: (b.__setitem__("path", v), bl._emit()))
    col.addLayout(_line("Пролёт камеры по пути", path, stretch_last=True))
    base = ObjRefEdit(bl.index, b.get("base", scene.PLAYER))
    base.picked.connect(lambda v: (b.__setitem__("base", v), bl._emit()))
    col.addLayout(_line("путь привязан к объекту", base, stretch_last=True))
    look = ObjRefEdit(bl.index, b.get("look", "") or "", empty="не следит" if not b.get("look") else "игрок",
                      empty_value="")
    if b.get("look") == scene.PLAYER:
        look.setPlaceholderText("игрок")
        look.empty_value = scene.PLAYER
    look.picked.connect(lambda v: (b.__setitem__("look", v), bl._emit()))
    me = QPushButton("на игрока")
    me.setObjectName("flat")
    me.setCursor(Qt.PointingHandCursor)
    me.clicked.connect(lambda: (b.__setitem__("look", scene.PLAYER), bl._rebuild(), bl._emit()))
    col.addLayout(_line("камера смотрит на", look, me))
    col.addLayout(_line("время, с", _num_edit(b, "time", bl, default="8"), *_fades(b, bl)))


def _message(bl, b: dict, col: QVBoxLayout):
    """Cutscene message together with its text and portrait from the map's strings.xml."""
    m = bl.index.current_map
    keys = ("who", "text", "time", "model", "skin", "cfg", "slot")
    mid = NameBox(bl.index, "strnum", str(b.get("id", "")))
    mid.setFixedWidth(70)
    col.addLayout(_line("Показать реплику ролика", mid))
    delay = _num_edit(b, "delay", bl, default="0.25")
    msg = m.message(str(b.get("id", ""))) or {}
    who = QLineEdit(msg.get("who") or "")
    who.setPlaceholderText("Кто говорит")
    text = QPlainTextEdit(msg.get("text") or "")
    text.setPlaceholderText("Текст реплики")
    text.setFixedHeight(58)
    text.setTabChangesFocus(True)
    time = QLineEdit(msg.get("time") or "")
    time.setFixedWidth(44)
    model = NameBox(bl.index, "portrait", msg.get("model") or "")
    model.setToolTip("modelName: mask_hero, mask_lisa, r1_man…")
    small = {}
    for key, tip in (("skin", "modelSkin"), ("cfg", "modelCfg"), ("slot", "modelSlot")):
        e = QLineEdit(msg.get(key) or "")
        e.setFixedWidth(44)
        e.setToolTip(tip)
        small[key] = e
    col.addWidget(who)
    col.addWidget(text)
    col.addLayout(_line("через, с", delay, "на экране, с", time))
    col.addLayout(_line("портрет", model, stretch_last=True))
    col.addLayout(_line("скин", small["skin"], "cfg", small["cfg"], "слот", small["slot"]))

    def save():
        mid_now = str(b.get("id", "")).strip()
        if not mid_now:
            return
        cur = m.message(mid_now) or {}
        new = {"who": who.text().strip(), "text": text.toPlainText().replace("\n", " ").strip(),
               "time": time.text().strip(), "model": model.text().strip(),
               "skin": small["skin"].text().strip(), "cfg": small["cfg"].text().strip(),
               "slot": small["slot"].text().strip()}
        if new == {k: (cur.get(k) or "") for k in keys}:
            return
        if not cur and not any(new.values()):       # nothing typed yet: do not create an empty entry
            return
        m.set_message(mid_now, new["who"], new["text"], new["time"], new["model"], new["skin"], new["cfg"], new["slot"])
    for w in (who, time, *small.values()):
        w.editingFinished.connect(save)
    model.committed.connect(lambda _v: save())
    old_focus_out = text.focusOutEvent

    def focus_out(e):
        old_focus_out(e)
        save()
    text.focusOutEvent = focus_out

    def set_id(v):
        b["id"] = v or "0"
        bl._rebuild()
        bl._emit()
    mid.committed.connect(set_id)

"""Shared widgets: framed panel, name field with completion, block list."""
from __future__ import annotations

from PySide6.QtCore import QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QComboBox, QCompleter, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

import copy
import html
import time

from . import lua


def clear_layout(lay):
    """Remove everything from a layout, nested layouts included."""
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            it.widget().hide()          # deleteLater runs later: without hide the old row flashes over the new one
            it.widget().deleteLater()
        elif it.layout():
            clear_layout(it.layout())


def add_row(lay, *widgets):
    """A 'buttons left, stretch right' row without a container widget."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(3)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    lay.addLayout(row)
    return row


class Panel(QFrame):
    def __init__(self, parent=None, margins=(4, 4, 4, 4), spacing=5):
        super().__init__(parent)
        self.setObjectName("panel")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(spacing)


def head(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("head")
    return l


def plus_button(tip: str = "") -> QToolButton:
    b = QToolButton()
    b.setObjectName("plus")
    b.setFixedSize(20, 20)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    return b


def minus_button(tip: str = "") -> QToolButton:
    b = QToolButton()
    b.setObjectName("minus")
    b.setFixedSize(18, 18)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    return b


def ask_text(parent, title: str, label: str, value: str = "") -> str | None:
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(value)
    dlg.setOkButtonText("Готово")
    dlg.setCancelButtonText("Отмена")
    dlg.resize(360, 100)
    if dlg.exec():
        return dlg.textValue().strip()
    return None


def ask_choice(parent, title: str, label: str, items: list[str], current: str = "") -> str | None:
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setComboBoxItems(items)
    if current in items:
        dlg.setTextValue(current)
    dlg.setOkButtonText("Готово")
    dlg.setCancelButtonText("Отмена")
    dlg.resize(420, 100)
    return dlg.textValue() if dlg.exec() else None


def confirm(parent, text: str, yes: str = "Удалить") -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("VersationEditor")
    box.setText(text)
    y = box.addButton(yes, QMessageBox.AcceptRole)
    box.addButton("Отмена", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is y


def warn(parent, text: str):
    box = QMessageBox(parent)
    box.setWindowTitle("VersationEditor")
    box.setText(text)
    box.addButton("Понятно", QMessageBox.AcceptRole)
    box.exec()


class NameBox(QLineEdit):
    """Name field with completion over quests/triggers/objects."""

    committed = Signal(str)

    def __init__(self, index, type_: str, value: str = "", width: int = 150, titles=None):
        super().__init__(value)
        self.index, self.type = index, type_
        self.titles = titles          # fn name -> caption (quest brief)
        self._last = value
        self.setMinimumWidth(60)
        self._w = width
        self._comp = QCompleter(self)
        self._comp.setCaseSensitivity(Qt.CaseInsensitive)
        self._comp.setFilterMode(Qt.MatchContains)
        self._comp.setMaxVisibleItems(14)
        self._comp.popup().setObjectName("completer")
        self._model = QStringListModel(self)
        self._comp.setModel(self._model)
        self.setCompleter(self._comp)
        self._comp.activated.connect(lambda _t: QTimer.singleShot(0, self._commit))
        self.editingFinished.connect(self._commit)
        self._tip()

    def sizeHint(self):
        s = super().sizeHint()
        w = QFontMetrics(self.font()).horizontalAdvance(self.text() or "Wwwwwww") + 22
        return QSize(max(70, min(w, 320)), s.height())

    def minimumSizeHint(self):
        return QSize(60, super().minimumSizeHint().height())

    def focusInEvent(self, e):
        self._model.setStringList(self.index.choices(self.type) if self.index else [])
        super().focusInEvent(e)
        if not self.text():
            QTimer.singleShot(0, lambda: (self._comp.setCompletionPrefix(""), self._comp.complete()))

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        if not self.text():
            self._comp.setCompletionPrefix("")
            self._comp.complete()

    def _tip(self):
        t = self.titles(self.text()) if self.titles and self.text() else ""
        self.setToolTip(t if t and t != self.text() else "")

    def _commit(self):
        v = self.text().strip()
        if v != self._last:
            self._last = v
            self._tip()
            self.updateGeometry()
            self.committed.emit(v)

    def set_value(self, v: str):
        self._last = v
        self.setText(v)
        self._tip()


class Choice(QComboBox):
    """Value/caption combo box that ignores the mouse wheel."""

    picked = Signal(str)

    def __init__(self, choices: list[tuple[str, str]], value: str = "", repick: bool = False):
        super().__init__()
        self._closed = 0.0          # when the popup was last hidden
        self._repick = repick       # picking the current item again is an action too (e.g. "choose a file…")
        self.setFocusPolicy(Qt.StrongFocus)
        for v, t in choices:
            self.addItem(t, v)
        self.set_value(value)
        self.activated.connect(self._activated)

    def _activated(self, _i):
        """Report a pick after the popup has closed, and only when the value changed.

        Handlers usually rebuild the form this box sits in. Doing that from inside the click that
        is still closing the popup (a double click picks the current item again) left the old popup
        and the new box fighting for the screen.
        """
        v = self.currentData()
        if v == self._value and not self._repick:
            return
        self._value = v
        self.updateGeometry()
        QTimer.singleShot(0, self, lambda: self.picked.emit(v))

    def sizeHint(self):
        # sized by the current value, not the longest item
        s = super().sizeHint()
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.currentText()) + 34, s.height())

    def minimumSizeHint(self):
        return QSize(44, super().minimumSizeHint().height())

    def showPopup(self):
        """The field is narrow, the popup fits the longest item.

        The popup window itself is widened: forcing a minimum width on the list inside a narrower
        window makes Qt fight over the layout, which shows as flicker. A second request that comes
        while the list is open or has just closed (the second click of a double click) is dropped.
        """
        if self.view().isVisible() or time.monotonic() - self._closed < 0.3:
            return
        fm = QFontMetrics(self.view().font())
        widest = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0)
        super().showPopup()
        box = self.view().window()
        want = max(widest + 56, self.width(), 120)
        if box is not self.window() and box.width() < want:
            geo = box.geometry()
            geo.setWidth(want)
            screen = self.screen().availableGeometry() if self.screen() else None
            if screen is not None and geo.right() > screen.right():
                geo.moveRight(screen.right())
            box.setGeometry(geo)

    def hidePopup(self):
        self._closed = time.monotonic()
        super().hidePopup()

    def mouseDoubleClickEvent(self, e):
        e.accept()          # the first click already opened the list

    def wheelEvent(self, e):
        e.ignore()

    def set_value(self, value: str):
        i = self.findData(value)
        if i < 0 and value:
            self.addItem(value, value)
            i = self.count() - 1
        self.setCurrentIndex(max(i, 0))
        self._value = self.currentData()

    def value(self) -> str:
        return self.currentData() or ""


class CodeEdit(QPlainTextEdit):
    """Raw Lua editor; its height grows with the text."""

    edited = Signal()

    def __init__(self, text: str = "", max_lines: int = 22):
        super().__init__()
        self.setObjectName("code")
        self.setPlainText(text)
        self.setTabStopDistance(QFontMetrics(self.font()).horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._max = max_lines
        self._saved = text
        self.textChanged.connect(self._fit)
        self._fit()

    def _fit(self):
        n = min(max(self.blockCount(), 1), self._max)
        self.setFixedHeight(QFontMetrics(self.font()).lineSpacing() * n + 30)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.toPlainText() != self._saved:
            self._saved = self.toPlainText()
            self.edited.emit()


class FlowRow(QWidget):
    """Row that wraps its widgets to the next line."""

    def __init__(self, spacing=4):
        super().__init__()
        self._items: list[QWidget] = []
        self._sp = spacing
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def add(self, w: QWidget):
        w.setParent(self)
        self._items.append(w)
        w.show()

    def _layout(self, width: int, apply: bool) -> int:
        x = y = line = 0
        for w in self._items:
            h = w.sizeHint()
            ww = min(h.width(), max(width, 60))
            if x and x + ww > width:
                x, y, line = 0, y + line + self._sp, 0
            if apply:
                w.setGeometry(x, y, ww, h.height())
            x += ww + self._sp
            line = max(line, h.height())
        return y + line

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(w, False)

    def sizeHint(self):
        w = self.width() if self.width() > 80 else 240
        return QSize(w, self._layout(w, False))

    def minimumSizeHint(self):
        return QSize(80, self._layout(max(self.width(), 80), False))

    def resizeEvent(self, e):
        self._layout(self.width(), True)
        if self.height() != self._layout(self.width(), False):
            self.updateGeometry()


class BlockList(QWidget):
    """List of blocks; the list of dicts is the source of truth.

    mode: 'cond' conditions (and/or/not), 'act' actions, 'ops' object operations.
    hide: predicate for blocks kept in the list but not shown (cutscene links).
    """

    changed = Signal()
    request = Signal(str, str)       # request to the tab: ("die", team name)

    TAIL = ("end", "leave", "selfoff")
    SHOTS = ("fly", "flylinked", "flyaround")
    COMPOUND = [("Сцена", "Создание команды", "team"), ("Сцена", "Обращение к объекту", "obj"),
                ("Ролик", "Остановить машину игрока", "stop"), ("Ролик", "Облёт камерой вокруг объекта", "flyaround"),
                ("Ролик", "Пролёт камеры по пути, привязанному к объекту", "flylinked")]

    # cutscene blocks in the menu follow their usual order
    ROLIK_ORDER = ["neutral", "stop", "fly", "flylinked", "flyaround", "cinestart", "cinemsg", "msgbox", "restore",
                   "cambehind"]

    def __init__(self, index, mode: str, where: str = "dialog", quest_titles=None, hide=None):
        super().__init__()
        self.index, self.mode, self.where = index, mode, where
        self.quest_titles = quest_titles
        self.hidden = hide or (lambda b: False)   # not self.hide: that is a QWidget method
        if mode == "ops":
            from . import scene
            self.specs, self.by = scene.OP_SPECS, scene.OP_BY
        else:
            self.specs = lua.COND_SPECS if mode == "cond" else lua.ACT_SPECS
            self.by = lua.COND_BY if mode == "cond" else lua.ACT_BY
        self.blocks: list[dict] = []
        self._chips: list = []
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(3)

    def set_blocks(self, blocks: list[dict]):
        self.blocks = blocks
        self._rebuild()

    def _emit(self):
        for chip, b in self._chips:          # tooltip: the block's Lua with current values
            chip.setToolTip(self._tip(b))
        self.changed.emit()

    def _lua(self, b: dict) -> str:
        """What this block writes to the file."""
        q = '"' if self.where == "trigger" else "'"
        try:
            if self.mode == "cond":
                return lua.render_condition([dict(b, join="")], q)
            if self.mode == "ops":
                from . import scene
                return scene.render_op(b, "obj", q)
            return lua.render_block(self.by, b, q)
        except Exception:
            return ""

    def _tip(self, b: dict) -> str:
        code = "" if b["k"] == "lua" else self._lua(b).replace("\t", "    ")
        if not code.strip():
            return ""
        return '<pre style="font-family: Consolas; margin: 0">' + html.escape(code) + "</pre>"

    def _rebuild(self):
        clear_layout(self.lay)
        self._chips = []
        shown = 0
        for i, b in enumerate(self.blocks):
            if self.hidden(b):
                continue
            if self.mode == "cond" and shown:
                self._join(b)
            self.lay.addWidget(self._row(i, b))
            shown += 1
        add = plus_button()
        add.clicked.connect(lambda: self._add_menu(add))
        add_row(self.lay, add)

    def _join(self, b: dict):
        btn = QPushButton("или" if b.get("join") == "or" else "и")
        btn.setObjectName("flat")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(16)

        def flip():
            b["join"] = "and" if b.get("join") == "or" else "or"
            btn.setText("или" if b["join"] == "or" else "и")
            self._emit()
        btn.clicked.connect(flip)
        add_row(self.lay, btn).setContentsMargins(6, 0, 0, 0)

    def _row(self, i: int, b: dict) -> QWidget:
        chip = QFrame()
        chip.setObjectName("chip")
        outer = QHBoxLayout(chip)
        outer.setContentsMargins(3, 1, 1, 1)
        outer.setSpacing(3)
        chip.setContextMenuPolicy(Qt.CustomContextMenu)
        chip.customContextMenuRequested.connect(lambda p, i=i, c=chip: self._row_menu(i, c.mapToGlobal(p)))
        chip.setToolTip(self._tip(b))
        self._chips.append((chip, b))

        if b["k"] == "lua":
            ed = CodeEdit(b.get("code", ""))
            ed.setFrameShape(QFrame.NoFrame)
            ed.setStyleSheet("border: none; border-image: none; background: transparent;")

            def upd(ed=ed, b=b):
                b["code"] = ed.toPlainText()
                self._emit()
            ed.edited.connect(upd)
            outer.addWidget(ed, 1)
        elif b["k"] == "group":
            col = QVBoxLayout()
            col.setContentsMargins(0, 2, 0, 2)
            if b.get("neg"):
                col.addWidget(self._neg_label(b))
            sub = BlockList(self.index, "cond", self.where, self.quest_titles)
            sub.set_blocks(b.setdefault("items", []))
            sub.changed.connect(self._emit)
            col.addWidget(sub)
            outer.addLayout(col, 1)
        else:
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            from . import scene_ui
            if self.mode == "cond" or not scene_ui.build(self, i, b, col):
                self._spec_row(b, col)
            outer.addLayout(col, 1)
        rm = minus_button()
        rm.clicked.connect(lambda _=False, i=i: self._remove(i))
        outer.addWidget(rm, 0, Qt.AlignTop)
        return chip

    def _spec_row(self, b: dict, col: QVBoxLayout):
        spec = self.by[b["k"]]
        line = QHBoxLayout()
        line.setSpacing(4)
        if b.get("neg"):
            line.addWidget(self._neg_label(b))
        line.addWidget(QLabel(spec.title))
        col.addLayout(line)
        fields = [f for f in spec.fields if f.type != "hidden"]
        small = ("enum", "op", "int", "num", "check")
        is_name = lambda f: f.type not in small
        if any(f.label or f.type in ("vec", "quat", "check") for f in fields):
            # labelled fields: first name on the title line, long ones on their own line, small ones grouped
            if fields and is_name(fields[0]) and not fields[0].label and fields[0].type not in ("vec", "quat"):
                line.addWidget(self._editor(b, fields[0]), 1)
                fields = fields[1:]
            else:
                line.addStretch(1)
            row = None
            for f in fields:
                wide = f.type not in small
                if wide or row is None or row.count() >= 5:
                    if row is not None:
                        row.addStretch(1)
                    row = QHBoxLayout()
                    row.setSpacing(4)
                    col.addLayout(row)
                if f.label and f.type != "check":
                    row.addWidget(QLabel(f.label))
                row.addWidget(self._editor(b, f), 1 if wide else 0)
                if wide:
                    row = None
            if row is not None:
                row.addStretch(1)
            return
        # name field on the title line, small fields on the second
        first_name = next((i for i, f in enumerate(fields) if is_name(f)), None)
        if first_name is None:                      # 'money >= 25'
            top, rest = fields, []
        elif first_name > 0:                        # 'quest [take]' / '[quest name]'
            top, rest = fields[:first_name], fields[first_name:]
        elif len(fields) <= 2:                      # 'quest [name] [taken]'
            top, rest = fields, []
        else:                                       # 'quest status [name]' / '[=] [taken]'
            top, rest = fields[:1], fields[1:]
        for f in top:
            line.addWidget(self._editor(b, f), 1 if is_name(f) else 0)
        if not any(is_name(f) for f in top):
            line.addStretch(1)
        if rest:
            second = QHBoxLayout()
            second.setSpacing(4)
            for f in rest:
                second.addWidget(self._editor(b, f), 1 if is_name(f) else 0)
            if not any(is_name(f) for f in rest):
                second.addStretch(1)
            col.addLayout(second)

    def _neg_label(self, b: dict) -> QWidget:
        n = QPushButton("не")
        n.setObjectName("flat")
        n.setStyleSheet("font-weight: bold; color: #7a2e12;")
        n.setCursor(Qt.PointingHandCursor)
        n.setToolTip("Убрать отрицание")

        def off():
            b["neg"] = False
            self._rebuild()
            self._emit()
        n.clicked.connect(off)
        return n

    def _editor(self, b: dict, f: lua.Field) -> QWidget:
        from . import scene_ui
        w = scene_ui.field_editor(self, b, f)
        if w is not None:
            return w
        v = str(b.get(f.name, ""))

        def setv(val, name=f.name):
            b[name] = val
            self._emit()
        if f.type == "enum":
            w = Choice(f.choices, v)
            w.picked.connect(setv)
        elif f.type == "op":
            w = Choice([(o, lua.OP_TITLES[o]) for o in lua.OPS], v)
            w.picked.connect(setv)
        elif f.type in ("int", "num"):
            w = QLineEdit(v)
            w.setFixedWidth(62)
            w.editingFinished.connect(lambda w=w: setv(w.text().strip().replace(",", ".") or "0"))
        elif f.type == "text":
            w = QLineEdit(v)
            w.setMinimumWidth(90)
            w.editingFinished.connect(lambda w=w: setv(w.text()))
        else:
            w = NameBox(self.index, f.type, v, titles=self.quest_titles if f.type == "quest" else None)
            w.committed.connect(setv)
        return w

    def _remove(self, i: int):
        del self.blocks[i]
        if self.blocks and self.mode == "cond":
            self.blocks[0]["join"] = ""
        self._rebuild()
        self._emit()

    def _neighbour(self, i: int, step: int) -> int | None:
        j = i + step
        while 0 <= j < len(self.blocks):
            if not self.hidden(self.blocks[j]):
                return j
            j += step
        return None

    clip: dict[str, list[dict]] = {}       # copied blocks by list kind, shared by every list of the window

    def _clip_key(self) -> str:
        return "cond" if self.mode == "cond" else ("ops" if self.mode == "ops" else "act:" + self.where)

    def _copy(self, i: int, cut: bool = False):
        BlockList.clip[self._clip_key()] = [copy.deepcopy(self.blocks[i])]
        if cut:
            self._remove(i)

    def _paste(self, at: int | None = None):
        """Insert the copied blocks after row `at` (at the end when None)."""
        items = copy.deepcopy(BlockList.clip.get(self._clip_key(), []))
        if not items:
            return
        at = len(self.blocks) if at is None else at + 1
        self.blocks[at:at] = items
        if self.mode == "cond":
            for n, b in enumerate(self.blocks):
                b["join"] = "" if n == 0 else (b.get("join") or "and")
        self._rebuild()
        self._emit()

    def _row_menu(self, i: int, pos):
        m = QMenu(self)
        b = self.blocks[i]
        if self.mode == "cond" and b["k"] != "lua":
            a = m.addAction("Отрицание")
            a.setCheckable(True)
            a.setChecked(bool(b.get("neg")))
            a.triggered.connect(lambda on: self._set(b, "neg", on))
        up, down = self._neighbour(i, -1), self._neighbour(i, 1)
        if up is not None:
            m.addAction("Выше", lambda: self._swap(i, up))
        if down is not None:
            m.addAction("Ниже", lambda: self._swap(i, down))
        if b["k"] not in ("lua", "group") and self.mode != "ops":
            m.addAction("Превратить в Lua", lambda: self._to_lua(i))
        m.addSeparator()
        m.addAction("Копировать", lambda: self._copy(i))
        m.addAction("Вырезать", lambda: self._copy(i, cut=True))
        if BlockList.clip.get(self._clip_key()):
            m.addAction("Вставить после", lambda: self._paste(i))
        m.addAction("Удалить", lambda: self._remove(i))
        m.exec(pos)

    def _set(self, b, k, v):
        b[k] = v
        self._rebuild()
        self._emit()

    def _swap(self, i, j):
        joins = [b.get("join") or "and" for b in self.blocks[1:]]   # joins live between rows
        self.blocks[i], self.blocks[j] = self.blocks[j], self.blocks[i]
        if self.mode == "cond":
            self.blocks[0]["join"] = ""
            for b, jn in zip(self.blocks[1:], joins):
                b["join"] = jn
        self._rebuild()
        self._emit()

    def _to_lua(self, i):
        b = self.blocks[i]
        code = lua.render_condition([dict(b, join="")]) if self.mode == "cond" else \
            lua.render_block(self.by, b, '"' if self.where == "trigger" else "'")
        self.blocks[i] = {"k": "lua", "code": code, "join": b.get("join", ""), "neg": False}
        self._rebuild()
        self._emit()

    def _add_menu(self, anchor: QWidget):
        self._build_menu().exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        groups: dict[str, list] = {}
        seen = set()
        for s in self.specs:
            if (self.mode == "ops" and s.where != "none" and s.title not in seen) or s.where in ("all", self.where):
                seen.add(s.title)
                groups.setdefault(s.group, []).append((s.title.rstrip(" ="), lambda s=s: lua.new_block(s), s.kind))
        if self.mode == "act" and self.where == "trigger":
            from . import scene
            for g, title, kind in self.COMPOUND:
                groups.setdefault(g, []).append((title, lambda kind=kind: scene.new_block(kind), kind))
        if "Ролик" in groups:
            rank = {k: n for n, k in enumerate(self.ROLIK_ORDER)}
            groups["Ролик"].sort(key=lambda it: rank.get(it[2], len(rank)))
        order = ["Сцена", "Ролик"]
        for g in sorted(groups, key=lambda g: (order.index(g) if g in order else 99)):
            sub = m.addMenu(g)
            sub.setToolTipsVisible(True)
            for title, make, _kind in groups[g]:
                a = sub.addAction(title, lambda make=make: self._add(make()))
                a.setToolTip(self._tip(make()))      # the item's Lua before it is added
        m.addSeparator()
        from . import templates
        tpl = templates.for_list(self.mode, self.where)
        if tpl:
            sub = m.addMenu("Шаблоны")
            sub.setToolTipsVisible(True)
            for item in tpl:
                if item is None:
                    sub.addSeparator()
                    continue
                title, make = item
                a = sub.addAction(title, lambda make=make: self._add_many(make(self.index)))
                a.setToolTip(self._tip_many(make(self.index)))
            m.addSeparator()
        if self.mode == "cond":
            m.addAction("Группа в скобках", lambda: self._add({"k": "group", "items": []}))
        m.addAction("Lua", lambda: self._add({"k": "lua", "code": ""}))
        if BlockList.clip.get(self._clip_key()):
            m.addSeparator()
            m.addAction("Вставить скопированное", lambda: self._paste())
        return m

    def _tip_many(self, blocks: list[dict]) -> str:
        code = "\n".join(self._lua(b) for b in blocks).replace("\t", "    ")
        return '<pre style="font-family: Consolas; margin: 0">' + html.escape(code) + "</pre>"

    def _add_many(self, blocks: list[dict]):
        """Insert a template: each block goes where it would go on its own."""
        for b in blocks:
            self._place(b, fresh=False)
        self._rebuild()
        self._emit()
        if any(b["k"] == "cinestart" for b in blocks):
            self.request.emit("ensure_end", "")

    def _add(self, b: dict):
        self._place(b)
        self._rebuild()
        self._emit()
        if b["k"] == "cinestart":     # a cutscene needs a trigger on end and skip
            self.request.emit("ensure_end", "")

    def _place(self, b: dict, fresh: bool = True):
        """Put a block at its place; 'fresh' also picks a new name/number for it."""
        if self.mode == "cond":
            b["join"] = "and" if self.blocks else ""
            b["neg"] = False
            self.blocks.append(b)
        elif self.mode == "ops":
            self.blocks.append(b)
        else:
            cur = self.index.current_map if self.index else None
            if fresh and b["k"] == "cinemsg" and cur is not None:
                b["id"] = cur.new_message_id()
            if fresh and b["k"] == "team" and cur is not None:
                b["name"] = cur.unique_trigger("NewTeam")
            # end-of-talk, self-deactivate and hidden links stay last
            tail = len(self.blocks)
            while tail and (self.blocks[tail - 1]["k"] in self.TAIL or self.hidden(self.blocks[tail - 1])) \
                    and b["k"] not in self.TAIL:
                tail -= 1
            # shots are registered before StartCinematic
            start = next((n for n, x in enumerate(self.blocks) if x["k"] == "cinestart"), None)
            if b["k"] in self.SHOTS and start is not None:
                tail = start
            if fresh and b["k"] in self.SHOTS:
                # a new shot continues the chain: fade-in stays at the start of the first and the end of the last
                # (two fade-ins in a row break in game)
                prev = next((x for x in reversed(self.blocks[:tail]) if x["k"] in self.SHOTS), None)
                if prev is not None:
                    prev["fout"], b["fin"], b["fout"] = "0", "0", "1"
            self.blocks.insert(tail, b)

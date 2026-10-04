"""Quests tab: tree with nested subquests and one form over quests.xml + journal."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                               QSplitter, QVBoxLayout, QWidget)

from . import lua
from .game import GameError
from .model import ROOT
from .tab_dialogs import PATH, REPLY, FolderTree
from .widgets import (BlockList, Choice, NameBox, Panel, add_row, ask_choice, ask_text, clear_layout, confirm, head, minus_button,
                      plus_button, warn)


class NameList(QWidget):
    """Editable list of names (quests, maps)."""

    changed = Signal()

    def __init__(self, index, type_: str, titles=None):
        super().__init__()
        self.index, self.type, self.titles = index, type_, titles
        self.values: list[str] = []
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(3)

    def set_values(self, values: list[str]):
        self.values = list(values)
        self._rebuild()

    def _rebuild(self, focus_last: bool = False):
        clear_layout(self.lay)
        last = None
        for i, v in enumerate(self.values):
            l = QHBoxLayout()
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(3)
            box = NameBox(self.index, self.type, v, titles=self.titles)
            box.committed.connect(lambda val, i=i: self._set(i, val))
            l.addWidget(box, 1)
            rm = minus_button()
            rm.clicked.connect(lambda _=False, i=i: self._del(i))
            l.addWidget(rm)
            self.lay.addLayout(l)
            last = box
        add = plus_button()
        add.clicked.connect(self._add)
        add_row(self.lay, add)
        if focus_last and last:
            last.setFocus()

    def _set(self, i, v):
        self.values[i] = v
        self.changed.emit()

    def _del(self, i):
        del self.values[i]
        self._rebuild()
        self.changed.emit()

    def _add(self):
        self.values.append("")
        self._rebuild(focus_last=True)

    def clean(self) -> list[str]:
        return [v for v in dict.fromkeys(self.values) if v]


class MarkerList(QWidget):
    """Quest markers: map + object on it."""

    changed = Signal()

    def __init__(self, index):
        super().__init__()
        self.index = index
        self.rows: list[list[str]] = []
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(3)

    def set_rows(self, rows):
        self.rows = [list(r) for r in rows]
        self._rebuild()

    def _rebuild(self):
        clear_layout(self.lay)
        maps = [(m, m) for m in self.index.game.maps()]
        for i, (mp, obj) in enumerate(self.rows):
            l = QHBoxLayout()
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(3)
            c = Choice(maps, mp)
            box = NameBox(self.index, "obj:" + mp, obj)
            c.picked.connect(lambda v, i=i, box=box: self._map(i, v, box))
            box.committed.connect(lambda v, i=i: self._obj(i, v))
            l.addWidget(c)
            l.addWidget(box, 1)
            rm = minus_button()
            rm.clicked.connect(lambda _=False, i=i: self._del(i))
            l.addWidget(rm)
            self.lay.addLayout(l)
        add = plus_button()
        add.clicked.connect(self._add)
        add_row(self.lay, add)

    def _map(self, i, v, box):
        self.rows[i][0] = v
        box.type = "obj:" + v
        self.changed.emit()

    def _obj(self, i, v):
        self.rows[i][1] = v
        self.changed.emit()

    def _del(self, i):
        del self.rows[i]
        self._rebuild()
        self.changed.emit()

    def _add(self):
        maps = self.index.game.maps()
        self.rows.append([self.rows[-1][0] if self.rows else (maps[0] if maps else ""), ""])
        self._rebuild()

    def clean(self):
        return [(m, o) for m, o in self.rows if m and o]


def toggle_row(options: list[tuple[str, str]]):
    """Row of exclusive toggle buttons."""
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(3)
    btns = {}
    for v, t in options:
        b = QPushButton(t)
        b.setCheckable(True)
        l.addWidget(b)
        btns[v] = b
    l.addStretch(1)
    w.btns = btns

    def set_value(v):
        for k, b in btns.items():
            b.setChecked(k == v)
    w.set_value = set_value
    return w


class QuestsTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.q = app.quests
        self.cur = ""
        self._loading = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        left = Panel()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск")
        self.search.setClearButtonEnabled(True)
        self._stimer = QTimer(self, singleShot=True, interval=250)
        self._stimer.timeout.connect(lambda: self.tree.set_filter(self.search.text()))
        self.search.textChanged.connect(lambda _t: self._stimer.start())
        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(self.search, 1)
        self.add_btn = plus_button("Добавить папку или квест")
        self.add_btn.clicked.connect(self._add_menu)
        top.addWidget(self.add_btn)
        left.lay.addLayout(top)
        self.tree = FolderTree(self.q, self.q.title, self._leaves, self._icon)
        self.tree.picked.connect(self._pick)
        self.tree.customContextMenuRequested.connect(self._menu)
        left.lay.addWidget(self.tree, 1)
        split.addWidget(left)

        idx = app.index
        # middle: what the quest is and when it is available
        mid = Panel()
        sa1, f1 = self._scroll()
        self.name = QLineEdit()
        self.name.setToolTip("Имя квеста")
        self.name.editingFinished.connect(self._rename)
        f1.addWidget(self.name)
        self.brief = QLineEdit()
        self.brief.setPlaceholderText("Строка в списке заданий")
        self.brief.editingFinished.connect(lambda: self._info("briefDiz", self.brief.text()))
        f1.addWidget(self.brief)
        self.full = QPlainTextEdit()
        self.full.setPlaceholderText("Описание в журнале")
        self.full.setFixedHeight(84)
        self.full.setTabChangesFocus(True)
        self.full.textChanged.connect(self._full)
        f1.addWidget(self.full)
        self.auto = QCheckBox("Берётся сам, когда доступен")
        self.auto.clicked.connect(lambda on: self._set("Automatic", "1" if on else "0"))
        f1.addWidget(self.auto)
        self.main = QCheckBox("Сюжетный")
        self.main.setToolTip("Жёлтый маркер на карте вместо фиолетового")
        self.main.clicked.connect(lambda on: self._info("isMainQuest", "1" if on else "0"))
        f1.addWidget(self.main)
        f1.addWidget(head("Доступен, когда"))
        self.c_all = toggle_row([("all", "все"), ("any", "любой")])
        self.c_state = toggle_row([("complete", "выполнены"), ("taken", "взяты"), ("failed", "провалены")])
        for v, b in self.c_all.btns.items():
            b.clicked.connect(lambda _=False, v=v: self._cond(v, None))
        for v, b in self.c_state.btns.items():
            b.clicked.connect(lambda _=False, v=v: self._cond(None, v))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.c_all)
        row.addWidget(self.c_state)
        row.addStretch(1)
        f1.addLayout(row)
        self.prec = NameList(idx, "quest", self.q.title)
        self.prec.changed.connect(lambda: self._set("PrecedingQuests", " ".join(self.prec.clean())))
        f1.addWidget(self.prec)
        f1.addWidget(head("Маркер на карте"))
        self.markers = MarkerList(idx)
        self.markers.changed.connect(lambda: self.q.set_markers(self.cur, self.markers.clean()) if self.cur else None)
        f1.addWidget(self.markers)
        f1.addWidget(head("Можно уехать на карты"))
        self.levels = NameList(idx, "map")
        self.levels.setToolTip("Пусто — на любые. Переезд на карту вне списка проваливает квест")
        self.levels.changed.connect(lambda: self._set("Levels", " ".join(self.levels.clean())))
        f1.addWidget(self.levels)
        f1.addWidget(head("Несовместим с"))
        self.mutex = NameList(idx, "quest", self.q.title)
        self.mutex.changed.connect(lambda: self.q.set_mutex(self.cur, self.mutex.clean()) if self.cur else None)
        f1.addWidget(self.mutex)
        f1.addStretch(1)
        mid.lay.addWidget(sa1)
        split.addWidget(mid)

        # right: subquests and hooks
        right = Panel()
        sa2, f2 = self._scroll()
        self.sub_head = head("Подквесты: выполнить")
        f2.addWidget(self.sub_head)
        self.sub = toggle_row([("and", "все"), ("or", "любой"), ("xor", "один из")])
        for v, b in self.sub.btns.items():
            b.clicked.connect(lambda _=False, v=v: (self._set("SubQuestsCondition", v), self.sub.set_value(v)))
        f2.addWidget(self.sub)
        self.checkall = QCheckBox("Дождаться исхода всех")
        self.checkall.clicked.connect(lambda on: self._set("CheckAll", "1" if on else "0"))
        f2.addWidget(self.checkall)
        self.hooks = {}
        for attr, title in (("OnTake", "При взятии"), ("OnComplete", "При выполнении"), ("OnFail", "При провале"),
                            ("OnCanBeGiven", "Когда стал доступен")):
            f2.addWidget(head(title))
            bl = BlockList(idx, "act", "quest", self.q.title)
            bl.changed.connect(lambda attr=attr, bl=bl: self._set(attr, lua.render_actions(bl.blocks)))
            f2.addWidget(bl)
            self.hooks[attr] = bl
        f2.addStretch(1)
        right.lay.addWidget(sa2)
        split.addWidget(right)
        split.setSizes([300, 520, 500])
        self.forms = (sa1, sa2)
        self.tree.rebuild()
        self._show("")

    def _scroll(self):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        f = QVBoxLayout(w)
        f.setContentsMargins(0, 0, 4, 0)
        f.setSpacing(5)
        sa.setWidget(w)
        return sa, f

    def _icon(self, name: str) -> str:
        """Radar ring: orange for main quests, magenta for optional ones."""
        i = self.q.infos.get(name)
        return "quest_a" if i is not None and i.get("isMainQuest") == "1" else "quest_b"

    def _leaves(self, folder: str):
        def sub(name):
            return [(c, sub(c)) for c in self.q.children_of(name)]
        return [(e.get("Name"), sub(e.get("Name"))) for e in self.q.in_folder(folder)]

    def reload(self):
        self.tree.rebuild()
        self._show(self.cur if self.cur in self.q.items else "")

    def goto(self, name: str):
        if name in self.q.items:
            self.tree.rebuild(self.q.folder_of[name], name)
            self._show(name)

    # --- tree ---
    def _pick(self, folder: str, quest: str):
        self._show(quest)

    def _menu(self, pos):
        ix = self.tree.indexAt(pos)
        path = (ix.data(PATH) if ix.isValid() else ROOT) or ROOT
        quest = ix.data(REPLY) if ix.isValid() else ""
        m = QMenu(self)
        if quest:
            m.addAction("Новый подквест", lambda: self._new(path, quest))
            if self.q.parent_of(quest):
                m.addAction("Вынести из родителя", lambda: self._move(quest, None))
            else:
                m.addAction("Вложить в квест…", lambda: self._nest(quest))
                m.addAction("Перенести в папку…", lambda: self._to_folder(quest))
            m.addSeparator()
            m.addAction("Удалить", lambda: self._delete(quest))
        else:
            m.addAction("Новый квест", lambda: self._new(path, None))
            m.addAction("Новая папка", lambda: self._new_folder(path))
            if ix.isValid():
                m.addAction("Переименовать папку", lambda: self._rename_folder(path))
                m.addAction("Удалить папку", lambda: self._delete_folder(path))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_menu(self):
        ix = self.tree.currentIndex()
        path = (ix.data(PATH) if ix.isValid() else ROOT) or ROOT
        quest = (ix.data(REPLY) if ix.isValid() else "") or ""
        m = QMenu(self)
        m.addAction("Папка", lambda: self._new_folder(ROOT))
        if path != ROOT:
            m.addAction(f"Папка внутри «{path.rsplit('/', 1)[-1].strip()}»", lambda: self._new_folder(path))
        m.addSeparator()
        m.addAction("Квест", lambda: self._new(path, None))
        if quest:
            m.addAction(f"Подквест в «{self.q.title(quest)}»", lambda: self._new(path, quest))
        m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def _new(self, folder: str, parent: str | None):
        seg = folder.split("/")[1] if "/" in folder else ""
        base = (parent + "_Sub") if parent else ((seg + "_") if seg.isascii() and seg else "") + "New_Quest"
        name = ask_text(self, "Новый квест", "Имя", self.q.unique_name(base))
        if not name:
            return
        try:
            self.q.add(name, folder, parent)
        except GameError as e:
            warn(self, str(e))
            return
        self.goto(name)
        self.brief.setFocus()

    def _delete(self, name: str):
        kids = len(list(self.q.items[name].iter("quest")))
        extra = f" и {kids} подквест(ов)" if kids else ""
        if confirm(self, f"Удалить квест «{self.q.title(name)}»{extra}?"):
            folder = self.q.folder_of[name]
            self.q.delete(name)
            self.cur = ""
            self.tree.rebuild(folder)
            self._show("")

    def _move(self, name, parent):
        try:
            self.q.move(name, parent, self.q.folder_of[name])
        except GameError as e:
            warn(self, str(e))
        self.goto(name)

    def _to_folder(self, name):
        folders = [f for f in self.q.folders if f != ROOT]
        to = ask_choice(self, "Перенести квест", "Папка", folders, self.q.folder_of[name])
        if to:
            self.q.move_to_folder(name, to)
            self.goto(name)

    def _nest(self, name):
        parent = ask_text(self, "Вложить в квест", "Имя родительского квеста")
        if parent:
            if parent not in self.q.items:
                warn(self, f"Квеста «{parent}» нет.")
                return
            self._move(name, parent)

    def _new_folder(self, parent):
        name = ask_text(self, "Новая папка", "Название")
        if name:
            name = name.replace("/", " ").replace("--", "-").strip()
            self.q.add_folder(f"{parent}/{name}")
            self.tree.rebuild(f"{parent}/{name}")

    def _rename_folder(self, path):
        name = ask_text(self, "Папка", "Название", path.rsplit("/", 1)[-1])
        if name and "/" not in name:
            new = path.rsplit("/", 1)[0] + "/" + name
            self.q.rename_folder(path, new)
            self.tree.rebuild(new)

    def _delete_folder(self, path):
        if not self.q.folder_empty(path):
            warn(self, "В папке есть квесты — сначала удалите или перенесите их.")
            return
        self.q.delete_folder(path)
        self.tree.rebuild()

    # --- form ---
    def _show(self, name: str):
        el = self.q.items.get(name)
        self.cur = name if el is not None else ""
        for sa in self.forms:
            sa.widget().setEnabled(el is not None)
        self._loading = True
        info = self.q.infos.get(name)
        g = (lambda a: el.get(a)) if el is not None else (lambda a: "")
        gi = (lambda a: info.get(a)) if info is not None else (lambda a: "")
        self.name.setText(self.cur)
        self.brief.setText(gi("briefDiz"))
        self.full.setPlainText(gi("fullDiz"))
        self.auto.setChecked(g("Automatic") == "1")
        self.main.setChecked(gi("isMainQuest") == "1")
        cg = (g("ConditionToGive") or "all complete").split()
        self.c_all.set_value(cg[0])
        self.c_state.set_value(cg[1] if len(cg) > 1 else "complete")
        self.prec.set_values(g("PrecedingQuests").split())
        self.levels.set_values(g("Levels").split())
        self.markers.set_rows(self.q.markers(name) if self.cur else [])
        self.mutex.set_values(self.q.mutex_of(name) if self.cur else [])
        self.sub.set_value(g("SubQuestsCondition") or "and")
        self.checkall.setChecked(g("CheckAll") == "1")
        has_kids = bool(self.cur and self.q.children_of(name))
        for w in (self.sub_head, self.sub, self.checkall):
            w.setVisible(has_kids)
        for attr, bl in self.hooks.items():
            bl.set_blocks(lua.parse_actions(g(attr)))
        self._loading = False

    def _set(self, attr, value):
        if self.cur and not self._loading:
            self.q.set(self.cur, attr, value)

    def _info(self, attr, value):
        if self.cur and not self._loading:
            i = self.q.infos.get(self.cur)
            if (i.get(attr) if i is not None else "") != value:
                self.q.set_info(self.cur, attr, value)
                if attr in ("briefDiz", "isMainQuest"):      # caption or ring colour changes in the tree
                    self.tree.rebuild(self.q.folder_of[self.cur], self.cur)

    def _full(self):
        if self.cur and not self._loading:
            self._info("fullDiz", self.full.toPlainText().replace("\n", " "))

    def _cond(self, which, state):
        if not self.cur:
            return
        cg = (self.q.items[self.cur].get("ConditionToGive") or "all complete").split()
        cg = [which or cg[0], state or (cg[1] if len(cg) > 1 else "complete")]
        self.c_all.set_value(cg[0])
        self.c_state.set_value(cg[1])
        self._set("ConditionToGive", " ".join(cg))

    def _rename(self):
        new = self.name.text().strip()
        if not self.cur or new == self.cur:
            return
        old = self.cur
        try:
            self.q.rename(old, new)
        except GameError as e:
            self.name.setText(old)
            warn(self, str(e))
            return
        self.app.rename_everywhere(old, new)
        self.goto(new)

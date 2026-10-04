"""Dialogs tab: folders, graph, reply inspector."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                               QTreeView, QVBoxLayout, QWidget)

from . import lua, theme
from .game import GameError
from .graph import GraphView
from .model import ROOT
from .widgets import BlockList, Panel, ask_text, confirm, head, plus_button, warn

PATH = Qt.UserRole + 1
REPLY = Qt.UserRole + 2


class FolderTree(QTreeView):
    """Folder tree (dialogs or quests) with filtering and a folder context menu."""

    picked = Signal(str, str)     # folder, item (or "")

    def __init__(self, store, leaf_title=None, leaves=None, leaf_icon=None):
        super().__init__()
        self.store = store
        self.leaf_title = leaf_title        # name -> caption
        self.leaves = leaves                # folder -> [(name, [children])]; None for folders only
        self.leaf_icon = leaf_icon          # name -> theme icon name
        self.setIconSize(QSize(16, 16))
        self.m = QStandardItemModel(self)
        self.setModel(self.m)
        self.setHeaderHidden(True)
        self.setEditTriggers(QTreeView.NoEditTriggers)
        self.setIndentation(14)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self._filter = ""
        self.selectionModel().currentChanged.connect(self._cur)
        self._mute = False

    def _cur(self, cur, _prev):
        if self._mute or not cur.isValid():
            return
        self.picked.emit(cur.data(PATH) or "", cur.data(REPLY) or "")

    def rebuild(self, select_path: str | None = None, select_leaf: str | None = None):
        expanded = set()

        def walk(parent):
            for r in range(parent.rowCount()):
                it = parent.child(r)
                if self.isExpanded(it.index()) and not it.data(REPLY):
                    expanded.add(it.data(PATH))
                walk(it)
        walk(self.m.invisibleRootItem())
        if select_path is None:
            ci = self.currentIndex()
            select_path, select_leaf = (ci.data(PATH), ci.data(REPLY)) if ci.isValid() else (None, None)
        self._mute = True
        self.m.clear()
        items: dict[str, QStandardItem] = {}
        target = None
        flt = self._filter.lower()
        for path in self.store.folders:
            if path == ROOT:
                continue
            parent = items.get(path.rsplit("/", 1)[0], self.m.invisibleRootItem())
            it = QStandardItem(theme.icon("folder"), path.rsplit("/", 1)[-1].strip())
            it.setData(path, PATH)
            parent.appendRow(it)
            items[path] = it
            if path == select_path and not select_leaf:
                target = it
        if self.leaves:
            for path in list(self.store.folders):
                parent = items.get(path, self.m.invisibleRootItem())
                target = self._add_leaves(parent, path, self.leaves(path), select_leaf, flt) or target
        if flt:
            self._prune(self.m.invisibleRootItem(), flt)
            self.expandAll()
        else:
            for p, it in items.items():
                if p in expanded or (select_path and (select_path + "/").startswith(p + "/")):
                    self.setExpanded(it.index(), True)
        self._mute = False
        if target is not None and target.model() is self.m:
            self.setCurrentIndex(target.index())
            self.scrollTo(target.index())

    def _add_leaves(self, parent, path, leaves, select_leaf, flt):
        target = None
        for name, kids in leaves:
            it = QStandardItem(self.leaf_title(name) if self.leaf_title else name)
            if self.leaf_icon:
                it.setIcon(theme.icon(self.leaf_icon(name)))
            it.setData(path, PATH)
            it.setData(name, REPLY)
            it.setToolTip(name)
            parent.appendRow(it)
            if name == select_leaf:
                target = it
            target = self._add_leaves(it, path, kids, select_leaf, flt) or target
            if kids and (not flt):
                pass
        return target

    def _prune(self, parent, flt) -> bool:
        keep_any = False
        for r in reversed(range(parent.rowCount())):
            it = parent.child(r)
            sub = self._prune(it, flt)
            own = flt in it.text().lower() or flt in (it.data(REPLY) or "").lower()
            if sub or own:
                keep_any = True
            else:
                parent.removeRow(r)
        return keep_any

    def set_filter(self, text: str):
        self._filter = text.strip()
        self.rebuild()

    def current_path(self) -> str:
        ci = self.currentIndex()
        return (ci.data(PATH) if ci.isValid() else "") or ""


class DialogsTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.d = app.dialogs
        self.cur = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        # left: search and folders
        left = Panel()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск")
        self.search.setClearButtonEnabled(True)
        self._stimer = QTimer(self, singleShot=True, interval=250)
        self._stimer.timeout.connect(self._search)
        self.search.textChanged.connect(lambda _t: self._stimer.start())
        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(self.search, 1)
        self.add_btn = plus_button("Добавить папку или диалог")
        self.add_btn.clicked.connect(self._add_menu)
        top.addWidget(self.add_btn)
        left.lay.addLayout(top)
        self.tree = FolderTree(self.d)
        self.tree.picked.connect(self._pick)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        left.lay.addWidget(self.tree, 1)
        split.addWidget(left)

        # centre: graph
        self.graph = GraphView(self.d, app.index)
        self.graph.selected.connect(self._select)
        self.graph.structure_changed.connect(self._structure)
        self.graph.open_folder.connect(self.goto)
        split.addWidget(self.graph)

        # right: reply
        right = Panel()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.form = QWidget()
        f = QVBoxLayout(self.form)
        f.setContentsMargins(0, 0, 4, 0)
        f.setSpacing(5)
        roles = QHBoxLayout()
        roles.setSpacing(4)
        self.b_npc = QPushButton("NPC")
        self.b_pl = QPushButton("Игрок")
        for b, r in ((self.b_npc, "NPC"), (self.b_pl, "PLAYER")):
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, r=r: self._set_role(r))
            roles.addWidget(b)
        roles.addStretch(1)
        self.b_play = QPushButton("Проиграть")
        self.b_play.setToolTip("Пройти диалог как в игре, начиная с этой реплики")
        self.b_play.clicked.connect(self._play)
        roles.addWidget(self.b_play)
        f.addLayout(roles)
        self.name = QLineEdit()
        self.name.setToolTip("Имя реплики")
        self.name.editingFinished.connect(self._rename)
        f.addWidget(self.name)
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(96)
        self.text.setTabChangesFocus(True)
        self.text.textChanged.connect(self._text)
        f.addWidget(self.text)
        f.addWidget(head("Условия"))
        self.cond = BlockList(app.index, "cond", "dialog", app.quests.title)
        self.cond.changed.connect(self._cond)
        f.addWidget(self.cond)
        f.addWidget(head("Результаты"))
        self.res = BlockList(app.index, "act", "dialog", app.quests.title)
        self.res.changed.connect(self._res)
        f.addWidget(self.res)
        f.addStretch(1)
        sa.setWidget(self.form)
        right.lay.addWidget(sa)
        split.addWidget(right)
        split.setSizes([230, 720, 380])
        split.setStretchFactor(1, 1)
        self.form.setEnabled(False)
        self._loading = False

        self.tree.rebuild()
        first = next((f for f in self.d.folders if self.d.in_folder(f)), "")
        if first:
            self.goto(first, "")

    # --- navigation ---
    def goto(self, folder: str, reply: str = ""):
        self.tree.rebuild(folder)
        self.graph.show_folder(folder, reply or None)

    def reload(self):
        self.tree.rebuild()
        self.graph.show_folder(self.graph.folder, self.cur or None, keep_view=True)

    def _pick(self, folder: str, _leaf: str):
        if folder and folder != self.graph.folder:
            self.graph.show_folder(folder)

    def _search(self):
        q = self.search.text().strip().lower()
        if len(q) < 2:
            return
        for name, el in self.d.items.items():
            if q in name.lower() or q in el.get("text").lower():
                self.goto(self.d.folder_of[name], name)
                self.search.setFocus()
                return

    def _tree_menu(self, pos):
        ix = self.tree.indexAt(pos)
        path = ix.data(PATH) if ix.isValid() else ROOT
        m = QMenu(self)
        m.addAction("Новая папка", lambda: self._new_folder(path))
        if ix.isValid():
            m.addAction("Новый диалог", lambda: self._new_dialog(path))
            m.addAction("Переименовать", lambda: self._rename_folder(path))
            m.addAction("Удалить", lambda: self._delete_folder(path))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _add_menu(self):
        cur = self.tree.current_path() or self.graph.folder
        m = QMenu(self)
        m.addAction("Папка", lambda: self._new_folder(ROOT))
        if cur and cur != ROOT:
            m.addAction(f"Папка внутри «{cur.rsplit('/', 1)[-1].strip()}»", lambda: self._new_folder(cur))
            m.addSeparator()
            m.addAction("Диалог", lambda: self._new_dialog(cur))
        m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def _new_folder(self, parent: str):
        name = ask_text(self, "Новая папка", "Название")
        if name:
            name = name.replace("/", " ").replace("--", "-").strip()   # '/' splits the path, '--' breaks an XML comment
            self.d.add_folder(f"{parent}/{name}")
            self.goto(f"{parent}/{name}")

    def _new_dialog(self, folder: str):
        """First NPC reply in a folder: the dialog starts with it."""
        base = folder.split("/")[1] if folder.count("/") else "Dlg"
        base = base if base.isascii() else "Dlg"
        name = ask_text(self, "Новый диалог", "Имя первой реплики", self.d.unique_name(f"{base}_Npc_hellodlg0"))
        if not name:
            return
        try:
            self.d.add(name, "NPC", folder)
        except GameError as e:
            warn(self, str(e))
            return
        self.goto(folder, name)
        self.text.setFocus()

    def _rename_folder(self, path: str):
        name = ask_text(self, "Папка", "Название", path.rsplit("/", 1)[-1])
        if name and "/" not in name:
            new = path.rsplit("/", 1)[0] + "/" + name
            self.d.rename_folder(path, new)
            self.goto(new)

    def _delete_folder(self, path: str):
        inside = [n for n, f in self.d.folder_of.items() if f == path or f.startswith(path + "/")]
        if inside and not confirm(self, f"В папке {len(inside)} реплик. Удалить вместе с ними?"):
            return
        for n in inside:
            self.d.delete(n)
        self.d.delete_folder(path)
        self.tree.rebuild()
        self.graph.show_folder(path.rsplit("/", 1)[0])

    # --- reply ---
    def _select(self, name: str):
        el = self.d.items.get(name)
        ok = el is not None and self.d.folder_of.get(name) == self.graph.folder
        self.cur = name if ok else ""
        self.form.setEnabled(ok)
        self._loading = True
        if ok:
            self.b_npc.setChecked(el.get("role") == "NPC")
            self.b_pl.setChecked(el.get("role") != "NPC")
            self.name.setText(name)
            if self.text.toPlainText() != el.get("text"):
                self.text.setPlainText(el.get("text"))
            self.cond.set_blocks(lua.parse_condition(el.get("scriptCondition")))
            self.res.set_blocks(lua.parse_actions(el.get("scriptResult")))
        else:
            self.name.clear()
            self.text.clear()
            self.cond.set_blocks([])
            self.res.set_blocks([])
            self.b_npc.setChecked(False)
            self.b_pl.setChecked(False)
        self._loading = False

    def _structure(self):
        self.tree.rebuild(self.graph.folder)

    def _set_role(self, role: str):
        if self.cur:
            self.d.set(self.cur, "role", role)
            self.b_npc.setChecked(role == "NPC")
            self.b_pl.setChecked(role != "NPC")
            self.graph.refresh_node(self.cur)

    def _rename(self):
        new = self.name.text().strip()
        if not self.cur or new == self.cur:
            return
        old = self.cur
        try:
            self.d.rename(old, new)
        except GameError as e:
            self.name.setText(old)
            warn(self, str(e))
            return
        users = self.app.index.hello_users().get(old, [])
        self.app.rename_hello(old, new, users)
        self.cur = new
        self.graph.show_folder(self.graph.folder, new, keep_view=True)

    def _text(self):
        if self._loading or not self.cur:
            return
        t = self.text.toPlainText().replace("\n", " ")
        if t != self.d.items[self.cur].get("text"):
            self.d.set(self.cur, "text", t)
            self.graph.refresh_node(self.cur)

    def _cond(self):
        if self.cur and not self._loading:
            self.d.set(self.cur, "scriptCondition", lua.render_condition(self.cond.blocks))
            self.graph.refresh_node(self.cur)

    def _res(self):
        if self.cur and not self._loading:
            self.d.set(self.cur, "scriptResult", lua.render_actions(self.res.blocks))
            self.graph.refresh_node(self.cur)

    def _play(self):
        if self.cur:
            from .preview import PreviewDialog
            PreviewDialog(self.app, self.cur, self).exec()

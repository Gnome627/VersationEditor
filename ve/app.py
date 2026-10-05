"""Main window: tab ribbon and saving."""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import theme
from .game import Game, GameError, find_root
from .model import Dialogs, Index, MapData, Quests, Towns, _replace_quoted, _replace_word
from .models import ModelLib


class App:
    """Everything that is open: game files and the models over them."""

    def __init__(self, game: Game):
        self.game = game
        self.dialogs = Dialogs(game)
        self.quests = Quests(game)
        self.towns = Towns(game)
        self.index = Index(game, self.dialogs, self.quests, self.towns)
        self.models = ModelLib(game)
        self.index.pick_look = self.pick_look
        self._maps: dict[str, MapData] = {}
        self.window: "MainWindow | None" = None

    def map(self, name: str) -> MapData:
        if name not in self._maps:
            self._maps[name] = MapData(self.game, name, self.towns)
        return self._maps[name]

    def belong_title(self, belong: str) -> str:
        return dict(self.index.belongs()).get(belong, "")

    def rename_hello(self, old: str, new: str, users: list[tuple[str, str]]):
        """A hello reply was renamed: fix helloReplyNames of the NPCs that use it."""
        for mp, npc in users:
            m = self.map(mp)
            el = m.objects.get(npc)
            if el is not None:
                m.set_attr(el, "helloReplyNames", _replace_word(el.get("helloReplyNames"), old, new), True)
        self.index.reset()

    def rename_everywhere(self, old: str, new: str):
        """A quest was renamed: fix replies and triggers on every map that mentions it."""
        self.dialogs.replace_ref(old, new)
        for mp in self.game.maps():
            base = f"data/maps/{mp}/"
            if not any(old in self.index._raw(base + f) for f in ("triggers.xml", "cinematriggers.xml")):
                continue
            m = self.map(mp)
            for t in m.triggers():
                m._replace_in_script(t, old, new)

    def reload_all(self):
        """After undo/redo the document trees are new: rebuild models and tabs."""
        self.dialogs.reindex()
        self.quests.reindex()
        self.towns.reindex()
        for m in self._maps.values():
            m.reindex()
        self.index.reset()
        if self.window:
            for t in self.window.tabs:
                t.reload()

    def pick_look(self, model: str, skin: str, cfg: str, title: str, done):
        """Open the model in the Models tab; the chosen skin and config come back through `done`."""
        w = self.window
        if not w or not model:
            return
        back = w.stack.currentIndex()

        def finish(s, c):
            w.show_tab(back)
            done(s, c)
        w.show_tab(3)
        w.tabs[3].pick(model, skin or "", cfg or "", title, finish)

    def open_dialog(self, reply: str):
        if self.window:
            self.window.show_tab(0)
            self.window.tabs[0].goto(self.dialogs.folder_of[reply], reply)


class MainWindow(QMainWindow):
    def __init__(self, app: App):
        super().__init__()
        self.app = app
        app.window = self
        self.setWindowTitle("VersationEditor")
        from . import appicon
        self.setWindowIcon(appicon.qicon())
        QApplication.instance().setWindowIcon(self.windowIcon())
        self.resize(1440, 880)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        from .tab_dialogs import DialogsTab
        from .tab_events import EventsTab
        from .tab_quests import QuestsTab
        from .tab_models import ModelsTab
        self.tabs = [DialogsTab(app), QuestsTab(app), EventsTab(app), ModelsTab(app)]

        ribbon = QHBoxLayout()
        ribbon.setSpacing(4)
        self.tab_btns = []
        for i, title in enumerate(("Диалоги", "Квесты", "Окружение", "Модели")):
            b = QPushButton(title)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, i=i: self.show_tab(i))
            ribbon.addWidget(b)
            self.tab_btns.append(b)
        ribbon.addStretch(1)
        self.map_box = self.tabs[2].map_box
        ribbon.addWidget(self.map_box)
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setObjectName("tab")
        self.save_btn.setToolTip("Ctrl+S")
        self.save_btn.clicked.connect(self.save)
        ribbon.addWidget(self.save_btn)
        lay.addLayout(ribbon)

        self.stack = QStackedWidget()
        for t in self.tabs:
            self.stack.addWidget(t)
        lay.addWidget(self.stack, 1)
        QShortcut(QKeySequence.Save, self, self.save)
        QShortcut(QKeySequence.Undo, self, lambda: self._history(app.game.undo))
        QShortcut(QKeySequence.Redo, self, lambda: self._history(app.game.redo))
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, lambda: self._history(app.game.redo))
        self._commit = QTimer(self, singleShot=True, interval=450)   # a burst of edits is one undo step
        self._commit.timeout.connect(app.game.commit)
        app.game.on_change(self._commit.start)
        for i in range(4):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda i=i: self.show_tab(i))
        app.game.on_change(self._dirty)
        self.show_tab(0)
        self._dirty()

    def show_tab(self, i: int):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.tab_btns):
            b.setChecked(i == j)
        self.map_box.setVisible(i == 2)
        self.app.index.current_map = self.tabs[2].map if i == 2 else None
        if i == 1:
            self.tabs[1].tree.rebuild()
        if i == 3:
            self.tabs[3].activate()

    def _history(self, step):
        if step():
            docs = self.app.game.last_step
            if docs and all(d.rel.lower().endswith(".gam") or d.rel.lower() == "data/models/animmodels.xml"
                            for d in docs):
                self.tabs[3].reload()       # a model edit: dialogs, quests and maps are untouched
            else:
                self.app.reload_all()
            self._dirty()

    def _dirty(self):
        n = len(self.app.game.dirty_docs())
        self.save_btn.setEnabled(n > 0)
        self.save_btn.setObjectName("accent" if n else "tab")
        self.save_btn.setStyleSheet("padding: 2px 18px; font-size: 10pt;")
        self.setWindowTitle("VersationEditor" + (" *" if n else ""))

    def save(self) -> bool:
        w = QApplication.focusWidget()
        if w is not None:
            w.clearFocus()          # commit the field being edited
        try:
            self.app.game.save_all()
        except (GameError, OSError, ValueError) as e:
            box = QMessageBox(self)
            box.setWindowTitle("VersationEditor")
            box.setText(f"Не сохранено.\n{e}")
            box.addButton("Понятно", QMessageBox.AcceptRole)
            box.exec()
            return False
        self.app.index.reset()
        self.tabs[3].reload()
        return True

    def closeEvent(self, e):
        if not self.app.game.dirty_docs():
            return e.accept()
        box = QMessageBox(self)
        box.setWindowTitle("VersationEditor")
        box.setText("Есть несохранённые правки.")
        s = box.addButton("Сохранить", QMessageBox.AcceptRole)
        d = box.addButton("Не сохранять", QMessageBox.DestructiveRole)
        box.addButton("Отмена", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is s:
            e.accept() if self.save() else e.ignore()
        elif box.clickedButton() is d:
            e.accept()
        else:
            e.ignore()


def make_app(argv=None) -> QApplication:
    qapp = QApplication.instance() or QApplication(argv or sys.argv[:1])
    qapp.setFont(QFont("Tahoma", 9))
    return qapp


def open_game(qapp, root=None) -> App | None:
    root = root or find_root()
    if root is None:
        box = QMessageBox()
        box.setWindowTitle("VersationEditor")
        box.setText("Рядом нет папки игры (data/maps). Положите VersationEditor.exe в папку Ex Machina "
                    "или укажите её.")
        pick = box.addButton("Указать папку", QMessageBox.AcceptRole)
        box.addButton("Закрыть", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not pick:
            return None
        from pathlib import Path
        d = QFileDialog.getExistingDirectory(None, "Папка игры")
        if not d or not (Path(d) / "data" / "maps").is_dir():
            return None
        root = Path(d)
    game = Game(root)
    theme.build(game)
    qapp.setStyleSheet(theme.qss())
    return App(game)


def run(argv=None) -> int:
    qapp = make_app(argv)
    try:
        app = open_game(qapp)
    except Exception as e:      # broken game XML: report it instead of crashing
        box = QMessageBox()
        box.setWindowTitle("VersationEditor")
        box.setText(f"Не удалось открыть файлы игры.\n{e}")
        box.exec()
        return 1
    if app is None:
        return 1
    win = MainWindow(app)
    win.show()
    return qapp.exec()

"""Environment tab: map, places (towns, locations, NPCs), triggers, cutscenes, paths."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QTreeView, QVBoxLayout, QWidget)

from . import lua, theme
from .game import GameError, fmt
from .mapview import MapView
from .model import EVENT_BY, EVENTS, rot_to_yaw, yaw_to_rot
from .cutscene_ui import CutsceneView, EventsEditor, PathEditor, block_marks, new_cutscene
from .tab_quests import NameList
from .widgets import (BlockList, Choice, CodeEdit, NameBox, Panel, ask_text, clear_layout, confirm, head, minus_button,
                      plus_button, warn)

EL = Qt.UserRole + 1
KIND = Qt.UserRole + 2

ARTICLE_COLS = [("Prototype", "Товар"), ("Amount", "Запас"), ("ExternalPriceCoefficient", "Цена ×"),
                ("Export", "Продаёт"), ("Import", "Покупает"), ("MinCount", "Мин"), ("MaxCount", "Макс"),
                ("RegenerationPeriod", "Пополнение, с"), ("PriceDynamic", "Динамика")]
PROTO_TITLES = {"ModelFile": "Модель", "GateModelFile": "Модель ворот", "MusicName": "Музыка",
                "Vehicles": "Машины защитников", "MaxDefenders": "Защитников", "GunGenerator": "Генератор оружия",
                "DesiredGunsInWorkshop": "Оружия в мастерской", "GunAffixGenerator": "Аффиксы оружия",
                "GunAffixesCount": "Аффиксов на оружии", "CabinsAndBasketsAffixGenerator": "Аффиксы кабин и кузовов",
                "CabinsAndBasketsAffixesCount": "Аффиксов на кабинах", "WeaponPriceMultiplier": "Цена оружия ×",
                "WeaponPriceDispersion": "Разброс цены, %", "LookRadius": "Радиус обзора",
                "IntersectionRadius": "Радиус столкновения", "NodeScale": "Масштаб", "IsIntersecting": "Столкновения"}


def field_row(form: QFormLayout, title: str, w: QWidget):
    l = QLabel(title)
    l.setObjectName("dim")
    form.addRow(l, w)


class ProtoDialog(QDialog):
    """Town prototype: parameters and market goods."""

    def __init__(self, app, name: str, parent=None):
        super().__init__(parent)
        self.app, self.towns, self.name = app, app.towns, name
        self.el = self.towns.protos[name]
        self.setWindowTitle(f"Прототип города — {name}")
        self.setObjectName("root")
        self.resize(860, 620)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        left = Panel(margins=(8, 8, 8, 8))
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 0, 6, 0)
        form.setSpacing(5)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.edits: dict[str, QLineEdit] = {}
        for k, v in self.el.attrs.items():
            if k in ("Class", "Name"):
                continue
            e = QLineEdit(v)
            e.editingFinished.connect(lambda k=k, e=e: self._set(k, e.text().strip()))
            field_row(form, PROTO_TITLES.get(k, k), e)
            self.edits[k] = e
        sa.setWidget(w)
        left.lay.addWidget(sa)
        lay.addWidget(left, 2)

        right = Panel(margins=(8, 8, 8, 8))
        right.lay.addWidget(head("Рынок"))
        self.table = QTableWidget(0, len(ARTICLE_COLS))
        self.table.setHorizontalHeaderLabels([t for _, t in ARTICLE_COLS])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for a in self.towns.articles(name):
            self._add_row({k: a.get(k) for k, _ in ARTICLE_COLS})
        self.table.itemChanged.connect(self._articles)
        right.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = plus_button()
        add.clicked.connect(lambda: (self._add_row({"Prototype": "potato", "Amount": "0", "ExternalPriceCoefficient": "1",
                                                    "Export": "FALSE", "Import": "TRUE", "MinCount": "0", "MaxCount": "30",
                                                    "RegenerationPeriod": "300", "PriceDynamic": "1"}), self._articles()))
        rm = minus_button()
        rm.clicked.connect(self._del_row)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        done = QPushButton("Готово")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        right.lay.addLayout(row)
        lay.addWidget(right, 3)
        self._mute = False

    def _set(self, k, v):
        if self.el.get(k) != v:
            self.el.set(k, v)
            self.towns.doc.touch()

    def _add_row(self, vals: dict):
        self._mute = True
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, (k, _) in enumerate(ARTICLE_COLS):
            self.table.setItem(r, c, QTableWidgetItem(vals.get(k, "")))
        self._mute = False

    def _del_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._articles()

    def _articles(self, *_):
        if self._mute:
            return
        rows = []
        for r in range(self.table.rowCount()):
            d = {}
            for c, (k, _) in enumerate(ARTICLE_COLS):
                it = self.table.item(r, c)
                v = it.text().strip() if it else ""
                if v:
                    d[k] = v
            if d.get("Prototype"):
                rows.append(d)
        self.towns.set_articles(self.name, rows)


class NewTownDialog(QDialog):
    def __init__(self, app, mapdata, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый город")
        self.setObjectName("root")
        self.resize(420, 220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        p = Panel(margins=(10, 10, 10, 10))
        form = QFormLayout()
        form.setSpacing(6)
        self.name = QLineEdit(mapdata.unique("TheNewTown"))
        self.full = QLineEdit()
        protos = [(n, n) for n in app.towns.protos]
        self.proto = Choice(protos, protos[0][0] if protos else "")
        self.new_proto = QLineEdit()
        self.new_proto.setPlaceholderText("пусто — взять выбранный как есть")
        self.belong = NameBox(app.index, "belong", "1008", titles=app.belong_title)
        field_row(form, "Имя объекта", self.name)
        field_row(form, "Название в игре", self.full)
        field_row(form, "Прототип", self.proto)
        field_row(form, "Свой прототип на его основе", self.new_proto)
        field_row(form, "Группировка", self.belong)
        p.lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Создать")
        ok.setObjectName("accent")
        ok.clicked.connect(self.accept)
        no = QPushButton("Отмена")
        no.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(no)
        p.lay.addLayout(row)
        lay.addWidget(p)


class EventsTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.map = None
        self.cur = None            # selected scene element or trigger
        self.cur_kind = ""
        self._loading = False
        self._code_mode = False
        maps = app.game.maps()
        self.map_box = Choice([(m, m) for m in maps], "")
        self.map_box.setMinimumWidth(110)
        self.map_box.picked.connect(self.open_map)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        left = Panel()
        row = QHBoxLayout()
        row.setSpacing(3)
        self.setObjectName("eventsTab")
        self.mode_btns = {}
        self._raw = None            # a cutscene start the user asked to see as a plain trigger
        for mode, title in (("places", "Места"), ("trigs", "Триггеры"), ("paths", "Пути")):
            b = QPushButton(title)
            b.setCheckable(True)
            b.setStyleSheet("padding: 0px 3px;")
            b.clicked.connect(lambda _=False, mode=mode: self.set_mode(mode))
            row.addWidget(b)
            self.mode_btns[mode] = b
        self.b_places = self.mode_btns["places"]
        row.addStretch(1)
        self.add_btn = plus_button()
        self.add_btn.clicked.connect(self._add_clicked)
        row.addWidget(self.add_btn)
        left.lay.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск")
        self.search.setClearButtonEnabled(True)
        self._stimer = QTimer(self, singleShot=True, interval=200)
        self._stimer.timeout.connect(self.fill_tree)
        self.search.textChanged.connect(lambda _t: self._stimer.start())
        left.lay.addWidget(self.search)
        self.tree = QTreeView()
        self.tm = QStandardItemModel(self)
        self.tree.setModel(self.tm)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QTreeView.NoEditTriggers)
        self.tree.setIndentation(14)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.selectionModel().currentChanged.connect(self._tree_pick)
        left.lay.addWidget(self.tree, 1)
        split.addWidget(left)

        self.view = MapView()
        self.view.picked.connect(lambda el: self.select(el, from_map=True))
        self.view.moved.connect(self._moved)
        self.view.resized.connect(self._resized)
        self.view.create.connect(self._create_at)
        self.view.remove.connect(self._delete)
        split.addWidget(self.view)

        right = Panel()
        # one switch for any trigger, a cutscene included: its whole script as text instead of blocks
        self.t_code_btn = QPushButton("Скрипт текстом")
        self.t_code_btn.setCheckable(True)
        self.t_code_btn.setToolTip("Показать весь Lua-скрипт триггера текстом; выключить — вернуться к блокам")
        self.t_code_btn.clicked.connect(self._toggle_code)
        self.t_code_btn.hide()
        right.lay.addWidget(self.t_code_btn)
        self.stack = QStackedWidget()
        self.stack.addWidget(QWidget())
        self.stack.addWidget(self._build_place())
        self.stack.addWidget(self._build_npc())
        self.stack.addWidget(self._build_trigger())
        self.scene_view = CutsceneView(app)
        self.scene_view.changed.connect(self.update_marks)
        self.scene_view.renamed.connect(self.fill_tree)
        self.stack.addWidget(self.scene_view)
        self.path_view = PathEditor(app)
        self.path_view.changed.connect(self._path_changed)
        self.path_view.renamed.connect(lambda _n: self.fill_tree())
        self.stack.addWidget(self.path_view)
        self.stack.addWidget(self._build_bar())
        right.lay.addWidget(self.stack)
        split.addWidget(right)
        app.index.pick = self.view.pick
        app.index.center = self.map_center
        split.setSizes([290, 640, 430])
        split.setStretchFactor(1, 1)
        self.mode = "places"
        self.b_places.setChecked(True)
        start = "r1m1" if "r1m1" in maps else (maps[0] if maps else "")
        if start:
            self.map_box.set_value(start)
            self.open_map(start)

    # --- forms ---
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

    def _build_place(self):
        sa, f = self._scroll()
        idx = self.app.index
        self.p_name = QLineEdit()
        self.p_name.setToolTip("Имя объекта")
        self.p_name.editingFinished.connect(self._rename_obj)
        f.addWidget(self.p_name)
        self.p_full = QLineEdit()
        self.p_full.setPlaceholderText("Название в игре")
        self.p_full.editingFinished.connect(lambda: self._full(self.p_full))
        f.addWidget(self.p_full)
        form = QFormLayout()
        form.setSpacing(5)
        self.p_belong = NameBox(idx, "belong", titles=self.app.belong_title)
        self.p_belong.committed.connect(self._belong)
        self.p_belong.setFixedWidth(64)
        self.p_clan = QLabel()
        bl = QHBoxLayout()
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.p_belong)
        bl.addWidget(self.p_clan, 1)
        lb = QLabel("Группировка")
        lb.setObjectName("dim")
        form.addRow(lb, bl)
        xy = QHBoxLayout()
        self.p_x, self.p_z = QLineEdit(), QLineEdit()
        for e in (self.p_x, self.p_z):
            e.editingFinished.connect(self._pos_typed)
            xy.addWidget(e)
        xw = QWidget()
        xw.setLayout(xy)
        xy.setContentsMargins(0, 0, 0, 0)
        field_row(form, "X, Z", xw)
        self.p_radius = QLineEdit()
        self.p_radius.editingFinished.connect(self._radius_typed)
        self.p_radius_l = QLabel("Радиус")
        self.p_radius_l.setObjectName("dim")
        form.addRow(self.p_radius_l, self.p_radius)
        self.p_yaw = QLineEdit()
        self.p_yaw.editingFinished.connect(self._yaw_typed)
        self.p_yaw_l = QLabel("Поворот, °")
        self.p_yaw_l.setObjectName("dim")
        form.addRow(self.p_yaw_l, self.p_yaw)
        self.p_proto = Choice([(n, n) for n in self.app.towns.protos], "")
        self.p_proto.picked.connect(lambda v: self._attr("Prototype", v))
        self.p_proto_l = QLabel("Прототип")
        self.p_proto_l.setObjectName("dim")
        form.addRow(self.p_proto_l, self.p_proto)
        # town building names live in object_names.xml too
        self.p_parts = {}
        for kind, title in (("workshop", "Мастерская"), ("shop", "Магазин")):
            e = QLineEdit()
            e.editingFinished.connect(lambda kind=kind, e=e: self._part_name(kind, e))
            l = QLabel(title)
            l.setObjectName("dim")
            form.addRow(l, e)
            self.p_parts[kind] = (l, e)
        f.addLayout(form)
        self.p_proto_row = QWidget()
        pr = QHBoxLayout(self.p_proto_row)
        pr.setContentsMargins(0, 0, 0, 0)
        b1 = QPushButton("Изменить прототип")
        b1.clicked.connect(self._edit_proto)
        b2 = QPushButton("Свой прототип")
        b2.setToolTip("Скопировать прототип под новым именем и назначить городу")
        b2.clicked.connect(self._clone_proto)
        pr.addWidget(b1)
        pr.addWidget(b2)
        pr.addStretch(1)
        f.addWidget(self.p_proto_row)
        hr = QHBoxLayout()
        hr.addWidget(head("NPC"))
        hr.addStretch(1)
        an = plus_button()
        an.clicked.connect(self._add_npc)
        hr.addWidget(an)
        f.addLayout(hr)
        self.p_npcs = QVBoxLayout()
        self.p_npcs.setSpacing(2)
        f.addLayout(self.p_npcs)
        hr = QHBoxLayout()
        hr.addWidget(head("Триггеры"))
        hr.addStretch(1)
        at = plus_button()
        at.setToolTip("Триггер на въезд сюда")
        at.clicked.connect(self._add_enter_trigger)
        hr.addWidget(at)
        f.addLayout(hr)
        self.p_trigs = QVBoxLayout()
        self.p_trigs.setSpacing(2)
        f.addLayout(self.p_trigs)
        f.addStretch(1)
        return sa

    def _build_bar(self):
        sa, f = self._scroll()
        self.b_name = QLineEdit()
        self.b_name.setToolTip("Имя объекта")
        self.b_name.editingFinished.connect(self._rename_obj)
        f.addWidget(self.b_name)
        self.b_full = QLineEdit()
        self.b_full.setPlaceholderText("Название в игре")
        self.b_full.editingFinished.connect(lambda: self._full(self.b_full))
        f.addWidget(self.b_full)
        self.b_kind = Choice([("bar", "Бар с барменом"), ("barWithoutBarman", "Бар без бармена")], "bar")
        self.b_kind.picked.connect(self._bar_kind)
        r = QHBoxLayout()
        r.addWidget(self.b_kind)
        r.addStretch(1)
        f.addLayout(r)
        hr = QHBoxLayout()
        hr.addWidget(head("NPC"))
        hr.addStretch(1)
        an = plus_button()
        an.clicked.connect(self._add_npc)
        hr.addWidget(an)
        f.addLayout(hr)
        self.b_npcs = QVBoxLayout()
        self.b_npcs.setSpacing(2)
        f.addLayout(self.b_npcs)
        f.addStretch(1)
        return sa

    def _build_npc(self):
        sa, f = self._scroll()
        idx = self.app.index
        self.n_name = QLineEdit()
        self.n_name.setToolTip("Имя объекта")
        self.n_name.editingFinished.connect(self._rename_obj)
        f.addWidget(self.n_name)
        self.n_full = QLineEdit()
        self.n_full.setPlaceholderText("Имя в игре")
        self.n_full.editingFinished.connect(lambda: self._full(self.n_full))
        f.addWidget(self.n_full)
        form = QFormLayout()
        form.setSpacing(5)
        self.n_model = NameBox(idx, "npc_model")
        self.n_model.committed.connect(lambda v: self._attr("ModelName", v))
        field_row(form, "Модель", self.n_model)
        self.n_skin, self.n_cfg, self.n_spoken = QLineEdit(), QLineEdit(), QLineEdit()
        self.n_skin.editingFinished.connect(lambda: self._attr("skin", self.n_skin.text().strip(), True))
        self.n_cfg.editingFinished.connect(lambda: self._attr("cfg", self.n_cfg.text().strip(), True))
        self.n_spoken.editingFinished.connect(lambda: self._attr("SpokenCount", self.n_spoken.text().strip() or "0"))
        field_row(form, "Скин", self.n_skin)
        field_row(form, "Конфигурация", self.n_cfg)
        look = QPushButton("Подобрать вид")
        look.setToolTip("Открыть маску во вкладке «Модели» и выбрать скин и конфигурацию")
        look.clicked.connect(self._pick_look)
        field_row(form, "", look)
        field_row(form, "Разговоров на старте", self.n_spoken)
        f.addLayout(form)
        self.n_barman = QCheckBox("Бармен")
        self.n_barman.clicked.connect(lambda on: self._attr("NpcType", "BARMAN" if on else "", True))
        f.addWidget(self.n_barman)
        hr = QHBoxLayout()
        hr.addWidget(head("Диалоги"))
        hr.addStretch(1)
        nd = QPushButton("Новый диалог")
        nd.clicked.connect(self._new_dialog)
        hr.addWidget(nd)
        f.addLayout(hr)
        self.n_hello = NameList(idx, "reply", self._reply_title)
        self.n_hello.setToolTip("Движок начинает разговор с первой реплики списка, у которой выполнено условие")
        self.n_hello.changed.connect(lambda: self._attr("helloReplyNames", " ".join(self.n_hello.clean()), True))
        f.addWidget(self.n_hello)
        self.n_open = QPushButton("Открыть в диалогах")
        self.n_open.clicked.connect(self._open_dialog)
        r = QHBoxLayout()
        r.addWidget(self.n_open)
        r.addStretch(1)
        f.addLayout(r)
        f.addStretch(1)
        return sa

    def _build_trigger(self):
        sa, f = self._scroll()
        self.t_name = QLineEdit()
        self.t_name.setToolTip("Имя триггера")
        self.t_name.editingFinished.connect(self._rename_trigger)
        f.addWidget(self.t_name)
        self.t_active = QCheckBox("Активен с начала игры")
        self.t_active.clicked.connect(lambda on: self.map.set_trigger(self.cur, "active", "1" if on else "0") or self.fill_tree())
        f.addWidget(self.t_active)
        self.t_ev = EventsEditor(self.app)
        self.t_ev.changed.connect(
            lambda: self.view.highlight({e.get("ObjName", "") for e in self.t_ev.events} - {""}))
        hr = QHBoxLayout()
        hr.addWidget(head("Когда"))
        hr.addStretch(1)
        ae = plus_button()
        ae.clicked.connect(self.t_ev.add)
        hr.addWidget(ae)
        f.addLayout(hr)
        f.addWidget(self.t_ev)
        hr = QHBoxLayout()
        hr.addWidget(head("Что сделать"))
        hr.addStretch(1)
        f.addLayout(hr)
        self.t_blocks = BlockList(self.app.index, "act", "trigger", self.app.quests.title)
        self.t_blocks.changed.connect(self._blocks_changed)
        self.t_blocks.request.connect(self.handle_request)
        f.addWidget(self.t_blocks)
        self.t_code = CodeEdit("", max_lines=40)
        self.t_code.edited.connect(self._code_changed)
        self.t_code.hide()
        f.addWidget(self.t_code)
        f.addStretch(1)
        return sa

    # --- map and tree ---
    def open_map(self, name: str):
        try:
            self.map = self.app.map(name)
        except GameError as e:
            warn(self, str(e))
            return
        self.app.index.current_map = self.map
        self.cur, self.cur_kind = None, ""
        self.view._fitted = False
        self.view.load(self.app.game, self.map)
        self.fill_tree()
        self.stack.setCurrentIndex(0)

    def reload(self):
        if not self.map:
            return
        name, kind = (self.cur.get("Name"), self.cur_kind) if self.cur is not None else ("", "")
        self.map.reindex()
        self.cur = None
        self.view.select(None)
        self.view.rebuild()
        if self.mode == "paths" and self.path_view.name:
            if self.path_view.name in self.map.paths(self.path_view.kind):
                self.select_path(self.path_view.kind, self.path_view.name)
            else:
                self._blank()
            return
        el = (self.map.trigger(name) if kind == "trigger" else self.map.objects.get(name)) if name else None
        if el is not None:
            self.select(el, from_map=True)
        else:
            self._blank()

    def _blank(self):
        self.cur, self.cur_kind = None, ""
        self.t_code_btn.hide()
        self.stack.setCurrentIndex(0)
        self.view.set_marks([])
        self.view.set_path()
        self.fill_tree()

    def set_mode(self, mode: str):
        self.mode = mode
        for k, b in self.mode_btns.items():
            b.setChecked(k == mode)
        if mode != "paths":
            self.view.set_path()
        self.fill_tree()

    # --- cutscenes, paths, points ---
    def update_marks(self):
        if self.cur is None or self.cur_kind != "trigger":
            self.view.set_marks([])
        elif self.stack.currentIndex() == 4:
            self.view.set_marks(block_marks(self.scene_view.all_blocks()))
        else:
            self.view.set_marks(block_marks(lua.parse_actions(self.map.script(self.cur))))

    def handle_request(self, what: str, arg: str):
        if what == "ensure_end":
            # A cutscene start appeared. Must-happen things go into the trigger on
            # GE_END_CINEMATIC + GE_SKIP_CINEMATIC, which fires on both finish and skip. Create it if missing.
            t = self.cur
            if t is None or self.cur_kind != "trigger" or not self.map.is_cutscene(t):
                return
            if any(k == "end" for k, _a, _t in self.map.family(t)):
                return
            from .cutscene_ui import add_member
            add_member(self.map, t, "end", "")
            self.select(t)
            return
        if what == "die":            # trigger on team destruction
            t = self.map.trigger("tr" + arg + "Die")
            if t is None:
                t = self.map.add_trigger(self.map.unique_trigger("tr" + arg + "Die"), after=self.cur)
                self.map.set_events(t, [{"eventid": "GE_OBJECT_DIE", "ObjName": arg}])
                host = self.cur                      # the trigger that creates the team also activates the watcher
                if host is not None and self.cur_kind == "trigger":
                    bs = lua.parse_actions(self.map.script(host))
                    at = len(bs)
                    while at and bs[at - 1]["k"] == "selfoff":
                        at -= 1
                    bs.insert(at, {"k": "trigger", "do": "on", "trigger": t.get("Name")})
                    self.map.set_blocks(host, bs)
            self.mode = "trigs"
            self.select(t)

    def select_path(self, kind: str, name: str):
        self.cur, self.cur_kind = None, "path"
        self.t_code_btn.hide()
        self.view.select(None)
        self.view.highlight(set())
        self.view.set_marks([])
        self.path_view.show_path(self.map, kind, name)
        self.view.set_path(kind, self.path_view.pts, self.path_view.move_point)
        self.stack.setCurrentIndex(5)
        self.view.frame_path()

    def _path_changed(self):
        self.view.set_path(self.path_view.kind, self.path_view.pts, self.path_view.move_point)

    def _new_cutscene(self):
        name = ask_text(self, "Новый ролик", "Имя запускающего триггера", self.map.unique_trigger("Rolik_New"))
        if not name:
            return
        try:
            t = new_cutscene(self.map, name)
        except GameError as e:
            warn(self, str(e))
            return
        self.mode = "trigs"
        self.select(t)

    def _new_path(self, kind: str):
        base = "cam_new01" if kind == "cam" else "path_new01"
        names = set(self.map.paths(kind))
        i = 1
        while f"{base[:-2]}{i:02d}" in names:
            i += 1
        name = ask_text(self, "Новый путь камеры" if kind == "cam" else "Новый путь езды", "Имя", f"{base[:-2]}{i:02d}")
        if not name:
            return
        try:
            self.map.add_path(kind, name)
        except GameError as e:
            warn(self, str(e))
            return
        self.set_mode("paths")
        self.select_path(kind, name)
        self.fill_tree()

    def _item(self, text, el, kind, dim=False):
        it = QStandardItem(text)
        it.setData(el, EL)
        it.setData(kind, KIND)
        if dim:
            it.setForeground(QColor("#55565a"))
        return it

    def fill_tree(self):
        m = self.map
        self._loading = True
        self.tm.clear()
        flt = self.search.text().strip().lower()
        target = None

        def keep(*texts):
            return not flt or any(flt in t.lower() for t in texts)

        def note(it, el):
            nonlocal target
            if el is self.cur:
                target = it

        if m and self.mode == "places":
            for t in m.towns_list():
                full = m.full_name(t.get("Name"))
                it = self._item(full.capitalize() if full else t.get("Name"), t, "town")
                it.setToolTip(t.get("Name"))
                kids = []          # (item, element, [(npc item, npc)])
                for bar in m.bars(t):
                    title = m.full_name(bar.get("Name")) or ("Бар" if bar.get("Prototype") == "bar" else "Бар без бармена")
                    npcs = [(self._item(m.full_name(n.get("Name")) or n.get("Name"), n, "npc"), n)
                            for n in bar.elements("Object") if m.kind(n) == "npc"]
                    kids.append((self._item(title, bar, "bar"), bar, npcs))
                for l in m.locations(t):
                    kids.append((self._item(l.get("Name"), l, "loc", dim=True), l, []))
                hit = lambda k, el: keep(k.text(), el.get("Name"))
                shown = [(k, el, [x for x in sub if hit(*x)] if flt and not hit(k, el) else sub)
                         for k, el, sub in kids if hit(k, el) or any(hit(*x) for x in sub)]
                if keep(it.text(), t.get("Name")) or shown:
                    self.tm.appendRow(it)
                    note(it, t)
                    for k, el, sub in (kids if not flt else shown):
                        k.setToolTip(el.get("Name") + ("" if m.kind(el) != "bar" else f" ({el.get('Prototype')})"))
                        it.appendRow(k)
                        note(k, el)
                        for nk, n in sub:
                            nk.setToolTip(n.get("Name"))
                            k.appendRow(nk)
                            note(nk, n)
            for l in m.locations():
                npcs = m.npcs(l)
                if not keep(l.get("Name"), *[n.get("Name") for n in npcs]):
                    continue
                it = self._item(l.get("Name"), l, "loc")
                self.tm.appendRow(it)
                note(it, l)
                for n in npcs:
                    k = self._item(m.full_name(n.get("Name")) or n.get("Name"), n, "npc")
                    k.setToolTip(n.get("Name"))
                    it.appendRow(k)
                    note(k, n)
        elif m and self.mode == "paths":
            for kind, title in (("cam", "Камера"), ("ext", "Езда")):
                group = self._item(title, None, "group")
                self.tm.appendRow(group)
                for name in m.paths(kind):
                    if not keep(name):
                        continue
                    it = self._item(name, (kind, name), "path")
                    group.appendRow(it)
                    if self.cur_kind == "path" and (kind, name) == (self.path_view.kind, self.path_view.name):
                        target = it
            self.tree.expandAll()
        elif m:
            # a cutscene is its start trigger with the triggers it drives folded under it
            parts: dict[int, list] = {}
            member = set()
            for s in m.cutscenes():
                fam = [t for _k, _a, t in m.family(s) if not m.is_cutscene(t)]
                parts[id(s)] = fam
                member.update(id(t) for t in fam)

            def hit(t):
                return keep(t.get("Name"), m.script(t) if len(flt) > 2 else "")
            for t in m.triggers():
                if id(t) in member:
                    continue
                kids = parts.get(id(t), [])
                shown = [k for k in kids if hit(k)]
                if not hit(t) and not shown:
                    continue
                it = self._item(t.get("Name"), t, "trigger", dim=t.get("active") != "1")
                if id(t) in parts:
                    it.setIcon(theme.icon("film"))
                    it.setToolTip("Ролик: запускающий триггер и его части")
                self.tm.appendRow(it)
                note(it, t)
                for k in (shown if flt else kids):
                    ki = self._item(k.get("Name"), k, "trigger", dim=True)
                    ki.setToolTip("Часть ролика; открывается как обычный триггер")
                    it.appendRow(ki)
                    note(ki, k)
        if flt:
            self.tree.expandAll()
        else:
            p = target.parent() if target is not None else None
            while p is not None:
                self.tree.setExpanded(p.index(), True)
                p = p.parent()
        self._loading = False
        if target is not None:
            self.tree.setCurrentIndex(target.index())
            self.tree.scrollTo(target.index())

    def _tree_pick(self, cur, _prev):
        if self._loading or not cur.isValid():
            return
        if cur.data(KIND) == "path":
            self.select_path(*cur.data(EL))
        elif cur.data(KIND) != "group":
            self.select(cur.data(EL), from_tree=True)

    def _tree_menu(self, pos):
        ix = self.tree.indexAt(pos)
        m = QMenu(self)
        if ix.isValid() and ix.data(KIND) == "path":
            kind, name = ix.data(EL)
            m.addAction("Удалить", lambda: self._delete_path(kind, name))
        elif ix.isValid() and ix.data(KIND) != "group":
            el, kind = ix.data(EL), ix.data(KIND)
            if kind in ("town", "loc", "bar"):
                m.addAction("Добавить NPC", lambda: (self.select(el), self._add_npc()))
            if kind == "town":
                m.addAction("Добавить бар", lambda: self._add_bar(el, True))
                m.addAction("Добавить бар без бармена", lambda: self._add_bar(el, False))
            if kind == "trigger" and self.map.is_cutscene(el):
                if self._raw is el:
                    m.addAction("Показать ролик", lambda: self._as_trigger(None, el))
                else:
                    m.addAction("Показать как обычный триггер", lambda: self._as_trigger(el, el))
                m.addAction("Удалить ролик целиком", lambda: self._delete_scene(el))
            m.addAction("Удалить", lambda: self._delete(el))
        else:
            if self.mode == "trigs":
                m.addAction("Новый триггер", self._add_trigger)
                m.addAction("Новый ролик", self._new_cutscene)
            elif self.mode == "paths":
                m.addAction("Новый путь камеры", lambda: self._new_path("cam"))
                m.addAction("Новый путь езды", lambda: self._new_path("ext"))
            else:
                m.addAction("Новая локация", lambda: self._create_at("loc", self.map_center()[0], self.map_center()[1]))
                m.addAction("Новый город", lambda: self._create_at("town", self.map_center()[0], self.map_center()[1]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _as_trigger(self, raw, el):
        self._raw = raw
        self.select(el)

    def map_center(self):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        return c.x(), self.view.world - c.y()

    def _delete_path(self, kind, name):
        if confirm(self, f"Удалить путь «{name}»?"):
            self.map.remove_path(kind, name)
            self._blank()

    def _delete_scene(self, start):
        fam = self.map.family(start)
        if not confirm(self, f"Удалить ролик «{start.get('Name')}» и {len(fam)} его частей?"):
            return
        for _k, _a, t in fam:
            self.map.remove_trigger(t)
        self.map.remove_trigger(start)
        self._blank()

    def _add_clicked(self):
        if self.mode == "trigs":
            m = QMenu(self)
            m.addAction("Триггер", self._add_trigger)
            m.addAction("Ролик", self._new_cutscene)
            m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))
        elif self.mode == "paths":
            m = QMenu(self)
            m.addAction("Путь камеры", lambda: self._new_path("cam"))
            m.addAction("Путь езды", lambda: self._new_path("ext"))
            m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))
        else:
            m = QMenu(self)
            x, z = self.map_center()
            m.addAction("Локация", lambda: self._create_at("loc", x, z))
            m.addAction("Город", lambda: self._create_at("town", x, z))
            m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    # --- selection ---
    def select(self, el, from_tree=False, from_map=False):
        if el is None or self.map is None:
            return
        kind = "trigger" if el.tag == "trigger" else self.map.kind(el)
        want = "places"
        timeline = False
        if kind == "trigger":       # a cutscene start opens as a timeline unless asked otherwise
            want = "trigs"
            timeline = self.map.is_cutscene(el) and el is not self._raw and not self._code_mode
            if el is not self._raw:
                self._raw = None
        self.cur, self.cur_kind = el, kind
        self.t_code_btn.setVisible(kind == "trigger")
        self.view.set_path()
        if want != self.mode:
            self.mode = want
            for k, b in self.mode_btns.items():
                b.setChecked(k == want)
            from_tree = False
        if not from_tree:
            self.fill_tree()
        self._loading = True
        if kind == "trigger":
            self.view.select(None)
            self.view.highlight({e.get("ObjName", "") for e in self.map.events(el)} - {""})
            if timeline:
                self.scene_view.show_scene(self.map, el)
                self.stack.setCurrentIndex(4)
            else:
                self._show_trigger()
                self.stack.setCurrentIndex(3)
            self.update_marks()
        elif kind == "bar":
            self.view.highlight(set())
            self.view.set_marks([])
            self.view.select(el.parent, center=not from_map)
            self._show_bar()
            self.stack.setCurrentIndex(6)
        elif kind == "npc":
            host = el.parent
            if self.map.kind(host) == "bar":
                host = host.parent
            self.view.highlight(set())
            self.view.set_marks([])
            self.view.select(host, center=not from_map)
            self._show_npc()
            self.stack.setCurrentIndex(2)
        else:
            self.view.highlight(set())
            self.view.set_marks([])
            self.view.select(el, center=not from_map)
            self._show_place()
            self.stack.setCurrentIndex(1)
        self._loading = False

    def _clear(self, lay):
        clear_layout(lay)

    def _link(self, text: str, fn, tip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("flat")
        b.setStyleSheet("text-align: left;")
        b.setCursor(Qt.PointingHandCursor)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def _show_place(self):
        el, m = self.cur, self.map
        town = self.cur_kind == "town"
        self.p_name.setText(el.get("Name"))
        self.p_full.setText(m.full_name(el.get("Name")))
        self.p_belong.set_value(el.get("Belong"))
        self.p_clan.setText(self.app.belong_title(el.get("Belong")))
        p = m.pos(el) or (0, 0, 0)
        self.p_x.setText(f"{p[0]:.1f}")
        self.p_z.setText(f"{p[2]:.1f}")
        self.p_radius.setText(el.get("Radius"))
        self.p_yaw.setText(f"{rot_to_yaw(el.get('Rot')):.0f}" if el.get("Rot") else "")
        for w in (self.p_radius, self.p_radius_l):
            w.setVisible(not town)
        for w in (self.p_proto, self.p_proto_l, self.p_proto_row):
            w.setVisible(town)
        if town:
            self.p_proto.set_value(el.get("Prototype"))
        for kind, (l, e) in self.p_parts.items():
            part = self._part(kind) if town else None
            l.setVisible(part is not None)
            e.setVisible(part is not None)
            if part is not None:
                e.setText(m.full_name(part.get("Name")))
                e.setToolTip(part.get("Name"))
        self._clear(self.p_npcs)
        for n in m.npcs(el):
            self.p_npcs.addWidget(self._link(m.full_name(n.get("Name")) or n.get("Name"),
                                             lambda _=False, n=n: self.select(n), n.get("Name")))
        self._clear(self.p_trigs)
        names = {el.get("Name")} | {c.get("Name") for c in el.iter("Object") if m.kind(c) == "loc"}
        for t in m.triggers():
            if any(e.get("ObjName") in names for e in m.events(t)):
                self.p_trigs.addWidget(self._link(t.get("Name"), lambda _=False, t=t: self.select(t)))

    def _show_npc(self):
        el, m = self.cur, self.map
        self.n_name.setText(el.get("Name"))
        self.n_full.setText(m.full_name(el.get("Name")))
        self.n_model.set_value(el.get("ModelName"))
        self.n_skin.setText(el.get("skin"))
        self.n_cfg.setText(el.get("cfg"))
        self.n_spoken.setText(el.get("SpokenCount"))
        self.n_barman.setChecked(el.get("NpcType") == "BARMAN")
        self.n_hello.set_values(el.get("helloReplyNames").split())

    def _reply_title(self, name: str) -> str:
        el = self.app.dialogs.items.get(name)
        return el.get("text") if el is not None else "нет такой реплики"

    # --- editing places ---
    def _pick_look(self):
        el, m = self.cur, self.map
        if el is None:
            return

        def done(skin, cfg):
            m.set_attr(el, "skin", skin, True)
            m.set_attr(el, "cfg", cfg, True)
            self.select(el)
        self.app.pick_look(el.get("ModelName"), el.get("skin"), el.get("cfg"),
                           m.full_name(el.get("Name")) or el.get("Name"), done)

    def _attr(self, attr, value, optional=False):
        if self._loading or self.cur is None:
            return
        self.map.set_attr(self.cur, attr, value, optional)

    def _rename_obj(self):
        w = {"npc": self.n_name, "bar": self.b_name}.get(self.cur_kind, self.p_name)
        new = w.text().strip()
        if self._loading or self.cur is None or new == self.cur.get("Name"):
            return
        try:
            self.map.rename(self.cur, new, self.app.dialogs, self.app.quests)
        except GameError as e:
            w.setText(self.cur.get("Name"))
            warn(self, str(e))
            return
        self.fill_tree()
        self.view.viewport().update()

    def _full(self, w):
        if self._loading or self.cur is None:
            return
        name = self.cur.get("Name")
        if self.map.full_name(name) != w.text().strip():
            self.map.set_full_name(name, w.text().strip())
            if self.cur_kind == "town":   # the caption shown near a town is the name of its enter location
                if name + "_enter" in self.map.objects:
                    self.map.set_full_name(name + "_enter", w.text().strip())
            self.fill_tree()
            self.view.viewport().update()

    def _part(self, kind: str):
        """Building of the selected town: bar, workshop or shop."""
        if self.cur is None:
            return None
        return next((c for c in self.cur.elements("Object") if self.map.kind(c) == kind), None)

    def _part_name(self, kind: str, w):
        part = self._part(kind)
        if self._loading or part is None:
            return
        if self.map.full_name(part.get("Name")) != w.text().strip():
            self.map.set_full_name(part.get("Name"), w.text().strip())

    def _belong(self, v):
        if not self._loading and self.cur is not None and v:
            self.map.set_belong(self.cur, v)
            self.p_clan.setText(self.app.belong_title(v))

    def _moved(self, el, x, z):
        self.map.move(el, x, z)
        self.view.sync(el)
        if el is self.cur:
            self._loading = True
            self.p_x.setText(f"{x:.1f}")
            self.p_z.setText(f"{z:.1f}")
            self._loading = False

    def _resized(self, el, r):
        self.map.set_attr(el, "Radius", fmt(r))
        self.view.sync(el)
        if el is self.cur:
            self.p_radius.setText(fmt(r))

    def _pos_typed(self):
        if self._loading or self.cur is None:
            return
        try:
            x, z = float(self.p_x.text().replace(",", ".")), float(self.p_z.text().replace(",", "."))
        except ValueError:
            return
        p = self.map.pos(self.cur)
        if p and (abs(p[0] - x) > 0.05 or abs(p[2] - z) > 0.05):
            self.map.move(self.cur, x, z)
            self.view.sync(self.cur)

    def _radius_typed(self):
        if self._loading or self.cur is None:
            return
        try:
            r = float(self.p_radius.text().replace(",", "."))
        except ValueError:
            return
        if fmt(r) != self.cur.get("Radius"):
            self.map.set_attr(self.cur, "Radius", fmt(r))
            self.view.sync(self.cur)

    def _yaw_typed(self):
        if self._loading or self.cur is None:
            return
        t = self.p_yaw.text().strip()
        try:
            rot = yaw_to_rot(float(t.replace(",", "."))) if t else ""
        except ValueError:
            return
        if rot != self.cur.get("Rot") and (t or self.cur.get("Rot")):
            self.map.set_attr(self.cur, "Rot", rot, optional=True)

    def _create_at(self, kind, x, z):
        m = self.map
        if kind == "loc":
            name = ask_text(self, "Новая локация", "Имя объекта", m.unique("New_loc"))
            if not name:
                return
            try:
                el = m.add_location(name, x, z)
            except GameError as e:
                warn(self, str(e))
                return
        else:
            if not self.app.towns.protos:
                warn(self, "В towns.xml нет прототипов городов.")
                return
            dlg = NewTownDialog(self.app, m, self)
            if not dlg.exec():
                return
            try:
                proto = dlg.proto.value()
                custom = dlg.new_proto.text().strip()
                if custom:
                    self.app.towns.clone(proto, custom)
                    proto = custom
                    self.p_proto.addItem(custom, custom)
                el = m.add_town(dlg.name.text().strip(), proto, x, z, dlg.belong.text().strip() or "1008",
                                dlg.full.text().strip())
            except GameError as e:
                warn(self, str(e))
                return
        self.set_mode("places")
        self.view.rebuild()
        self.select(el)
        if kind == "town" and dlg.new_proto.text().strip():
            self._edit_proto()

    def _delete(self, el):
        m = self.map
        name = el.get("Name")
        if el.tag == "trigger":
            if confirm(self, f"Удалить триггер «{name}»?"):
                m.remove_trigger(el)
                self.cur = None
                self.stack.setCurrentIndex(0)
                self.fill_tree()
            return
        users = [t.get("Name") for t in m.triggers() if any(e.get("ObjName") == name for e in m.events(t))]
        text = f"Удалить «{m.full_name(name) or name}»?"
        if users:
            text += f"\nНа него ссылаются триггеры: {', '.join(users[:6])}."
        if el.elements("Object"):
            text += "\nВместе с ним исчезнет всё, что внутри."
        if not confirm(self, text):
            return
        m.remove(el)
        self.cur = None
        self.stack.setCurrentIndex(0)
        self.view.select(None)
        self.view.rebuild()
        self.fill_tree()

    def _add_bar(self, town, with_barman: bool):
        bar = self.map.add_bar(town, with_barman)
        self.select(bar)
        self.b_full.setFocus()

    def _show_bar(self):
        el, m = self.cur, self.map
        self.b_name.setText(el.get("Name"))
        self.b_full.setText(m.full_name(el.get("Name")))
        self.b_kind.set_value(el.get("Prototype"))
        self._clear(self.b_npcs)
        for n in el.elements("Object"):
            if m.kind(n) == "npc":
                self.b_npcs.addWidget(self._link(m.full_name(n.get("Name")) or n.get("Name"),
                                                 lambda _=False, n=n: self.select(n), n.get("Name")))

    def _bar_kind(self, proto: str):
        if self._loading or self.cur is None:
            return
        self.map.set_bar_kind(self.cur, proto == "bar")
        self.select(self.cur)

    def _add_npc(self):
        if self.cur is None or self.cur_kind not in ("town", "loc", "bar"):
            return
        name = ask_text(self, "Новый NPC", "Имя объекта", self.map.unique("NewNpc"))
        if not name:
            return
        models = self.app.index.choices("npc_model")
        try:
            npc = self.map.add_npc(self.cur, name, models[0] if models else "r1_man")
        except GameError as e:
            warn(self, str(e))
            return
        self.select(npc)
        self.n_full.setFocus()

    def _edit_proto(self):
        if self.cur is not None and self.cur.get("Prototype") in self.app.towns.protos:
            ProtoDialog(self.app, self.cur.get("Prototype"), self).exec()

    def _clone_proto(self):
        src = self.cur.get("Prototype")
        name = ask_text(self, "Свой прототип", "Имя нового прототипа", f"{self.map.name}_{self.cur.get('Name')}")
        if not name:
            return
        try:
            self.app.towns.clone(src, name)
        except GameError as e:
            warn(self, str(e))
            return
        self.p_proto.addItem(name, name)
        self.p_proto.set_value(name)
        self.map.set_attr(self.cur, "Prototype", name)
        self._edit_proto()

    # --- NPC dialogs ---
    def _new_dialog(self):
        npc = self.cur.get("Name")
        d = self.app.dialogs
        i = 0
        while f"{npc}_hellodlg{i}" in d.items:
            i += 1
        name = f"{npc}_hellodlg{i}"
        folder = f"Root/{self.map.name}/{self.map.full_name(npc) or npc}"
        d.add(name, "NPC", folder, "")
        self.map.set_attr(self.cur, "helloReplyNames", (self.cur.get("helloReplyNames") + " " + name).strip(), True)
        self.n_hello.set_values(self.cur.get("helloReplyNames").split())
        self.app.open_dialog(name)

    def _open_dialog(self):
        hello = [h for h in self.cur.get("helloReplyNames").split() if h in self.app.dialogs.items]
        if hello:
            self.app.open_dialog(hello[0])

    # --- triggers ---
    def _show_trigger(self):
        t, m = self.cur, self.map
        self.t_name.setText(t.get("Name"))
        self.t_active.setChecked(t.get("active") == "1")
        self.t_ev.set_trigger(m, t)
        self._fill_script()

    def _fill_script(self):
        code = self.map.script(self.cur)
        self.t_blocks.setVisible(not self._code_mode)
        self.t_code.setVisible(self._code_mode)
        if self._code_mode:
            self.t_code.setPlainText(lua._dedent(code, len(code) - len(code.lstrip()), len(code.rstrip())) if code.strip() else "")
            self.t_code._saved = self.t_code.toPlainText()
        else:
            self.t_blocks.set_blocks(lua.parse_actions(code))

    def _toggle_code(self, on):
        self._code_mode = on
        if self.cur is not None and self.cur_kind == "trigger":
            self.select(self.cur, from_tree=True)      # a cutscene swaps its timeline for the start trigger's text

    def _blocks_changed(self):
        if not self._loading and self.cur_kind == "trigger":
            self.map.set_blocks(self.cur, self.t_blocks.blocks)
            self.update_marks()

    def _code_changed(self):
        if not self._loading and self.cur_kind == "trigger":
            self.map.set_blocks(self.cur, [{"k": "lua", "code": self.t_code.toPlainText()}])

    def _add_trigger(self, events=None, base="NewTrigger"):
        name = ask_text(self, "Новый триггер", "Имя", self.map.unique(base))
        if not name:
            return
        try:
            t = self.map.add_trigger(name)
        except GameError as e:
            warn(self, str(e))
            return
        self.map.set_events(t, events or [{"eventid": "GE_TIME_PERIOD", "timeout": "0"}])
        if events:
            self.map.set_trigger(t, "active", "1")
        self.select(t)

    def _add_enter_trigger(self):
        if self.cur is None:
            return
        name = self.cur.get("Name")
        target = name + "_enter" if self.cur_kind == "town" and name + "_enter" in self.map.objects else name
        self._add_trigger([{"eventid": "GE_OBJECT_ENTERS_LOCATION", "ObjName": target}], "tr" + name)

    def _rename_trigger(self):
        new = self.t_name.text().strip()
        if self._loading or self.cur is None or new == self.cur.get("Name"):
            return
        try:
            self.map.rename_trigger(self.cur, new, self.app.dialogs, self.app.quests)
        except GameError as e:
            self.t_name.setText(self.cur.get("Name"))
            warn(self, str(e))
            return
        self.fill_tree()

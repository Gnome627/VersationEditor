"""Models tab: GAM preview, skins as a materials x skins table, visibility configs of masks."""
from __future__ import annotations

import re
from pathlib import Path

import math

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QIcon, QImage, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
                               QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget)

from . import gam as G
from . import theme
from .game import load_image
from .gam import GamError
from .models import KIND_TITLES
from .widgets import (Choice, NameBox, Panel, ask_text, clear_layout, head, minus_button, plus_button, warn)

ID = Qt.UserRole + 1
VEH = Qt.UserRole + 2
THUMB = 40
GROUP_TITLES = {"masks": "Лицо", "hats": "Шапка", "glasses": "Очки", "respirators": "Респиратор", "collars": "Воротник",
                "bodies": "Одежда", "special": "Особое", "headphones": "Наушники", "caps": "Кепка"}
STATES = ["целая", "повреждённая", "разбитая"]


class _Names:
    """Completion source for NameBox."""

    def __init__(self, fn):
        self.fn = fn

    def choices(self, _type):
        return self.fn()


class _NoView(QWidget):
    """Stand-in for the 3D view where OpenGL is not available (offscreen checks)."""

    pointPicked = Signal(int)
    pointMoved = Signal(int, float, float, float)
    pointRotated = Signal(int, int, float)
    pointDropped = Signal()

    def set_model(self, *a, **k): pass
    def set_parts(self, *a, **k): pass
    def set_points(self, *a, **k): pass
    def set_memory(self, *a, **k): pass
    def bake_ao(self, *a, **k): return None
    def show_state(self, *a, **k): pass
    def reload_textures(self): pass


def make_view() -> QWidget:
    if QGuiApplication.platformName() == "offscreen":
        return _NoView()
    from .glview import ModelView
    return ModelView()


def dim(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("dim")
    return l


class ModelsTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app, self.lib = app, app.models
        self.doc = None
        self.rel = ""
        self.model_id = ""
        self.skin = self.mat = 0
        self.choice: list[int] = []
        self._pick = None               # (title, callback) while a look is being chosen for an NPC
        self.vehicle = ""               # vehicle prototype shown as a whole, or ""
        self.vparts: list = []
        self.vrow = 0
        self.vshown: dict[str, str] = {}    # kind -> file of the cabin/basket standing on the chassis
        self.vnote = ""
        self.wizard = None              # conversion to Skinned in progress (skinned_ui.SkinnedWizard)
        self.lp_mode = False            # the table and the right panel edit load points instead of skins
        self.lp = -1                    # row of the selected load point
        self._offsets: dict[str, tuple] = {}
        self.vchoices: dict = {}
        self._built = False
        self._loading = False
        self._thumbs: dict[str, QPixmap] = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split)

        left = Panel()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск")
        self.search.setClearButtonEnabled(True)
        self._flt = QTimer(self, singleShot=True, interval=200)
        self._flt.timeout.connect(self._fill_tree)
        self.search.textChanged.connect(lambda _t: self._flt.start())
        left.lay.addWidget(self.search)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tm = QStandardItemModel(self)
        self.tree.setModel(self.tm)
        self.tree.selectionModel().currentChanged.connect(self._tree_pick)
        left.lay.addWidget(self.tree, 1)
        split.addWidget(left)

        mid = QSplitter(Qt.Vertical)
        vp = Panel(margins=(2, 2, 2, 2))
        self.view = make_view()
        vp.lay.addWidget(self.view, 1)
        self.vbar = QWidget(vp)                 # cabin / basket pickers over the corner of the view
        self.vbar_lay = QHBoxLayout(self.vbar)
        self.vbar_lay.setContentsMargins(0, 0, 0, 0)
        self.vbar_lay.setSpacing(4)
        self.vbar.move(12, 12)
        self.vbar.hide()
        mid.addWidget(vp)
        tp = Panel()
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.mode_btns = []
        for i, title in enumerate(("Скины", "Load Points")):
            b = QPushButton(title)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setStyleSheet("padding: 1px 10px; font-size: 9pt;")
            b.clicked.connect(lambda _=False, i=i: self.set_lp_mode(bool(i)))
            bar.addWidget(b)
            self.mode_btns.append(b)
        bar.addStretch(1)
        self.add_skin = plus_button("Добавить скин: копия выбранного")
        self.add_skin.clicked.connect(self._add_skin)
        self.del_skin = minus_button("Удалить выбранный скин")
        self.del_skin.clicked.connect(self._del_skin)
        bar.addWidget(self.add_skin)
        bar.addWidget(self.del_skin)
        tp.lay.addLayout(bar)
        self.table = QTableWidget()
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(THUMB, THUMB))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(THUMB + 14)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(THUMB + 6)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.currentCellChanged.connect(self._cell)
        self.lp_table = QTableWidget()
        self.lp_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lp_table.setColumnCount(6)
        self.lp_table.setHorizontalHeaderLabels(["X", "Y", "Z", "RX°", "RY°", "RZ°"])
        self.lp_table.horizontalHeader().setDefaultSectionSize(84)
        self.lp_table.verticalHeader().setDefaultSectionSize(22)
        self.lp_table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._lp_pick(r))
        self.lp_table.itemChanged.connect(self._lp_cell)
        self.tables = QStackedWidget()
        self.tables.addWidget(self.table)
        self.tables.addWidget(self.lp_table)
        tp.lay.addWidget(self.tables, 1)
        self.view.pointPicked.connect(self._lp_pick)
        self.view.pointMoved.connect(self._lp_dragged)
        self.view.pointRotated.connect(self._lp_rotated)
        self.view.pointDropped.connect(self._lp_dropped)
        mid.addWidget(tp)
        mid.setStretchFactor(0, 1)
        mid.setSizes([560, 250])
        split.addWidget(mid)

        right = Panel()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.form = QVBoxLayout(body)
        self.form.setContentsMargins(2, 2, 6, 2)
        self.form.setSpacing(5)
        sa.setWidget(body)
        right.lay.addWidget(sa)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 880, 300])

    # --- model list ---
    def activate(self):
        if not self._built:
            self._built = True
            self._fill_tree()

    def _fill_tree(self):
        flt = self.search.text().strip().lower()
        self.tm.clear()
        folders: dict[str, QStandardItem] = {}
        target = None

        def folder(path: str) -> QStandardItem:
            if path not in folders:
                parent, _, name = path.rpartition("/")
                it = QStandardItem(theme.icon("truck" if path == "Машины" else "folder"), name)
                it.setSelectable(False)
                (folder(parent) if parent else self.tm.invisibleRootItem()).appendRow(it)
                folders[path] = it
            return folders[path]
        for name in self.lib.vehicles():
            if flt and flt not in name.lower():
                continue
            it = QStandardItem(name)
            it.setData(name, VEH)
            it.setToolTip("Машина целиком: шасси, все кабины и кузова, колёса, подвеска")
            folder("Машины").appendRow(it)
            if name == self.vehicle:
                target = it
        for mid, rel in sorted(self.lib.models(), key=lambda m: (m[1].lower().rsplit("/", 1)[0], m[0].lower())):
            if flt and flt not in mid.lower() and flt not in rel.lower():
                continue
            path = rel.lower().removeprefix("data/models/").rsplit("/", 1)[0] if "/" in rel else ""
            it = QStandardItem(mid)
            it.setData(mid, ID)
            it.setToolTip(rel)
            (folder(path) if path else self.tm.invisibleRootItem()).appendRow(it)
            if mid == self.model_id and not self.vehicle:
                target = it
        if flt:
            self.tree.expandAll()
        if target is not None:
            self._loading = True
            self.tree.setCurrentIndex(target.index())
            self.tree.scrollTo(target.index())
            self._loading = False

    def _tree_pick(self, cur, _prev):
        if self._loading:
            return
        veh, mid = cur.data(VEH), cur.data(ID)
        if veh and veh != self.vehicle:
            self._pick = None
            self.open_vehicle(veh)
        elif mid and (mid != self.model_id or self.vehicle):
            self._pick = None
            self.open(mid)

    # --- opening ---
    def open(self, model_id: str, skin: int = 0, cfg: int = 0) -> bool:
        rel = self.lib.file_of(model_id)
        if rel is None:
            warn(self, f"Модели {model_id} нет в animmodels.xml.")
            return False
        try:
            doc = self.lib.doc(rel)
            doc.gam.meshes
        except (GamError, OSError, ValueError, IndexError, Exception) as e:      # a broken file must not kill the tab
            warn(self, f"{rel}: не удалось прочитать модель.\n{e}")
            return False
        if self.wizard is not None:
            self._wizard_close()
        self.doc, self.rel, self.model_id = doc, rel, model_id
        self.vehicle = ""
        self.vbar.hide()
        self.lp = -1
        self.add_skin.setVisible(not self.lp_mode)
        self.del_skin.setVisible(not self.lp_mode)
        g = doc.gam
        self.skin = min(max(skin, 0), len(g.skins) - 1)
        self.mat = 0
        self.choice = g.config_to_choice(min(max(cfg, 0), g.config_count() - 1))
        self.view.set_model(g, lambda n: self.lib.find_texture(rel, n), self.lib.transparent(model_id))
        self.view.show_state(self.skin, g.visible(self.choice), refit=True)
        self._fill_table()
        self._fill_form()
        self._show_lp()
        return True

    def pick(self, model_id: str, skin: str, cfg: str, title: str, done):
        """Choose skin and config for an NPC or a cutscene message; `done(skin, cfg)` writes them back."""
        self.activate()
        if self.open(model_id, int(skin) if skin.isdigit() else 0, int(cfg) if cfg.isdigit() else 0):
            self._pick = (title, done)
            self._fill_form()
            self.search.setText("")
            self._fill_tree()

    # --- whole vehicle ---
    def open_vehicle(self, name: str) -> bool:
        parts = []
        for q in self.lib.vehicle_parts(name):
            try:
                self.lib.doc(q.rel).gam.meshes
                parts.append(q)
            except Exception:       # a broken part is left out, the rest still opens
                pass
        if not parts:
            warn(self, f"{name}: у прототипа нет моделей деталей.")
            return False
        if self.wizard is not None:
            self._wizard_close()
        self.vehicle, self.vparts, self.vrow, self.vnote = name, parts, 0, ""
        self.vshown = {}
        for q in parts:
            self.vshown.setdefault(q.kind, q.rel)
        self.doc = self.lib.doc(parts[0].rel)
        self.rel, self.model_id = parts[0].rel, parts[0].model
        self.skin = 0
        self.add_skin.setVisible(False)
        self.del_skin.setVisible(False)
        self.lp = -1
        self._vehicle_bar()
        self._vehicle_view(refit=True)
        self._fill_table()
        self._fill_form()
        self._show_lp()
        return True

    def _vehicle_bar(self):
        """Pickers of the cabin and the basket standing on the chassis, when there is a choice."""
        clear_layout(self.vbar_lay)
        self.vchoices = {}
        shown = False
        for kind in ("CABIN", "BASKET"):
            opts = [(q.rel, q.model) for q in self.vparts if q.kind == kind]
            if len(opts) < 2:
                continue
            c = Choice(opts, self.vshown.get(kind, ""))
            c.setToolTip("Кабина на шасси" if kind == "CABIN" else "Кузов на шасси")
            c.picked.connect(lambda rel, kind=kind: self._vehicle_put(kind, rel))
            self.vbar_lay.addWidget(c)
            self.vchoices[kind] = c
            shown = True
        self.vbar.setVisible(shown)
        self._vbar_fit()
        self.vbar.raise_()

    def _vbar_fit(self):
        """Size the bar by its pickers (their width follows the chosen name)."""
        hints = [c.sizeHint() for c in self.vchoices.values()]
        if hints:
            self.vbar.resize(sum(h.width() for h in hints) + self.vbar_lay.spacing() * (len(hints) - 1),
                             max(h.height() for h in hints))

    def _vehicle_put(self, kind: str, rel: str):
        if rel and self.vshown.get(kind) != rel:
            self.vshown[kind] = rel
            self._vehicle_view()
            self._vbar_fit()
            self._show_lp()

    def _vehicle_view(self, refit: bool = False):
        """Chassis with the chosen cabin and basket on its load points, wheels on theirs."""
        from .glview import Part
        by = {q.rel: q for q in self.vparts}
        chassis = next((q for q in self.vparts if q.kind == "CHASSIS"), None)
        cg = self.lib.doc(chassis.rel).gam if chassis else None

        def part(rel, offset=(0.0, 0.0, 0.0), mirror=False):
            g = self.lib.doc(rel).gam
            return Part(g, lambda n, rel=rel: self.lib.find_texture(rel, n), offset,
                        g.visible(g.config_to_choice(0)), mirror, self.wizard.mats(rel) if self.wizard else None)
        parts = [part(chassis.rel)] if chassis else []
        self._offsets = {}
        for kind in ("CABIN", "BASKET"):
            rel = self.vshown.get(kind)
            if rel:
                at = cg.load_point(by[rel].lp) if cg and by[rel].lp else None
                self._offsets[rel] = at or (0.0, 0.0, 0.0)
                parts.append(part(rel, at or (0.0, 0.0, 0.0)))
        if cg:
            # wheel N of the prototype stands on the N-th LP_WHL*, its suspension on the LP_SSP* of the same name
            lps = sorted(n.name for n in cg.nodes if n.name.upper().startswith("LP_WHL"))
            for (wheel, susp), lp in zip(self.lib.vehicle_wheels(self.vehicle), lps):
                right = lp.upper().endswith("R")
                for rel, at in ((wheel, lp), (susp, "LP_SSP" + lp[6:])):
                    pos = cg.load_point(at) if rel else None
                    if pos is None:
                        continue
                    try:
                        own_side = by[rel].model.lower().endswith(("r", "right")) if rel in by else False
                        parts.append(part(rel, pos, right and not own_side))
                        self._offsets.setdefault(rel, pos)
                    except Exception:       # a broken part is left out
                        pass
        self.view.set_parts(parts, refit=refit)
        self.view.show_state(self.skin)

    def _fill_vtable(self):
        t = self.table
        self._loading = True
        t.clear()
        gams = [self.lib.doc(q.rel).gam for q in self.vparts]
        cols = max(len(g.skins) for g in gams)
        t.setRowCount(len(self.vparts))
        t.setColumnCount(cols)
        for c in range(cols):
            t.setHorizontalHeaderItem(c, QTableWidgetItem(str(c)))
        for r, (q, g) in enumerate(zip(self.vparts, gams)):
            h = QTableWidgetItem(q.model)
            h.setToolTip(f"{KIND_TITLES[q.kind]}: {', '.join(q.protos)}\n{q.rel}")
            t.setVerticalHeaderItem(r, h)
            dead = self.lib.dead_skins(q.rel, g)
            for c in range(cols):
                it = QTableWidgetItem()
                if c >= len(g.skins):
                    it.setFlags(Qt.NoItemFlags)
                    it.setBackground(QBrush(QColor(0, 0, 0, 70)))
                    it.setToolTip("У детали нет такого скина")
                else:
                    names = [m.tex(G.TEX_DIFFUSE) for m in g.skins[c] if m.tex(G.TEX_DIFFUSE)]
                    missing = [x.name for m in g.skins[c] for x in m.textures
                               if self.lib.find_texture(q.rel, x.name) is None]
                    self.rel = q.rel
                    pm = self._thumb(names[0]) if names else None
                    if pm is not None:
                        it.setIcon(QIcon(pm))
                    if missing:
                        it.setBackground(QBrush(QColor("#a8483c")))
                    it.setToolTip("\n".join((["разбитая"] if c in dead else []) + names
                                            + [f"нет файла: {n}" for n in missing]))
                t.setItem(r, c, it)
        self.rel = self.vparts[self.vrow].rel
        t.setCurrentCell(self.vrow, min(self.skin, len(gams[self.vrow].skins) - 1))
        self._loading = False

    def _fill_vform(self):
        f = self.form
        clear_layout(f)
        name = QLineEdit(self.vehicle)
        name.setReadOnly(True)
        f.addWidget(name)
        counts = {}
        kind = None
        for q in self.vparts:
            g = self.lib.doc(q.rel).gam
            if q.kind != kind:
                kind = q.kind
                f.addWidget(head({"CHASSIS": "Шасси", "CABIN": "Кабины", "BASKET": "Кузова", "WHEEL": "Колёса",
                                  "SUSP": "Подвеска"}[kind]))
            live = self.lib.live_count(q.rel, g)
            if len(g.skins) > 1:
                counts[q.rel] = len(g.skins)
            b = QPushButton(f"{q.model}  ·  {len(g.skins)}")
            b.setObjectName("link")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"Открыть деталь отдельно\n{', '.join(q.protos)}\nскинов: {live} обычных"
                         + (f" + {len(g.skins) - live} разбитых" if len(g.skins) > live else ""))
            b.setStyleSheet("text-align: left;")
            b.clicked.connect(lambda _=False, q=q: self._open_part(q))
            f.addWidget(b)
        from . import skinned
        old = sum(len(skinned.old_materials(self.lib.doc(q.rel).gam)) for q in self.vparts)
        again = 0 if old else sum(len(skinned.retunable(self.lib, q.rel, self.lib.doc(q.rel).gam)) for q in self.vparts)
        want = len(skinned.PAINTS) + 1
        gams = [self.lib.doc(q.rel).gam for q in self.vparts]
        grow = any(skinned.has_skinned(g) for g in gams) and any(1 < len(g.skins) < want for g in gams)
        if old or grow or again:
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet("background: #505255; border: none;")
            f.addSpacing(4)
            f.addWidget(line)
            f.addSpacing(4)
        if old or again:
            wz = QPushButton(f"Мастер конверсии в Skinned ({old})" if old else "Настроить наборы Skinned заново")
            wz.setToolTip("Материалы на старом шейдере bumpdiffuse_envalphagloss_spec: сделать маску покраски\n"
                          "и набор текстур под Skinned. Уже переведённые наборы можно настроить ещё раз.")
            wz.clicked.connect(self._wizard_open)
            f.addWidget(wz)
        if grow:
            fs = QPushButton(f"Расширить до {want} скинов")
            fs.setToolTip("Материалы на Skinned получают по скину на каждую краску (color1…5, camo1…4, color6…12)\n"
                          "и разбитый в конце; у остальных материалов скины дублируются до нужного числа")
            fs.clicked.connect(self._full_skins)
            f.addWidget(fs)
        if len(set(counts.values())) > 1:
            f.addWidget(dim(f"Число скинов разное: от {min(counts.values())} до {max(counts.values())}."))
            eq = QPushButton(f"Выровнять до {max(counts.values())}")
            eq.setObjectName("accent")
            eq.setToolTip("Недостающие скины добавляются перед разбитым и продолжают нумерацию текстур детали;\n"
                          "общая краска берётся у детали, где скинов больше всего")
            eq.clicked.connect(self._equalize)
            f.addWidget(eq)
        if self.vnote:
            note = dim(self.vnote)
            note.setWordWrap(True)
            f.addWidget(note)
        f.addStretch(1)

    # --- conversion to Skinned ---
    def _wizard_open(self):
        from .skinned_ui import SkinnedWizard
        if self.lp_mode:
            self.set_lp_mode(False)
        w = SkinnedWizard(self)
        if not w.collect(self.vparts):
            return
        self.wizard = w
        self.tables.addWidget(w.table)
        self.tables.setCurrentWidget(w.table)
        for b in self.mode_btns:
            b.setVisible(False)
        w.fill_table()
        self._vehicle_view()
        w.start()
        self._fill_form()

    def _wizard_close(self):
        w, self.wizard = self.wizard, None
        if w is None:
            return
        self.tables.removeWidget(w.table)
        w.table.deleteLater()
        self.tables.setCurrentIndex(0)
        for b in self.mode_btns:
            b.setVisible(True)
        self.skin = 0
        self._refresh()

    def _full_skins(self):
        from . import skinned
        added = sum(skinned.full_skins(self.lib, q.rel) for q in self.vparts)
        self.vnote = f"Добавлено скинов: {added}."
        self._refresh()

    def _wizard_apply(self):
        from . import skinned
        w = self.wizard
        if not any(j.checked for j in w.jobs):
            return
        # a Skinned vehicle has a skin per paint plus the wrecked one: every multi-skin part gets as many
        want = len(skinned.PAINTS) + 1
        added = 0
        if any(j.checked and not j.retune for j in w.jobs):
            for q in self.vparts:
                if 1 < len(self.lib.doc(q.rel).gam.skins) < want:
                    added += self.lib.extend_skins(q.rel, want)[0]
        try:
            done, problems = skinned.convert(self.lib, w.jobs, w.source, w.targets, w.occlusion)
        except OSError as e:
            return warn(self, f"Не удалось записать текстуры.\n{e}")
        self.view.reload_textures()
        self.vnote = (f"Записано наборов текстур Skinned: {done}."
                      + (f" Скинов у деталей теперь {want}: добавлено {added}." if added else "")
                      + ("\n" + "\n".join(problems) if problems else ""))
        self._wizard_close()

    # --- load points ---
    def set_lp_mode(self, on: bool):
        self.lp_mode = on
        for i, b in enumerate(self.mode_btns):
            b.setChecked(bool(i) == on)
        self.tables.setCurrentIndex(int(on))
        self.add_skin.setVisible(not on and not self.vehicle)
        self.del_skin.setVisible(not on and not self.vehicle)
        self._show_lp()
        if self.doc is not None:
            self._fill_form()

    def _lp_offset(self) -> tuple:
        return self._offsets.get(self.rel, (0.0, 0.0, 0.0)) if self.vehicle else (0.0, 0.0, 0.0)

    def _show_lp(self):
        """Fill the load point table and the markers in the view (or clear them outside the mode)."""
        if not self.lp_mode or self.doc is None:
            self.view.set_points([])
            return
        g = self.doc.gam
        ids = g.load_points()
        self.lp = min(self.lp, len(ids) - 1)
        t = self.lp_table
        self._loading = True
        t.setRowCount(len(ids))
        for r, i in enumerate(ids):
            h = QTableWidgetItem(g.nodes[i].name)
            parent = g.nodes[i].parent
            h.setToolTip(f"узел {i}" + (f", родитель {g.nodes[parent].name}" if 0 <= parent < len(g.nodes) else ""))
            t.setVerticalHeaderItem(r, h)
            for c, v in enumerate(self._lp_values(i)):
                t.setItem(r, c, QTableWidgetItem(v))
        if self.lp >= 0:
            t.setCurrentCell(self.lp, max(t.currentColumn(), 0))
        self._loading = False
        self._lp_markers()

    def _lp_markers(self):
        g, off = self.doc.gam, self._lp_offset()
        ids = g.load_points()
        self.view.set_points([(g.nodes[i].name, tuple(p + o for p, o in zip(g.node_pos(i), off)), g.node_axes(i))
                              for i in ids], self.lp, g.limits.get(ids[self.lp]) if 0 <= self.lp < len(ids) else None)

    def _lp_pick(self, row: int):
        if self._loading or not self.lp_mode or self.doc is None or row < 0 or row == self.lp:
            return
        self.lp = row
        self._loading = True
        self.lp_table.setCurrentCell(row, max(self.lp_table.currentColumn(), 0))
        self.lp_table.scrollToItem(self.lp_table.item(row, 0))
        self._loading = False
        self._lp_markers()
        self._fill_form()

    def _lp_values(self, i: int) -> list[str]:
        """Position and angles of a node as the six texts of its row."""
        g = self.doc.gam
        return [f"{v:.3f}" for v in g.node_pos(i)] + [f"{v + 0.0:.1f}".replace("-0.0", "0.0") for v in g.node_euler(i)]

    def _lp_show(self, row: int):
        """Refresh the row and the fields of a load point after it moved or turned."""
        vals = self._lp_values(self.doc.gam.load_points()[row])
        self._loading = True
        for c, v in enumerate(vals):
            self.lp_table.item(row, c).setText(v)
        self._loading = False
        if row == self.lp:
            for e, v in zip(getattr(self, "_lp_fields", []), vals):
                e.setText(v)

    def _lp_set(self, row: int, pos, done: bool = True):
        """Move a load point (model space). `done` closes the edit: undo step, parts re-seated."""
        g = self.doc.gam
        ids = g.load_points()
        if not 0 <= row < len(ids):
            return
        g.move_node(ids[row], pos)
        self._lp_show(row)
        if done:
            self._lp_dropped()

    def _lp_turn(self, row: int, deg):
        """Set the angles of a load point."""
        g = self.doc.gam
        ids = g.load_points()
        if 0 <= row < len(ids):
            g.set_node_euler(ids[row], deg)
            self._lp_show(row)
            self._lp_dropped()

    def _lp_rotated(self, row: int, axis: int, deg: float):
        g = self.doc.gam
        ids = g.load_points()
        if 0 <= row < len(ids):
            g.rotate_node(ids[row], axis, deg)
            self._lp_show(row)
            self._lp_markers()

    def _lp_dragged(self, row: int, x: float, y: float, z: float):
        off = self._lp_offset()
        self._lp_set(row, (x - off[0], y - off[1], z - off[2]), done=False)

    def _lp_dropped(self):
        self.doc.touch()
        if self.vehicle:
            self._vehicle_view()        # cabin, basket and wheels follow their points
        self._lp_markers()

    def _lp_cell(self, item):
        if self._loading or self.doc is None:
            return
        g = self.doc.gam
        ids = g.load_points()
        row, c = item.row(), item.column()
        vals = list(g.node_pos(ids[row])) + list(g.node_euler(ids[row]))
        try:
            vals[c] = float(item.text().replace(",", "."))
        except ValueError:
            return self._lp_show(row)
        self._lp_set(row, vals[:3]) if c < 3 else self._lp_turn(row, vals[3:])

    def _fill_lpform(self):
        f = self.form
        clear_layout(f)
        self._lp_fields = []
        g = self.doc.gam
        name = QLineEdit(self.rel)
        name.setReadOnly(True)
        f.addWidget(name)
        ids = g.load_points()
        if not ids:
            f.addWidget(dim("В модели нет точек LP_*."))
        elif 0 <= self.lp < len(ids):
            i = ids[self.lp]
            f.addWidget(head(g.nodes[i].name))
            vals = self._lp_values(i)
            for c, axis in enumerate(("X", "Y", "Z", "RX°", "RY°", "RZ°")):
                row = QHBoxLayout()
                row.setSpacing(4)
                lab = dim(axis)
                lab.setFixedWidth(30)
                row.addWidget(lab)
                e = QLineEdit(vals[c])
                e.editingFinished.connect(lambda c=c, e=e: self._lp_field(c, e.text()))
                row.addWidget(e, 1)
                f.addLayout(row)
                self._lp_fields.append(e)
            self._form_limits(f, g, i)
        f.addStretch(1)

    def _form_limits(self, f, g: G.Gam, i: int):
        """Angle limits of the node (how far a gun mounted here may turn), in degrees."""
        hr = QHBoxLayout()
        hr.addWidget(head("Ограничение углов"))
        hr.addStretch(1)
        lim = g.limits.get(i)
        if lim is None:
            add = plus_button("Добавить ограничение углов этой точке")
            add.clicked.connect(lambda: self._limit_toggle(i, True))
            hr.addWidget(add)
            f.addLayout(hr)
            return
        rm = minus_button("Убрать ограничение")
        rm.clicked.connect(lambda: self._limit_toggle(i, False))
        hr.addWidget(rm)
        f.addLayout(hr)
        for base, title in ((0, "min°"), (3, "max°")):
            row = QHBoxLayout()
            row.setSpacing(4)
            lab = dim(title)
            lab.setFixedWidth(30)
            row.addWidget(lab)
            for k in range(3):
                e = QLineEdit(f"{math.degrees(lim[base + k]):.1f}")
                e.setToolTip("XYZ"[k])
                e.editingFinished.connect(lambda i=i, c=base + k, e=e: self._limit_set(i, c, e.text()))
                row.addWidget(e, 1)
            f.addLayout(row)

    def _limit_toggle(self, i: int, on: bool):
        g = self.doc.gam
        if on:
            a = math.radians(45.0)
            g.limits[i] = [-a, -a, -a, a, a, a]
        else:
            g.limits.pop(i, None)
        self.doc.touch()
        self._lp_markers()
        QTimer.singleShot(0, self._fill_form)

    def _limit_set(self, i: int, c: int, text: str):
        lim = self.doc.gam.limits.get(i)
        try:
            v = math.radians(float(text.replace(",", ".")))
        except ValueError:
            return
        if lim is not None and abs(v - lim[c]) > 1e-4:
            lim[c] = v
            self.doc.touch()
            self._lp_markers()

    def _lp_field(self, c: int, text: str):
        g = self.doc.gam
        ids = g.load_points()
        if not 0 <= self.lp < len(ids):
            return
        vals = list(g.node_pos(ids[self.lp])) + list(g.node_euler(ids[self.lp]))
        try:
            v = float(text.replace(",", "."))
        except ValueError:
            return self._lp_show(self.lp)
        if abs(v - vals[c]) > (1e-4 if c < 3 else 0.05):
            vals[c] = v
            self._lp_set(self.lp, vals[:3]) if c < 3 else self._lp_turn(self.lp, vals[3:])

    def _open_part(self, q):
        self.open(q.model, self.skin)
        self._fill_tree()

    def _equalize(self):
        """Give every part as many ordinary skins as the richest one has."""
        info = [(q, self.lib.doc(q.rel).gam) for q in self.vparts]
        multi = [(q, g) for q, g in info if len(g.skins) > 1]
        if not multi:
            return
        target = max(len(g.skins) for _q, g in multi)
        ref = max(multi, key=lambda x: len(x[1].skins))
        added = guessed = 0
        for q, g in multi:
            a, m = self.lib.extend_skins(q.rel, target, (ref[0].rel, ref[1]) if q is not ref[0] else None)
            added, guessed = added + a, guessed + m
        self.vnote = f"Добавлено скинов: {added}." + (
            f" Для {guessed} текстур не нашлось файла с нужным номером — оставлена копия последнего скина, "
            "проверьте красные и повторяющиеся ячейки." if guessed else "")
        self._refresh()

    def reload(self):
        """After undo/redo or saving: skins may have changed under us."""
        if self.doc is None:
            return
        self.skin = min(self.skin, len(self.doc.gam.skins) - 1)
        self.lib.refresh()
        self._refresh()

    def _refresh(self):
        if self.vehicle:
            self.view.reload_textures()
            self._vehicle_view()
            self._fill_table()
            self._fill_form()
            self._show_lp()
            return
        self.view.reload_textures()
        self.view.show_state(self.skin, self.doc.gam.visible(self.choice))
        self._fill_table()
        self._fill_form()
        self._show_lp()

    def _changed(self):
        self.doc.touch()
        self._refresh()

    # --- skin table ---
    def _thumb(self, name: str) -> QPixmap | None:
        path = self.lib.find_texture(self.rel, name)
        if path is None:
            return None
        key = str(path)
        if key not in self._thumbs:
            im = load_image(path)
            if im is None:
                return None
            im = im.convert("RGB").resize((THUMB, THUMB))
            self._thumbs[key] = QPixmap.fromImage(QImage(im.tobytes(), THUMB, THUMB, THUMB * 3, QImage.Format_RGB888).copy())
        return self._thumbs[key]

    def _fill_table(self):
        if self.vehicle:
            return self._fill_vtable()
        g = self.doc.gam
        dead = self.lib.action_skins(self.rel)
        t = self.table
        self._loading = True
        t.clear()
        t.setRowCount(g.n_mats)
        t.setColumnCount(len(g.skins))
        for c in range(len(g.skins)):
            h = QTableWidgetItem(f"{c} разб." if c in dead else str(c))
            if c in dead:
                h.setToolTip("Скин разбитой модели: на него ссылается action в animmodels.xml")
            t.setHorizontalHeaderItem(c, h)
        for r in range(g.n_mats):
            first = g.skins[0][r]
            t.setVerticalHeaderItem(r, QTableWidgetItem(Path(first.tex(G.TEX_DIFFUSE)).stem or first.shader or str(r)))
            for c, skin in enumerate(g.skins):
                m = skin[r]
                it = QTableWidgetItem()
                names = [x.name for x in m.textures]
                missing = [n for n in names if self.lib.find_texture(self.rel, n) is None]
                pm = self._thumb(m.tex(G.TEX_DIFFUSE))
                if pm is not None:
                    it.setIcon(QIcon(pm))
                elif names:
                    it.setText("?")
                    it.setTextAlignment(Qt.AlignCenter)
                if missing:
                    it.setBackground(QBrush(QColor("#a8483c")))
                tip = [m.shader] + [f"{G.TEX_TITLES.get(x.type, x.type)}: {x.name}" for x in m.textures]
                it.setToolTip("\n".join(tip + [f"нет файла: {n}" for n in missing]))
                t.setItem(r, c, it)
        if g.n_mats and g.skins:
            t.setCurrentCell(min(self.mat, g.n_mats - 1), self.skin)
        self._loading = False
        self.del_skin.setEnabled(len(g.skins) > 1)

    def _cell(self, row, col, _pr, _pc):
        if self._loading or self.doc is None or row < 0 or col < 0:
            return
        if self.vehicle:
            q = self.vparts[row]
            self.vrow, self.skin = row, col
            self.rel, self.model_id, self.doc = q.rel, q.model, self.lib.doc(q.rel)
            if q.kind in ("CABIN", "BASKET") and self.vshown.get(q.kind) != q.rel:
                self.vshown[q.kind] = q.rel
                if q.kind in self.vchoices:
                    self.vchoices[q.kind].set_value(q.rel)
                    self._vbar_fit()
                self._vehicle_view()
            else:
                self.view.show_state(self.skin)
            self.lp = -1
            return
        self.mat, self.skin = row, col
        self.view.show_state(self.skin, self.doc.gam.visible(self.choice))
        self._fill_form()

    def _table_menu(self, pos):
        if self.doc is None:
            return
        if self.vehicle:
            m = QMenu(self)
            q = self.vparts[self.vrow]
            m.addAction(f"Открыть {q.model} отдельно", lambda: self._open_part(q))
            m.exec(self.table.viewport().mapToGlobal(pos))
            return
        m = QMenu(self)
        m.addAction("Добавить скин (копия этого)", self._add_skin)
        a = m.addAction("Удалить скин", self._del_skin)
        a.setEnabled(len(self.doc.gam.skins) > 1)
        m.addSeparator()
        m.addAction("Материал: заполнить строку по шаблону…", self._fill_pattern)
        m.addAction("Материал: скопировать во все скины", self._copy_row)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _add_skin(self):
        if self.doc is None:
            return
        g = self.doc.gam
        at = self.skin + 1
        g.skins.insert(at, [m.clone() for m in g.skins[self.skin]])
        self.lib.shift_action_skins(self.rel, at, +1)
        self.skin = at
        self._changed()

    def _del_skin(self):
        if self.doc is None or len(self.doc.gam.skins) < 2:
            return
        if self.skin in self.lib.action_skins(self.rel):
            return warn(self, "Этот скин показывается у разбитой модели (action skin в animmodels.xml). "
                              "Удалять его нельзя.")
        del self.doc.gam.skins[self.skin]
        self.lib.shift_action_skins(self.rel, self.skin, -1)
        self.skin = min(self.skin, len(self.doc.gam.skins) - 1)
        self._changed()

    def _fill_pattern(self):
        """Diffuse texture of this material in every skin from a pattern with {n} = skin number."""
        g = self.doc.gam
        cur = g.skins[self.skin][self.mat].tex(G.TEX_DIFFUSE)
        guess = re.sub(r"\d+(\.\w+)$", r"{n}\1", cur) if cur else "name_{n}.dds"
        pat = ask_text(self, "Шаблон", "Имя текстуры, {n} — номер скина", guess)
        if not pat:
            return
        dead = self.lib.action_skins(self.rel)
        for n, skin in enumerate(g.skins):
            name = pat.replace("{n}", str(n))
            if n in dead or not self._name_ok(name):
                continue
            tex = next((x for x in skin[self.mat].textures if x.type == G.TEX_DIFFUSE), None)
            if tex is None:
                skin[self.mat].textures.insert(0, G.Texture(name, 0, G.TEX_DIFFUSE))
            else:
                tex.name = name
        self._changed()

    def _copy_row(self):
        g = self.doc.gam
        src = g.skins[self.skin][self.mat]
        dead = self.lib.action_skins(self.rel)
        for n, skin in enumerate(g.skins):
            if n != self.skin and n not in dead:
                skin[self.mat] = src.clone()
        self._changed()

    def _name_ok(self, name: str) -> bool:
        try:
            ok = len(name.encode("cp1251")) <= G.NAME_MAX
        except UnicodeEncodeError:
            ok = False
        if not ok:
            warn(self, f"{name}: имя текстуры длиннее {G.NAME_MAX} символов или не в windows-1251.")
        return ok

    # --- inspector ---
    def _fill_form(self):
        if self.wizard is not None:
            return self.wizard.fill_form()
        if self.lp_mode and self.doc is not None:
            return self._fill_lpform()
        if self.vehicle:
            return self._fill_vform()
        f = self.form
        clear_layout(f)
        if self.doc is None:
            return
        g = self.doc.gam
        name = QLineEdit(self.rel)
        name.setReadOnly(True)
        name.setToolTip(", ".join(self.lib.ids_of(self.rel)))
        f.addWidget(name)

        if g.n_mats and g.skins:
            m = g.skins[self.skin][min(self.mat, g.n_mats - 1)]
            f.addWidget(head(f"Материал {self.mat}, скин {self.skin}"))
            sh = Choice([(s, s) for s in self.lib.shaders()], m.shader)
            sh.picked.connect(lambda v, m=m: self._set_shader(m, v))
            f.addWidget(sh)
            names = _Names(lambda: self.lib.texture_choices(self.rel))
            for i, tex in enumerate(m.textures):
                row = QHBoxLayout()
                row.setSpacing(3)
                kind = Choice([(str(k), v) for k, v in G.TEX_TITLES.items()], str(tex.type))
                kind.picked.connect(lambda v, tex=tex: self._set_type(tex, v))
                box = NameBox(names, "tex", tex.name)
                if self.lib.find_texture(self.rel, tex.name) is None:
                    box.setStyleSheet("color: #8c1c10;")
                    box.setToolTip("Файл текстуры не найден")
                box.committed.connect(lambda v, tex=tex, box=box: self._set_tex(tex, v, box))
                rm = minus_button()
                rm.clicked.connect(lambda _=False, m=m, i=i: self._del_tex(m, i))
                row.addWidget(kind)
                row.addWidget(box, 1)
                row.addWidget(rm)
                f.addLayout(row)
            row = QHBoxLayout()
            add = plus_button("Добавить текстуру")
            add.clicked.connect(lambda _=False, m=m: self._add_tex(m))
            row.addWidget(add)
            row.addStretch(1)
            f.addLayout(row)

        self._form_look(f, g)
        f.addStretch(1)

    def _form_look(self, f, g: G.Gam):
        groups = [(i, gr) for i, gr in enumerate(g.groups) if len(gr.variants) > 1]
        if groups:
            f.addWidget(head("Вид"))
        if groups and all(re.fullmatch(r"Breakable\d+", gr.name) for _i, gr in groups):
            n = max(len(gr.variants) for _i, gr in groups)
            same = len({self.choice[i] for i, _gr in groups}) == 1
            titles = [(str(k), STATES[k] if n == 3 else f"состояние {k}") for k in range(n)]
            c = Choice(titles + ([] if same else [("-1", "смешанное")]), str(self.choice[groups[0][0]]) if same else "-1")
            c.picked.connect(lambda v: self._set_state(int(v)))
            f.addWidget(c)
        else:
            mats = g.skins[self.skin] if g.skins else []
            for i, gr in groups:
                opts = []
                for k, var in enumerate(gr.variants):
                    stems = [Path(mats[g.meshes[n].material].tex(G.TEX_DIFFUSE)).stem for n in var
                             if n < len(g.meshes) and 0 <= g.meshes[n].material < len(mats)]
                    opts.append((str(k), ", ".join(dict.fromkeys(s for s in stems if s)) or ("нет" if not var else str(k))))
                row = QHBoxLayout()
                row.setSpacing(4)
                row.addWidget(dim(GROUP_TITLES.get(gr.name.lower(), gr.name)))
                c = Choice(opts, str(self.choice[i]))
                c.picked.connect(lambda v, i=i: self._set_variant(i, int(v)))
                row.addWidget(c, 1)
                f.addLayout(row)
        if groups or len(g.skins) > 1 or self._pick:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(dim("скин"))
            sk = QLineEdit(str(self.skin))
            sk.setFixedWidth(40)
            sk.editingFinished.connect(lambda: self._type_skin(sk.text()))
            row.addWidget(sk)
            row.addWidget(dim("cfg"))
            cfg = QLineEdit(str(g.choice_to_config(self.choice)))
            cfg.setFixedWidth(64)
            cfg.setToolTip(f"Номер конфигурации, всего {g.config_count()}")
            cfg.editingFinished.connect(lambda: self._type_cfg(cfg.text()))
            row.addWidget(cfg)
            row.addStretch(1)
            f.addLayout(row)
        if self._pick:
            b = QPushButton(f"Записать: {self._pick[0]}")
            b.setObjectName("accent")
            b.clicked.connect(self._apply_pick)
            f.addWidget(b)

    def _look_changed(self, refit: bool = False):
        self.view.show_state(self.skin, self.doc.gam.visible(self.choice), refit)
        QTimer.singleShot(0, self._fill_form)

    def _set_variant(self, i: int, v: int):
        self.choice[i] = v
        self._look_changed()

    def _set_state(self, v: int):
        if v < 0:
            return
        for i, gr in enumerate(self.doc.gam.groups):
            if len(gr.variants) > 1:
                self.choice[i] = min(v, len(gr.variants) - 1)
        self._look_changed()

    def _type_cfg(self, text: str):
        g = self.doc.gam
        if text.strip().isdigit() and int(text) != g.choice_to_config(self.choice):
            self.choice = g.config_to_choice(min(int(text), g.config_count() - 1))
            self._look_changed()

    def _type_skin(self, text: str):
        if text.strip().isdigit() and int(text) != self.skin:
            self.skin = min(int(text), len(self.doc.gam.skins) - 1)
            self.table.setCurrentCell(self.mat, self.skin)

    def _apply_pick(self):
        title, done = self._pick
        self._pick = None
        done(str(self.skin), str(self.doc.gam.choice_to_config(self.choice)))

    # --- material edits (deferred: the editor that fired the signal is rebuilt) ---
    def _later(self):
        QTimer.singleShot(0, self._changed)

    def _set_shader(self, m, v: str):
        if v and v != m.shader:
            m.shader = v
            self._later()

    def _set_type(self, tex, v: str):
        if int(v) != tex.type:
            tex.type = int(v)
            self._later()

    def _set_tex(self, tex, v: str, box):
        if not v or not self._name_ok(v):
            return box.set_value(tex.name)
        tex.name = v
        self._later()

    def _del_tex(self, m, i: int):
        del m.textures[i]
        self._later()

    def _add_tex(self, m):
        used = {t.type for t in m.textures}
        kind = next((k for k in (G.TEX_DIFFUSE, G.TEX_BUMP, G.TEX_CUBE) if k not in used), 2)
        m.textures.append(G.Texture("lobbycube.dds" if kind == G.TEX_CUBE else "", 0, kind))
        self._later()

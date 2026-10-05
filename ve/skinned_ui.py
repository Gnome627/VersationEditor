"""Wizard of the Models tab: convert old vehicle materials to Skinned with a live preview."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSlider,
                               QTableWidget, QTableWidgetItem)

from . import gam as G
from . import skinned as S
from .widgets import Choice, clear_layout, head

PREVIEW = 512           # longest side of the textures made for the live view


def dim(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("dim")
    return l


class SkinnedWizard:
    def __init__(self, tab):
        self.tab, self.lib = tab, tab.lib
        self.jobs: list[S.Job] = []
        self.targets: dict[int, list[tuple[str, str, int]]] = {}    # job index -> (file, model, material)
        self.cur = 0
        self.show = "result"            # result | mask | source
        self.paint = S.PAINTS[0]
        self.source = S.Source(self.lib)
        self.ready: set[int] = set()    # jobs whose preview textures are in the view
        self.auto: set[int] = set()     # jobs whose green skin has been looked for
        self.ao: dict[int, object] = {}  # baked occlusion per job (None: no OpenGL), made once
        self._loading = False
        self.table = QTableWidget()
        self.table.setColumnCount(1)
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._pick(r))
        self.table.itemChanged.connect(self._checked)
        self._queue: list[int] = []
        self._tick = QTimer(tab, singleShot=True, interval=0)
        self._tick.timeout.connect(self._work)
        self._later = QTimer(tab, singleShot=True, interval=60)     # a dragged slider rebuilds once it rests
        self._later.timeout.connect(lambda: self._enqueue(self.cur, first=True))

    # --- jobs ---
    def collect(self, parts) -> int:
        """One job per texture set with the old shader; models that share the set go together."""
        self.jobs, self.targets, by_key = [], {}, {}
        for q in parts:
            g = self.lib.doc(q.rel).gam
            for mi in S.old_materials(g):
                key = (q.rel.rsplit("/", 1)[0], g.skins[0][mi].tex(G.TEX_DIFFUSE).lower(),
                       g.skins[0][mi].tex(G.TEX_BUMP).lower())
                if key not in by_key:
                    by_key[key] = len(self.jobs)
                    job = S.Job(q.rel, q.model, mi, S.base_name(g, mi),
                                checked=S.paintable(g, mi, self.lib.dead_skins(q.rel, g)),
                                choices=S.old_choices(self.lib, q.rel, g, mi), bump=g.skins[0][mi].tex(G.TEX_BUMP))
                    S.load_settings(self.lib, job)      # a set converted before for another model
                    self.jobs.append(job)
                    self.targets[by_key[key]] = []
                self.targets[by_key[key]].append((q.rel, q.model, mi))
            # sets converted earlier can be tuned again while their old textures are still there
            for mi, stem, names, bump in S.retunable(self.lib, q.rel, g):
                key = (q.rel.rsplit("/", 1)[0], "skinned", stem.lower())
                if key not in by_key:
                    by_key[key] = len(self.jobs)
                    job = S.Job(q.rel, q.model, mi, stem, checked=False, retune=True, choices=names, bump=bump)
                    S.load_settings(self.lib, job)
                    self.jobs.append(job)
                    self.targets[by_key[key]] = []
                self.targets[by_key[key]].append((q.rel, q.model, mi))
        return len(self.jobs)

    def key(self, i: int) -> str:
        j = self.jobs[i]
        return f"{j.rel.rsplit('/', 1)[0]}/{j.title}#{i}"

    def fill_table(self):
        self._loading = True
        t = self.table
        t.setRowCount(len(self.jobs))
        for r, j in enumerate(self.jobs):
            models = list(dict.fromkeys(m for _rel, m, _mi in self.targets[r]))
            it = QTableWidgetItem(f"{j.title}  ·  {', '.join(models)}" + ("  ·  уже Skinned" if j.retune else ""))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if j.checked else Qt.Unchecked)
            it.setToolTip("Отмеченные наборы текстур будут переведены на Skinned")
            t.setItem(r, 0, it)
        if self.jobs:
            t.setCurrentCell(min(self.cur, len(self.jobs) - 1), 0)
        self._loading = False

    def _checked(self, item):
        if self._loading:
            return
        self.jobs[item.row()].checked = item.checkState() == Qt.Checked
        if self.jobs[item.row()].checked:
            self._enqueue(item.row())
        self.tab.view.update()

    def _pick(self, row: int):
        if self._loading or not 0 <= row < len(self.jobs):
            return
        self.cur = row
        self._prepare(row)
        self._enqueue(row, first=True)
        self.tab._fill_form()

    def _prepare(self, i: int):
        """First visit: guess which skin carries the green paint."""
        if i in self.auto:
            return
        self.auto.add(i)
        j = self.jobs[i]
        if j.green not in j.choices:
            j.green = S.greenest(self.lib, j.rel, j.choices)
        if j.base not in j.choices:
            j.base = j.green

    def occlusion(self, i: int):
        """Ambient occlusion of a job's texels, baked from the geometry of every model that uses the set."""
        if i not in self.ao:
            targets = []
            for rel, _model, mat in self.targets[i]:
                g = self.lib.doc(rel).gam
                targets.append((g, mat, g.visible(g.config_to_choice(0))))
            self.ao[i] = self.tab.view.bake_ao(targets)
        return self.ao[i]

    # --- live preview ---
    def start(self):
        self.ready.clear()
        self._set_paint(self.paint)
        self._queue = [i for i, j in enumerate(self.jobs) if j.checked]
        if self.jobs:
            self._enqueue(self.cur, first=True)

    def _enqueue(self, i: int, first: bool = False):
        if i in self._queue:
            self._queue.remove(i)
        self._queue.insert(0, i) if first else self._queue.append(i)
        self._tick.start()

    def _work(self):
        """Build the preview textures of one job per tick, so the window stays responsive."""
        if not self._queue:
            return
        i = self._queue.pop(0)
        if 0 <= i < len(self.jobs):
            j = self.jobs[i]
            self._prepare(i)
            built = self.source.build(j, PREVIEW, self.occlusion(i))
            if built is not None:
                mask, detail, lightmap = built
                k = self.key(i)
                self.tab.view.set_memory("@m:" + k, mask.convert("RGB"))
                self.tab.view.set_memory("@d:" + k, detail)
                self.tab.view.set_memory("@l:" + k, lightmap)
                self.tab.view.set_memory("@a:" + k, lightmap.getchannel("B").convert("RGB"))
                self.ready.add(i)
        if self._queue:
            self._tick.start()

    def mats(self, rel: str):
        """Materials the view shows for a model while the wizard is open (None: as in the file)."""
        mine = [(i, mi) for i in range(len(self.jobs)) for r, _m, mi in self.targets[i] if r == rel]
        g = self.lib.doc(rel).gam
        live = len(g.skins) - len(self.lib.dead_skins(rel, g))

        def fn(skin: int):
            # the skin of the chosen paint; a model that has fewer skins yet shows its first one
            out = list(g.skins[skin if skin < live else 0])
            for i, mi in mine:
                j = self.jobs[i]
                mine_now = S.is_old(out[mi].shader) or (j.retune and out[mi].shader.lower() == S.NEW_SHADER.lower())
                if i not in self.ready or not mine_now or self.show == "source":
                    continue
                k = self.key(i)
                if self.show in ("mask", "ao") and i == self.cur:
                    name = ("@m:" if self.show == "mask" else "@a:") + k
                    out[mi] = G.Material(shader="diffuse", textures=[G.Texture(name, 0, G.TEX_DIFFUSE)])
                elif j.checked or i == self.cur:
                    out[mi] = S.new_material(out[mi], self.paint, "@d:" + k, "@l:" + k)
            return out
        return fn

    # --- right panel ---
    def _slider(self, f, title: str, obj, attr: str, lo: float, hi: float, step: float):
        row = QHBoxLayout()
        row.setSpacing(4)
        lab = dim(title)
        lab.setFixedWidth(84)
        row.addWidget(lab)
        s = QSlider(Qt.Horizontal)
        s.setRange(int(round(lo / step)), int(round(hi / step)))
        s.setValue(int(round(getattr(obj, attr) / step)))
        val = dim(f"{getattr(obj, attr):.2f}")
        val.setFixedWidth(34)

        def changed(v):
            setattr(obj, attr, v * step)
            val.setText(f"{v * step:.2f}")
            self._later.start()
        s.valueChanged.connect(changed)
        row.addWidget(s, 1)
        row.addWidget(val)
        f.addLayout(row)

    def fill_form(self):
        f = self.tab.form
        clear_layout(f)
        f.addWidget(head("Конверсия в Skinned"))
        if not self.jobs:
            f.addWidget(dim("Материалов со старым шейдером нет."))
            f.addStretch(1)
            return
        j = self.jobs[self.cur]
        self._prepare(self.cur)
        skins = [(n, n) for n in j.choices]

        f.addWidget(head("Маска покраски"))
        src = Choice(skins + [("file", "файл маски: " + Path(j.mask_file).name if j.mask_file else "файл маски…")],
                     "file" if j.mask_file else j.green)
        src.setToolTip("Скин с зелёной покраской (обычно _4) или готовая маска камуфляжа, например из M113")
        src.picked.connect(self._mask_source)
        f.addWidget(src)
        if not j.mask_file:
            self._slider(f, "оттенок от", j.mask, "hue_lo", 0.0, 1.0, 0.01)
            self._slider(f, "оттенок до", j.mask, "hue_hi", 0.0, 1.0, 0.01)
            self._slider(f, "насыщенность", j.mask, "sat_pow", 0.2, 3.0, 0.05)
            self._slider(f, "усиление", j.mask, "gain", 0.5, 8.0, 0.1)
            self._slider(f, "размытие", j.mask, "blur", 0.0, 4.0, 0.1)

        f.addWidget(head("Текстуры Skinned"))
        base = Choice(skins, j.base)
        base.setToolTip("Старая текстура, из которой берётся рисунок деталей")
        base.picked.connect(lambda v: self._set(j, "base", v))
        f.addWidget(base)
        self._slider(f, "серый", j.set, "gray_pow", 0.4, 3.0, 0.05)
        self._slider(f, "Roughness", j.set, "rough", 0.0, 10.0, 0.05)
        self._slider(f, "Metallic", j.set, "metal", 0.0, 10.0, 0.05)
        self._slider(f, "AO", j.set, "ao", 0.0, 2.0, 0.05)

        f.addWidget(head("Просмотр"))
        row = QHBoxLayout()
        row.setSpacing(4)
        show = Choice([("result", "результат"), ("mask", "маска"), ("ao", "AO"), ("source", "как было")], self.show)
        show.picked.connect(self._set_show)
        row.addWidget(show)
        paint = Choice([(p, f"{n}: {p.rsplit('.', 1)[0]}") for n, p in enumerate(S.PAINTS)], self.paint)
        paint.setToolTip("Скин для просмотра и его краска. «Как было» показывает старый скин с тем же номером, "
                         "а если у детали столько скинов ещё нет — первый")
        paint.picked.connect(self._set_paint)
        row.addWidget(paint)
        row.addStretch(1)
        f.addLayout(row)

        n = sum(x.checked for x in self.jobs)
        if j.retune:
            f.addWidget(dim("Набор уже на Skinned: отметьте его в списке,\nчтобы перезаписать текстуры с новыми настройками."))
        go = QPushButton(f"Конвертировать отмеченные: {n}")
        go.setObjectName("accent")
        go.setToolTip("Записать *_detail.dds и *_lightmap.dds рядом с моделью и перевести материалы на Skinned")
        go.clicked.connect(self.tab._wizard_apply)
        f.addWidget(go)
        close = QPushButton("Закрыть мастер")
        close.clicked.connect(self.tab._wizard_close)
        f.addWidget(close)
        f.addStretch(1)

    def _set(self, j, attr: str, value):
        setattr(j, attr, value)
        self._later.start()

    def _mask_source(self, v: str):
        j = self.jobs[self.cur]
        if v == "file":
            path, _ = QFileDialog.getOpenFileName(self.tab, "Маска покраски", str((self.lib.game.root / j.rel).parent),
                                                  "Текстуры (*.dds *.tga *.png);;Все файлы (*)")
            if path:
                j.mask_file = path
        else:
            j.mask_file, j.green = "", v
        self._later.start()
        QTimer.singleShot(0, self.tab._fill_form)

    def _set_show(self, v: str):
        self.show = v
        self.tab.view.update()

    def _set_paint(self, v: str):
        self.paint = v
        self.tab.skin = S.PAINTS.index(v) if v in S.PAINTS else 0
        self.tab.view.show_state(self.tab.skin)

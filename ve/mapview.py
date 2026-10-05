"""Map view: level picture with towns and locations; places are dragged by mouse.

Also draws marks from the selected trigger's blocks and a camera/drive path,
and can capture the next click to pick a world point.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QLabel, QMenu

from . import theme
from .game import load_image


class PlaceItem(QGraphicsItem):
    def __init__(self, view: "MapView", el, kind: str, child: bool):
        super().__init__()
        self.view, self.el, self.kind, self.child = view, el, kind, child
        self.x = self.z = 0.0
        self.radius = 0.0
        self.hot = False       # highlighted: referenced by the selected trigger
        self.setZValue(3 if kind == "town" else 2)
        self.sync()

    def sync(self):
        m = self.view.map
        p = m.pos(self.el)
        self.prepareGeometryChange()
        if p:
            self.x, self.z = p[0], p[2]
        try:
            self.radius = float(self.el.get("Radius") or 0)
        except ValueError:
            self.radius = 0.0
        self.setPos(self.x, self.view.world - self.z)
        self.update()

    def name(self) -> str:
        return self.el.get("Name")

    def boundingRect(self) -> QRectF:
        px = self.view.px
        r = max(self.radius, 9 * px) + 3 * px
        return QRectF(-r, -r - 4 * px, max(2 * r, 260 * px), 2 * r + 22 * px)

    def paint(self, p: QPainter, opt, widget=None):
        px = self.view.px
        sel = self.view.current is self.el
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(theme.ORANGE) if (sel or self.hot) else QColor("#f1f1f2" if not self.child else "#c9cacc")
        pen = QPen(col, (2.2 if sel else 1.3) * px)
        if not sel and self.kind == "loc":
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        if self.kind == "loc":
            fill = QColor(theme.ORANGE)
            fill.setAlpha(70 if sel else (40 if self.hot else 0))
            p.setBrush(fill)
            r = max(self.radius, 2 * px)
            p.drawEllipse(QPointF(0, 0), r, r)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(0, 0), 2.2 * px, 2.2 * px)
            if sel:   # radius handle
                p.setPen(QPen(QColor("#3a3b3c"), px))
                p.drawEllipse(QPointF(r, 0), 4.5 * px, 4.5 * px)
        else:
            s = 7 * px
            p.setBrush(QColor(theme.ORANGE) if sel else QColor("#e6e6e8"))
            p.setPen(QPen(QColor("#2e2f30"), 1.4 * px))
            p.drawRect(QRectF(-s, -s, 2 * s, 2 * s))
        if sel or self.kind == "town" or self.hot or self.view.hover is self:
            label = self.view.map.full_name(self.name()) if self.kind == "town" else ""
            label = (label.capitalize() if label else self.name())
            p.save()
            off = 9 * px if self.kind == "town" else max(self.radius, 2 * px) + 3 * px
            p.translate(0, off)
            p.scale(px, px)
            p.setFont(self.view.label_font)
            p.setPen(QColor(0, 0, 0, 190))
            p.drawText(QPointF(-3, 13), label)
            p.setPen(QColor("#ffffff") if not (sel or self.hot) else QColor(theme.ORANGE))
            p.drawText(QPointF(-4, 12), label)
            p.restore()


class MapView(QGraphicsView):
    picked = Signal(object)                 # place element or None
    moved = Signal(object, float, float)    # element, x, z
    resized = Signal(object, float)         # element, radius
    create = Signal(str, float, float)      # "loc" | "town", x, z
    remove = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("canvas")
        self.sc = QGraphicsScene(self)
        # Item bounds depend on zoom, so the scene index goes stale and removing an item
        # after zooming crashed (access violation). With a few hundred items no index is needed.
        self.sc.setItemIndexMethod(QGraphicsScene.NoIndex)
        self.setScene(self.sc)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self.label_font = QFont("Tahoma", 9)
        self.map = None
        self.world = 4096.0
        self.px = 1.0
        self.current = None
        self.hover: PlaceItem | None = None
        self.items_by: dict[int, PlaceItem] = {}
        self._drag = None           # (item, "move"|"radius", start scene point, moved)
        self._pick_cb = None        # waiting for a click to return a world point
        self.marks: list[tuple[float, float, str]] = []   # points from the selected trigger's blocks
        self.path = None            # {"kind", "pts": [{x, z, lx, lz}], "move": fn(i, what, x, z)}
        self._pdrag = None          # (point index, "pt"|"look")
        self.note = QLabel(self)
        self.note.setObjectName("dim")
        self.note.setStyleSheet("background: #f2f2f2; padding: 3px 8px; border: 1px solid #8a6b47;")
        self.note.hide()

    def load(self, game, mapdata):
        self.map = mapdata
        self.world = game.world_size(mapdata.name)
        self.current = None
        self.hover = None
        self._drag = None
        self.marks, self.path, self._pdrag = [], None, None
        self.cancel_pick()
        self.sc.clear()
        self.items_by = {}
        self.sc.setSceneRect(-self.world * 0.25, -self.world * 0.25, self.world * 1.5, self.world * 1.5)
        path = game.map_image_path(mapdata.name)
        im = load_image(path) if path else None
        if im is not None:
            if max(im.size) > 2048:
                im = im.resize((2048, 2048))
            data = im.convert("RGBA").tobytes("raw", "RGBA")
            qim = QImage(data, im.width, im.height, QImage.Format_RGBA8888).copy()
            pm = self.sc.addPixmap(QPixmap.fromImage(qim))
            pm.setScale(self.world / im.width)
            pm.setZValue(0)
            self.setBackgroundBrush(QBrush(QColor("#3a3b3c")))
            self.note.hide()
        else:
            self.sc.addRect(0, 0, self.world, self.world, QPen(QColor("#b9bbbf"), 0), QBrush(QColor("#ffffff")))
            self.setBackgroundBrush(QBrush(QColor("#ffffff")))
            self.note.setText(f"У карты {mapdata.name} нет картинки")
            self.note.adjustSize()
            self.note.move(18, 18)
            self.note.show()
        self.rebuild()
        self.fit()

    def rebuild(self):
        self.hover = None
        self._drag = None
        for it in list(self.items_by.values()):
            self.sc.removeItem(it)
        self.items_by = {}
        m = self.map
        for t in m.towns_list():
            self._add(t, "town", False)
            for c in m.locations(t):
                self._add(c, "loc", True)
        for l in m.locations():
            self._add(l, "loc", False)

    def _add(self, el, kind, child):
        if self.map.pos(el) is None:
            return
        it = PlaceItem(self, el, kind, child)
        self.sc.addItem(it)
        self.items_by[id(el)] = it

    def fit(self):
        self.resetTransform()
        s = min(self.viewport().width(), self.viewport().height()) / self.world * 0.96
        s = max(s, 0.01)
        self.scale(s, s)
        self.centerOn(self.world / 2, self.world / 2)
        self._zoomed()

    def _zoomed(self):
        for it in self.items_by.values():
            it.prepareGeometryChange()          # before the zoom changes, while bounds are still old
        self.px = 1.0 / max(self.transform().m11(), 1e-6)
        self.viewport().update()

    # --- point picking, block marks, path ---
    def pick(self, callback):
        self._pick_cb = callback
        self.viewport().setCursor(Qt.CrossCursor)
        self.setFocus()

    def cancel_pick(self):
        self._pick_cb = None
        self.viewport().unsetCursor()

    def set_marks(self, marks):
        self.marks = list(marks)
        self.viewport().update()

    def set_path(self, kind=None, pts=None, move=None):
        self.path = {"kind": kind, "pts": pts, "move": move} if kind else None
        self._pdrag = None
        self.viewport().update()

    def frame_path(self):
        """Bring the path into view without touching the zoom: the map moves only when
        the path is outside the window."""
        if not self.path or not self.path["pts"]:
            return
        xs = [d[k] for d in self.path["pts"] for k in ("x", "lx") if k in d]
        ys = [self._sy(d[k]) for d in self.path["pts"] for k in ("z", "lz") if k in d]
        centre = QPointF((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
        if not self.mapToScene(self.viewport().rect()).boundingRect().contains(centre):
            self.centerOn(centre)

    def _sy(self, z: float) -> float:
        return self.world - z

    def drawForeground(self, p: QPainter, rect):
        px = self.px
        p.setRenderHint(QPainter.Antialiasing)
        orange, dark = QColor(theme.ORANGE), QColor("#2e2f30")

        def label(x, y, text, col=QColor("#ffffff")):
            p.save()
            p.translate(x, y)
            p.scale(px, px)
            p.setFont(self.label_font)
            p.setPen(QColor(0, 0, 0, 190))
            p.drawText(QPointF(9, 5), text)
            p.setPen(col)
            p.drawText(QPointF(8, 4), text)
            p.restore()

        for x, z, text in self.marks:
            c = QPointF(x, self._sy(z))
            s = 6 * px
            p.setPen(QPen(dark, 1.3 * px))
            p.setBrush(orange)
            p.drawPolygon([c + QPointF(0, -s), c + QPointF(s, 0), c + QPointF(0, s), c + QPointF(-s, 0)])
            label(c.x(), c.y(), text, orange)
        if self.path and self.path["pts"]:
            pts = self.path["pts"]
            cam = self.path["kind"] == "cam"
            p.setPen(QPen(orange, 2 * px))
            for a, b in zip(pts, pts[1:]):
                p.drawLine(QPointF(a["x"], self._sy(a["z"])), QPointF(b["x"], self._sy(b["z"])))
            for i, d in enumerate(pts):
                c = QPointF(d["x"], self._sy(d["z"]))
                if cam and "lx" in d:           # where the camera looks: a handle like the location radius one
                    t = QPointF(d["lx"], self._sy(d["lz"]))
                    p.setPen(QPen(QColor("#f1f1f2"), 1.2 * px, Qt.DashLine))
                    p.drawLine(c, t)
                    p.setPen(QPen(dark, px))
                    p.setBrush(QColor("#f1f1f2"))
                    p.drawEllipse(t, 4.5 * px, 4.5 * px)
                p.setPen(QPen(dark, 1.3 * px))
                p.setBrush(orange)
                p.drawEllipse(c, 6 * px, 6 * px)
                label(c.x(), c.y(), str(i + 1))

    def _path_hit(self, sp: QPointF):
        if not self.path or not self.path["pts"]:
            return None
        best = None
        for i, d in enumerate(self.path["pts"]):
            if math.hypot(sp.x() - d["x"], sp.y() - self._sy(d["z"])) < 9 * self.px:
                best = (i, "pt")
            if "lx" in d and math.hypot(sp.x() - d["lx"], sp.y() - self._sy(d["lz"])) < 8 * self.px:
                return i, "look"
        return best

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self._pick_cb:
            self.cancel_pick()
            return
        super().keyPressEvent(e)

    def select(self, el, center: bool = False):
        self.current = el
        it = self.items_by.get(id(el)) if el is not None else None
        if it and center and not self.mapToScene(self.viewport().rect()).boundingRect().contains(it.pos()):
            self.centerOn(it)       # move the map only if the place is not visible
        self.viewport().update()

    def highlight(self, names: set[str]):
        for it in self.items_by.values():
            it.hot = it.name() in names
        self.viewport().update()

    def sync(self, el):
        it = self.items_by.get(id(el))
        if it:
            it.sync()
        for c in el.iter("Object"):
            ci = self.items_by.get(id(c))
            if ci:
                ci.sync()

    # --- mouse ---
    def wheelEvent(self, e):
        f = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        z = self.transform().m11() * f
        if 0.02 <= z <= 20:
            self.scale(f, f)
            self._zoomed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.map is not None and not getattr(self, "_fitted", False) and self.viewport().width() > 200:
            self._fitted = True
            self.fit()

    def _hit(self, sp: QPointF):
        """(item, what): the radius handle of the selected location wins."""
        cur = self.items_by.get(id(self.current)) if self.current is not None else None
        if cur and cur.kind == "loc":
            hx, hy = cur.pos().x() + max(cur.radius, 2 * self.px), cur.pos().y()
            if math.hypot(sp.x() - hx, sp.y() - hy) < 8 * self.px:
                return cur, "radius"
        best, bd = None, None
        for it in self.items_by.values():
            d = math.hypot(sp.x() - it.pos().x(), sp.y() - it.pos().y())
            reach = max(it.radius, 9 * self.px) if it.kind == "loc" else 10 * self.px
            if d <= reach:
                # among overlapping places pick the smallest, otherwise it is unreachable
                key = (0 if it.kind == "town" else 1, it.radius)
                if best is None or key < bd:
                    best, bd = it, key
        return (best, "move") if best else (None, "")

    def mousePressEvent(self, e):
        if self._pick_cb:
            cb = self._pick_cb
            self.cancel_pick()
            sp = self.mapToScene(e.pos())
            if e.button() == Qt.LeftButton and 0 <= sp.x() <= self.world and 0 <= sp.y() <= self.world:
                cb(sp.x(), self.world - sp.y())
            return
        if e.button() == Qt.LeftButton and self.path:
            hit = self._path_hit(self.mapToScene(e.pos()))
            if hit:
                self._pdrag = hit
                return
        if e.button() == Qt.LeftButton and self.map is not None:
            sp = self.mapToScene(e.pos())
            it, what = self._hit(sp)
            if it:
                self._drag = [it, what, sp, False]
                if self.current is not it.el:
                    self.current = it.el
                    self.picked.emit(it.el)
                    self.viewport().update()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.pos())
        if self._pdrag:
            i, what = self._pdrag
            d = self.path["pts"][i]
            x, z = min(max(sp.x(), 0), self.world), self.world - min(max(sp.y(), 0), self.world)
            if what == "look":
                d["lx"], d["lz"] = x, z
            else:
                if "lx" in d:
                    d["lx"], d["lz"] = d["lx"] + x - d["x"], d["lz"] + z - d["z"]
                d["x"], d["z"] = x, z
            self.viewport().update()
            return
        if self._drag:
            it, what, start, moved = self._drag
            if not moved and math.hypot(sp.x() - start.x(), sp.y() - start.y()) < 4 * self.px:
                return
            self._drag[3] = True
            it.prepareGeometryChange()
            if what == "radius":
                it.radius = max(1.0, math.hypot(sp.x() - it.pos().x(), sp.y() - it.pos().y()))
            else:
                it.setPos(min(max(sp.x(), 0), self.world), min(max(sp.y(), 0), self.world))
            it.update()
            return
        it, _ = self._hit(sp)
        if it is not self.hover:
            self.hover = it
            self.viewport().update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pdrag:
            i, what = self._pdrag
            self._pdrag = None
            d = self.path["pts"][i]
            x, z = (d["lx"], d["lz"]) if what == "look" else (d["x"], d["z"])
            self.path["move"](i, what, x, z)
            return
        if self._drag:
            it, what, _start, moved = self._drag
            self._drag = None
            if moved:
                if what == "radius":
                    self.resized.emit(it.el, round(it.radius, 1))
                else:
                    self.moved.emit(it.el, it.pos().x(), self.world - it.pos().y())
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        if self.map is None:
            return
        sp = self.mapToScene(e.pos())
        it, _ = self._hit(sp)
        m = QMenu(self)
        if it:
            if self.current is not it.el:
                self.current = it.el
                self.picked.emit(it.el)
            m.addAction("Удалить", lambda: self.remove.emit(it.el))
        elif 0 <= sp.x() <= self.world and 0 <= sp.y() <= self.world:
            x, z = sp.x(), self.world - sp.y()
            m.addAction("Новая локация", lambda: self.create.emit("loc", x, z))
            m.addAction("Новый город", lambda: self.create.emit("town", x, z))
        if m.actions():
            m.exec(e.globalPos())

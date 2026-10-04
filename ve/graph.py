"""Dialog graph: replies are nodes, transitions are arrows.

Layout is one DFS pass with a visited set, so loops and returns to earlier
replies cannot hang it; such a transition is drawn as a back arrow.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QMenu

from . import theme

NODE_W = 210
GAP_X, GAP_Y = 26, 46
PAD = 8


class Node(QGraphicsItem):
    def __init__(self, view: "GraphView", name: str):
        super().__init__()
        self.view, self.name = view, name
        self.ghost = False
        self.lines: list[str] = []
        self.h = 34.0
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.hover = False
        self.refresh()

    def refresh(self):
        d = self.view.dialogs
        el = d.items.get(self.name)
        self.ghost = el is None or d.folder_of.get(self.name) != self.view.folder
        self.missing = el is None
        self.role = el.get("role") if el is not None else "NPC"
        self.cond = bool(el is not None and el.get("scriptCondition"))
        self.res = bool(el is not None and el.get("scriptResult"))
        self.hello = self.name in self.view.hello
        text = (el.get("text") if el is not None else "") or self.name
        if self.missing:
            text = self.name + " — нет такой реплики"
        fm = QFontMetricsF(self.view.node_font)
        self.lines = _wrap(text, fm, NODE_W - 2 * PAD - 6, 4)
        self.prepareGeometryChange()
        self.h = max(30.0, len(self.lines) * fm.lineSpacing() + 2 * PAD - 2)
        self.setToolTip(self.name)
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -8, NODE_W + 8, self.h + 20)

    def shape(self):
        p = QPainterPath()
        p.addRect(QRectF(0, 0, NODE_W, self.h + 10))
        return p

    def port(self) -> QPointF:
        return self.pos() + QPointF(NODE_W / 2, self.h)

    def top(self) -> QPointF:
        return self.pos() + QPointF(NODE_W / 2, 0)

    def port_rect(self) -> QRectF:
        return QRectF(NODE_W / 2 - 7, self.h - 5, 14, 14)

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, NODE_W, self.h)
        sel = self.isSelected()
        npc = self.role == "NPC"
        fill = QColor(theme.ORANGE) if sel else QColor("#b9bbbf" if npc else "#dcdde0")
        if self.missing:
            fill = QColor("#c98f86")
        if self.ghost:
            fill.setAlpha(110)
        p.setPen(QPen(QColor("#8a5a14" if sel else "#3d3e40"), 1.4))
        p.setBrush(fill)
        p.drawRoundedRect(r, 5, 5)
        if npc:   # rust stripe on the left: NPC speaks
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.RUST))
            clip = QPainterPath()
            clip.addRoundedRect(r, 5, 5)
            p.setClipPath(clip)
            p.drawRect(QRectF(0, 0, 6, self.h))
            p.setClipping(False)
        p.setPen(QColor(theme.INK if not self.ghost else theme.INK2))
        p.setFont(self.view.node_font)
        fm = QFontMetricsF(self.view.node_font)
        y = PAD - 1 + fm.ascent()
        for ln in self.lines:
            p.drawText(QPointF(PAD + (6 if npc else 0), y), ln)
            y += fm.lineSpacing()
        # marks: condition top right, result bottom right, NPC entry top left
        p.setPen(QPen(QColor("#3d3e40"), 1))
        if self.cond:
            p.setBrush(QColor("#e9e06a"))
            p.drawPolygon(QPolygonF([QPointF(NODE_W - 16, -5), QPointF(NODE_W - 6, -5), QPointF(NODE_W - 11, 4)]))
        if self.res:
            p.setBrush(QColor("#7fc27a"))
            p.drawRect(QRectF(NODE_W - 15, self.h - 4, 8, 8))
        if self.hello:
            p.setBrush(QColor(theme.RUST))
            p.drawEllipse(QPointF(11, -1), 4.5, 4.5)
        if (self.hover or sel) and not self.ghost:
            p.setBrush(QColor("#f4f4f5"))
            p.setPen(QPen(QColor("#3d3e40"), 1.2))
            p.drawEllipse(self.port_rect().center(), 5.5, 5.5)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()


def _wrap(text: str, fm: QFontMetricsF, width: float, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in text.replace("\n", " ").split(" "):
        trial = (cur + " " + word).strip()
        if fm.horizontalAdvance(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    elif len(lines) == max_lines:
        lines[-1] = fm.elidedText(lines[-1] + " …", Qt.ElideRight, width)
    if lines and fm.horizontalAdvance(lines[-1]) > width:
        lines[-1] = fm.elidedText(lines[-1], Qt.ElideRight, width)
    return lines or [""]


class Edge(QGraphicsPathItem):
    def __init__(self, a: Node, b: Node, back: bool):
        super().__init__()
        self.a, self.b, self.back = a, b, back
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(1)
        self.head = QPolygonF()
        self.route()

    def route(self):
        s, t = self.a.port(), self.b.top()
        path = QPainterPath(s)
        if self.a is self.b:
            path.cubicTo(s + QPointF(150, 50), t + QPointF(190, -60), t)
        elif self.back:
            # a back arrow goes around the nodes
            side = NODE_W / 2 + 34 + min(abs(s.y() - t.y()) * 0.12, 90)
            right = s.x() >= t.x()
            dx = side if right else -side
            path.cubicTo(s + QPointF(dx, 60), t + QPointF(dx, -70), t)
        else:
            dy = max((t.y() - s.y()) * 0.5, 22)
            path.cubicTo(s + QPointF(0, dy), t - QPointF(0, dy), t)
        self.setPath(path)
        d = path.pointAtPercent(1.0) - path.pointAtPercent(0.97)
        ln = (d.x() ** 2 + d.y() ** 2) ** 0.5 or 1
        ux, uy = d.x() / ln, d.y() / ln
        self.head = QPolygonF([t, t - QPointF(ux * 9 - uy * 4.5, uy * 9 + ux * 4.5),
                               t - QPointF(ux * 9 + uy * 4.5, uy * 9 - ux * 4.5)])

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(10)
        return st.createStroke(self.path())

    def boundingRect(self):
        return self.path().boundingRect().adjusted(-12, -12, 12, 12)

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(theme.ORANGE) if (self.isSelected() or self.back) else QColor("#c9cacc")
        pen = QPen(col, 2.6 if self.isSelected() else 1.5)
        if self.back:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self.path())
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(self.head)


class GraphView(QGraphicsView):
    selected = Signal(str)          # reply name or ""
    structure_changed = Signal()    # added / removed / linked
    open_folder = Signal(str, str)  # folder, reply: jump to a foreign node

    def __init__(self, dialogs, index):
        super().__init__()
        self.dialogs, self.index = dialogs, index
        self.folder = ""
        self.hello: set[str] = set()
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.node_font = QFont("Tahoma", 9)
        self.setObjectName("canvas")
        self.sc = QGraphicsScene(self)
        self.sc.setItemIndexMethod(QGraphicsScene.NoIndex)   # node height changes with its text
        self.setScene(self.sc)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#3a3b3c")))
        self.setFocusPolicy(Qt.StrongFocus)
        self._drag_from: Node | None = None
        self._rubber: QGraphicsPathItem | None = None
        self._zoom = 1.0
        self.sc.selectionChanged.connect(self._sel)

    # --- build ---
    def show_folder(self, folder: str, select: str | None = None, keep_view: bool = False):
        self.folder = folder
        self.hello = set(self.index.hello_users())
        center = self.mapToScene(self.viewport().rect().center()) if keep_view else None
        self.sc.blockSignals(True)
        self._drag_from = self._rubber = None
        self.sc.clear()
        self.nodes, self.edges = {}, []
        d = self.dialogs
        own = [e.get("name") for e in d.in_folder(folder)]
        names = list(own)
        for n in own:                       # replies from other folders referenced from here
            for t in d.next_of(n):
                if t not in names:
                    names.append(t)
        for n in names:
            node = Node(self, n)
            self.nodes[n] = node
            self.sc.addItem(node)
        depth = self._layout(own, names)
        for n in own:
            for t in d.next_of(n):
                if t in self.nodes:
                    e = Edge(self.nodes[n], self.nodes[t], depth.get(t, 0) <= depth.get(n, 0))
                    self.edges.append(e)
                    self.sc.addItem(e)
        r = self.sc.itemsBoundingRect().adjusted(-300, -200, 300, 300)
        self.sc.setSceneRect(r)
        self.sc.blockSignals(False)
        node = self.nodes.get(select) if select else None
        if node:
            node.setSelected(True)
        if keep_view and center is not None:
            self.centerOn(center)
            if node and not self.mapToScene(self.viewport().rect()).boundingRect().intersects(node.sceneBoundingRect()):
                self.centerOn(node)
        else:
            self.reset_view()
            if node and not self.mapToScene(self.viewport().rect()).boundingRect().contains(node.sceneBoundingRect()):
                self.centerOn(node)
        self._sel()

    def _layout(self, own: list[str], names: list[str]) -> dict[str, int]:
        d = self.dialogs
        inside = set(own)
        has_parent = {t for n in own for t in d.next_of(n) if t != n}
        roots = [n for n in own if n not in has_parent]
        depth: dict[str, int] = {}
        xpos: dict[str, float] = {}
        cursor = [0.0]

        def place(n: str, dep: int):
            # explicit stack: deep dialogs do not hit the recursion limit
            stack = [(n, dep, None)]
            depth[n] = dep
            while stack:
                name, dp, it = stack.pop()
                if it is None:
                    kids = [t for t in d.next_of(name) if t in self.nodes and t not in depth and (name in inside)]
                    for k in kids:
                        depth[k] = dp + 1
                    it = [kids, 0]
                kids, i = it
                if i < len(kids):
                    it[1] += 1
                    stack.append((name, dp, it))
                    stack.append((kids[i], dp + 1, None))
                    continue
                if kids:
                    xpos[name] = (xpos[kids[0]] + xpos[kids[-1]]) / 2
                else:
                    xpos[name] = cursor[0]
                    cursor[0] += NODE_W + GAP_X

        for r in roots:
            place(r, 0)
            cursor[0] += GAP_X
        for n in names:                    # rootless loops and lone nodes
            if n not in depth:
                place(n, 0)
                cursor[0] += GAP_X
        # keep a parent off its left neighbour on the same level
        levels: dict[int, list[str]] = {}
        for n, dp in depth.items():
            levels.setdefault(dp, []).append(n)
        y = 0.0
        for dp in sorted(levels):
            row = sorted(levels[dp], key=lambda n: xpos[n])
            last = None
            for n in row:
                if last is not None and xpos[n] < last + NODE_W + GAP_X:
                    xpos[n] = last + NODE_W + GAP_X
                last = xpos[n]
            for n in row:
                self.nodes[n].setPos(xpos[n], y)
            y += max(self.nodes[n].h for n in row) + GAP_Y
        return depth

    def reset_view(self):
        self.resetTransform()
        self._zoom = 1.0
        r = self.sc.itemsBoundingRect()
        if r.isEmpty():
            return
        vw, vh = self.viewport().width() - 40, self.viewport().height() - 40
        if r.width() > vw or r.height() > vh:
            z = max(0.45, min(vw / r.width(), vh / r.height()))
            self.scale(z, z)
            self._zoom = z
        self.centerOn(QPointF(r.center().x(), r.top() + min(r.height(), vh / self._zoom) / 2))

    def refresh_node(self, name: str):
        n = self.nodes.get(name)
        if n:
            n.refresh()

    def current(self) -> str:
        for it in self.sc.selectedItems():
            if isinstance(it, Node):
                return it.name
        return ""

    def _sel(self):
        self.selected.emit(self.current())

    # --- mouse ---
    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        z = self._zoom * f
        if 0.25 <= z <= 2.0:
            self._zoom = z
            self.scale(f, f)

    def _node_at(self, pos) -> Node | None:
        for it in self.items(pos):
            if isinstance(it, Node):
                return it
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            n = self._node_at(e.pos())
            if n and not n.ghost and n.port_rect().adjusted(-4, -4, 4, 4).contains(n.mapFromScene(self.mapToScene(e.pos()))):
                self._drag_from = n
                self._rubber = QGraphicsPathItem()
                self._rubber.setPen(QPen(QColor(theme.ORANGE), 2, Qt.DashLine))
                self._rubber.setZValue(5)
                self.sc.addItem(self._rubber)
                self.setDragMode(QGraphicsView.NoDrag)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_from:
            p = QPainterPath(self._drag_from.port())
            p.lineTo(self.mapToScene(e.pos()))
            self._rubber.setPath(p)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_from:
            src = self._drag_from
            self.sc.removeItem(self._rubber)
            self._drag_from = self._rubber = None
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            tgt = self._node_at(e.pos())
            if tgt is not None:
                if not tgt.missing:
                    self.dialogs.link(src.name, tgt.name)
                    self._changed(src.name)
            else:
                self.add_reply(src.name)
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        n = self._node_at(e.pos())
        if n and n.ghost and not n.missing:
            self.open_folder.emit(self.dialogs.folder_of[n.name], n.name)
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selection()
        elif e.key() == Qt.Key_Insert:
            self.add_reply(self.current() or None)
        elif e.key() == Qt.Key_Home:
            self.reset_view()
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        n = self._node_at(e.pos())
        m = QMenu(self)
        if n and not n.ghost:
            n.setSelected(True)
            other = "игрока" if n.role == "NPC" else "NPC"
            m.addAction(f"Добавить ответ {other}", lambda: self.add_reply(n.name))
            parents = [p for p in self.dialogs.incoming().get(n.name, [])]
            if parents:
                m.addAction("Левее среди ответов", lambda: self._shift(n.name, -1))
                m.addAction("Правее среди ответов", lambda: self._shift(n.name, 1))
            m.addAction("Перенести в папку…", lambda: self._move(n.name))
            m.addSeparator()
            m.addAction("Удалить реплику", self.delete_selection)
        elif n and n.ghost:
            if not n.missing:
                m.addAction("Перейти к реплике", lambda: self.open_folder.emit(self.dialogs.folder_of[n.name], n.name))
        else:
            edge = next((it for it in self.items(e.pos()) if isinstance(it, Edge)), None)
            if edge:
                m.addAction("Убрать переход", lambda: self._unlink(edge))
            else:
                m.addAction("Новая реплика NPC", lambda: self.add_reply(None, "NPC"))
                m.addAction("Новая реплика игрока", lambda: self.add_reply(None, "PLAYER"))
        if m.actions():
            m.exec(e.globalPos())

    # --- editing ---
    def add_reply(self, parent: str | None, role: str | None = None):
        d = self.dialogs
        if not self.folder:
            return
        if parent:
            prole = d.items[parent].get("role")
            role = role or ("PLAYER" if prole == "NPC" else "NPC")
            name = d.child_name(parent, role)
            d.add(name, role, self.folder, after=parent)
            d.link(parent, name)
        else:
            role = role or "NPC"
            base = self.folder.split("/")[1] if "/" in self.folder else "Dlg"
            name = d.unique_name(f"{base}_NewNpc_hellodlg0" if role == "NPC" else f"{base}_Dlg_pl_1")
            d.add(name, role, self.folder)
        self._changed(name)

    def delete_selection(self):
        items = self.sc.selectedItems()
        edge = next((i for i in items if isinstance(i, Edge)), None)
        node = next((i for i in items if isinstance(i, Node)), None)
        if node and not node.ghost:
            users = self.index.hello_users().get(node.name, [])
            from .widgets import confirm
            if users and not confirm(self, f"С этой реплики начинает разговор {users[0][1]} ({users[0][0]}). Удалить её?"):
                return
            self.dialogs.delete(node.name)
            self._changed(None)
        elif edge:
            self._unlink(edge)

    def _move(self, name: str):
        from .widgets import ask_choice
        folders = [f for f in self.dialogs.folders if f != "Root"]
        to = ask_choice(self, "Перенести реплику", "Папка", folders, self.folder)
        if to and to != self.folder:
            self.dialogs.move_to_folder(name, to)
            self._changed(None)

    def _unlink(self, edge: Edge):
        self.dialogs.unlink(edge.a.name, edge.b.name)
        self._changed(edge.a.name)

    def _shift(self, name: str, step: int):
        for p in self.dialogs.incoming().get(name, []):
            nx = self.dialogs.next_of(p)
            i = nx.index(name)
            j = i + step
            if 0 <= j < len(nx):
                nx[i], nx[j] = nx[j], nx[i]
                self.dialogs.set_next(p, nx)
        self._changed(name)

    def _changed(self, select: str | None):
        self.show_folder(self.folder, select, keep_view=True)
        self.structure_changed.emit()

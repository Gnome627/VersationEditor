"""Game-style skin built from data/if/frames/*_hd.

Frame pieces are separate DDS files; they are glued into nine-patch images and
used through QSS border-image. Without the _hd folders the window keeps the
same colours with flat borders.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .game import load_image

INK = "#1b1b1c"          # text on grey plates
INK2 = "#4b4a48"         # secondary text
BG = "#2e2f30"           # window background
PLATE = "#aaacaf"        # button fill
FIELD = "#c9cbce"        # input field
ORANGE = "#f2b65c"
RUST = "#8a6b47"
LIGHT = "#e2e2e4"        # text on dark

_dir: Path | None = None
_have: set[str] = set()


def _nine(parts: dict, size: int | None = None):
    from PIL import Image
    tl = parts["tl"]
    cw, ch = tl.size
    mid = size or 4
    w, h = cw * 2 + mid, ch * 2 + mid
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    def crop(im, cw_, ch_):
        # long sides (1024 px): take the middle
        x = (im.width - cw_) // 2 if im.width > cw_ else 0
        y = (im.height - ch_) // 2 if im.height > ch_ else 0
        return im.crop((x, y, x + min(cw_, im.width), y + min(ch_, im.height))).resize((cw_, ch_))

    if "fill" in parts:
        out.paste(parts["fill"].resize((mid, mid)), (cw, ch))
    out.paste(crop(parts["t"], mid, ch), (cw, 0))
    out.paste(crop(parts["b"], mid, ch), (cw, h - ch))
    out.paste(crop(parts["l"], cw, mid), (0, ch))
    out.paste(crop(parts["r"], cw, mid), (w - cw, ch))
    out.paste(tl, (0, 0))
    out.paste(parts["tr"], (w - cw, 0))
    out.paste(parts["bl"], (0, h - ch))
    out.paste(parts["br"], (w - cw, h - ch))
    return out


def _ring(colour):
    """Quest ring, used when the game has no icon of its own."""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((2, 2, 61, 61), fill=(60, 60, 62, 255))
    d.ellipse((6, 6, 57, 57), fill=colour + (255,))
    d.ellipse((20, 20, 43, 43), fill=(60, 60, 62, 255))
    d.ellipse((25, 25, 38, 38), fill=(0, 0, 0, 0))
    return im.resize((16, 16), Image.LANCZOS)


def _folder():
    """Folder icon in the frame palette: sand body, dark edge, light top line."""
    from PIL import Image, ImageDraw
    s = 8                                    # draw 8x larger and downscale for clean edges
    im = Image.new("RGBA", (16 * s, 16 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    edge, back, front, light = (52, 40, 28, 255), (122, 94, 60, 255), (176, 138, 92, 255), (214, 184, 140, 255)
    r = int(1.2 * s)
    d.rounded_rectangle((1 * s, 2.5 * s, 7.5 * s, 6 * s), r, fill=back, outline=edge, width=s)          # tab
    d.rounded_rectangle((1 * s, 4 * s, 15 * s - 1, 13.5 * s), r, fill=back, outline=edge, width=s)       # back
    d.rounded_rectangle((1 * s, 6 * s, 15 * s - 1, 13.5 * s), r, fill=front, outline=edge, width=s)      # front
    d.line((2.4 * s, 7.4 * s, 13.6 * s, 7.4 * s), fill=light, width=int(0.8 * s))                        # highlight
    return im.resize((16, 16), Image.LANCZOS)


def _truck():
    """Truck icon for the vehicles folder, same palette as the folder."""
    from PIL import Image, ImageDraw
    s = 8
    im = Image.new("RGBA", (16 * s, 16 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    edge, back, front, light = (52, 40, 28, 255), (122, 94, 60, 255), (176, 138, 92, 255), (214, 184, 140, 255)
    r = int(0.9 * s)
    d.rounded_rectangle((0.5 * s, 3 * s, 9.5 * s, 11.5 * s), r, fill=front, outline=edge, width=s)        # cargo box
    d.line((2 * s, 4.6 * s, 8 * s, 4.6 * s), fill=light, width=int(0.8 * s))
    d.polygon([(9.5 * s, 11.5 * s), (9.5 * s, 5.5 * s), (12.6 * s, 5.5 * s), (15.3 * s, 8.4 * s), (15.3 * s, 11.5 * s)],
              fill=back, outline=edge, width=s)                                                             # cab
    d.polygon([(10.9 * s, 6.9 * s), (12.2 * s, 6.9 * s), (13.7 * s, 8.6 * s), (10.9 * s, 8.6 * s)], fill=light)  # window
    for x in (4.2, 12.0):                                                                                   # wheels
        d.ellipse(((x - 2.2) * s, 9.6 * s, (x + 2.2) * s, 14 * s), fill=edge)
        d.ellipse(((x - 0.9) * s, 10.9 * s, (x + 0.9) * s, 12.7 * s), fill=front)
    return im.resize((16, 16), Image.LANCZOS)


def icon(name: str):
    from PySide6.QtGui import QIcon
    return QIcon(pix(name)) if name in _have else QIcon()


def build(game) -> Path:
    """Build skin images into a temp folder and return its path."""
    global _dir
    _dir = Path(tempfile.mkdtemp(prefix="versation_skin_"))
    fr = game.data / "if" / "frames"

    def L(rel):
        p = fr / rel
        if not p.is_file():   # extension case varies: mb_tl.DDS
            alt = [q for q in p.parent.glob("*") if q.name.lower() == p.name.lower()] if p.parent.is_dir() else []
            p = alt[0] if alt else p
        im = load_image(p) if p.is_file() else None
        if im is None:
            raise FileNotFoundError(rel)
        return im

    def save(name, fn):
        try:
            im = fn()
            im.save(_dir / f"{name}.png")
            _have.add(name)
        except Exception:
            pass

    for st in ("enabled", "selected", "pressed", "disabled", "orange"):
        d = f"btngray1_hd/{st}/"
        save(f"btn_{st}", lambda d=d: _nine({
            "tl": L(d + "corner_top-left.dds"), "tr": L(d + "corner_top-right.dds"),
            "bl": L(d + "corner_bottom-left.dds"), "br": L(d + "corner_bottom-right.dds"),
            "t": L(d + "margin_top.dds"), "b": L(d + "margin_bottom.dds"),
            "l": L(d + "margin_left.dds"), "r": L(d + "margin_right.dds"), "fill": L(d + "fill.dds")}))
    d = "combobox_hd/"
    for name, fill in (("field", "fill_gray.dds"), ("field_on", "fill_orange.dds")):
        save(name, lambda fill=fill: _nine({
            "tl": L(d + "frm_top-left.dds"), "tr": L(d + "frm_top-right.dds"),
            "bl": L(d + "frm_bottom-left.dds"), "br": L(d + "frm_bottom-right.dds"),
            "t": L(d + "frm_margin_top.dds"), "b": L(d + "frm_margin_bottom.dds"),
            "l": L(d + "frm_margin_left.dds"), "r": L(d + "frm_margin_right.dds"), "fill": L(d + fill)}))
    for name, d, p in (("panel", "msgbox_hd/", "mb_"), ("popup", "menu_hd/", "menu_")):
        save(name, lambda d=d, p=p: _nine({
            "tl": L(d + p + "tl.dds"), "tr": L(d + p + "tr.dds"), "bl": L(d + p + "bl.dds"), "br": L(d + p + "br.dds"),
            "t": L(d + p + "t.dds"), "b": L(d + p + "b.dds"), "l": L(d + p + "l.dds"), "r": L(d + p + "r.dds"),
            "fill": L(d + p + "fill.dds")}, 64))
    d = "frame_e_hd/"
    save("frame", lambda: _nine({
        "tl": L(d + "corner_top-left.dds"), "tr": L(d + "corner_top-right.dds"),
        "bl": L(d + "corner_bottom-left.dds"), "br": L(d + "corner_bottom-right.dds"),
        "t": L(d + "margin_horizontal.dds"), "b": L(d + "margin_horizontal.dds"),
        "l": L(d + "margin_vertical.dds"), "r": L(d + "margin_vertical.dds")}, 32))
    for src, name in (("checkbox_hd/chkbox_e_n.dds", "chk"), ("checkbox_hd/chkbox_e_c.dds", "chk_on"),
                      ("checkbox_hd/chkbox_s_n.dds", "chk_h"), ("checkbox_hd/chkbox_s_c.dds", "chk_on_h"),
                      ("checkbox_hd/chkbox_d_n.dds", "chk_d"), ("checkbox_hd/chkbox_d_c.dds", "chk_on_d"),
                      ("scroll1_hd/btn_up_e.dds", "up"), ("scroll1_hd/btn_up_h.dds", "up_h"),
                      ("scroll1_hd/btn_down_e.dds", "down"), ("scroll1_hd/btn_down_h.dds", "down_h"),
                      ("slider_hd/b_plus_e.dds", "plus"), ("slider_hd/b_plus_h.dds", "plus_h"),
                      ("slider_hd/b_plus_p.dds", "plus_p"), ("slider_hd/b_minus_e.dds", "minus"),
                      ("slider_hd/b_minus_h.dds", "minus_h"), ("slider_hd/b_minus_p.dds", "minus_p"),
                      ("btncloseopenlist_hd/btn_openlist_e.dds", "open"),
                      ("btncloseopenlist_hd/btn_closelist_e.dds", "close")):
        save(name, lambda src=src: L(src))
    for a, b in (("up", "left"), ("up_h", "left_h"), ("down", "right"), ("down_h", "right_h")):
        save(b, lambda a=a: load_image(_dir / f"{a}.png").rotate(90))

    def track():
        from PIL import Image
        s = "scroll1_hd/scrollzone_"
        rows = [[L(s + "top-left.dds"), L(s + "top.dds"), L(s + "top-right.dds")],
                [L(s + "left.dds"), L(s + "center.dds"), L(s + "right.dds")],
                [L(s + "bottom-left.dds"), L(s + "bottom.dds"), L(s + "bottom-right.dds")]]
        w = sum(i.width for i in rows[0])
        h = sum(r[0].height for r in rows)
        out = Image.new("RGBA", (w, h))
        y = 0
        for r in rows:
            x = 0
            for i in r:
                out.paste(i, (x, y))
                x += i.width
            y += r[0].height
        return out
    # list icons: quest rings from the game radar; the folder is drawn here
    radar = game.data / "if" / "ico" / "radarwnd"
    for name, f, colour in (("quest_a", "icn_quest-a.dds", (255, 165, 0)), ("quest_b", "icn_quest-b.dds", (235, 0, 235))):
        save(name, lambda f=f, colour=colour: load_image(radar / f) or _ring(colour))
    save("folder", _folder)
    save("truck", _truck)
    save("track", track)
    save("track_h", lambda: load_image(_dir / "track.png").rotate(90, expand=True))
    return _dir


def url(name: str) -> str:
    return (_dir / f"{name}.png").as_posix()


def pix(name: str):
    from PySide6.QtGui import QPixmap
    return QPixmap(str(_dir / f"{name}.png")) if name in _have else QPixmap()


def has(name: str) -> bool:
    return name in _have


def _bi(name: str, cut: int, width: int, fallback: str) -> str:
    if name in _have:
        return f"border-width: {width}px; border-image: url({url(name)}) {cut} {cut} {cut} {cut} stretch stretch;"
    return fallback


def qss() -> str:
    btn = lambda st, bg, bd="#5c5d60": _bi(f"btn_{st}", 16, 6, f"border: 1px solid {bd}; border-radius: 4px; background: {bg};")
    field = _bi("field", 16, 5, f"border: 1px solid #6f7073; border-radius: 4px; background: {FIELD};")
    panel = _bi("panel", 30, 13, f"border: 2px solid {RUST}; border-radius: 4px; background: {PLATE};")
    popup = _bi("panel", 30, 13, f"border: 2px solid {RUST}; background: {PLATE};")
    frame = _bi("frame", 32, 12, f"border: 3px solid {RUST};")
    img = lambda n: f"image: url({url(n)});" if n in _have else ""
    bimg = lambda n: f"border-image: url({url(n)});" if n in _have else f"background: {PLATE};"
    return f"""
* {{ font-family: Tahoma; font-size: 9pt; color: {INK}; outline: 0; }}
QMainWindow, QDialog, #root {{ background: {BG}; }}
QToolTip {{ background: {FIELD}; color: {INK}; border: 1px solid {RUST}; padding: 3px; }}
#panel {{ {panel} }}
#canvas {{ {frame} background: #3a3b3c; }}
QLabel {{ background: transparent; }}
QLabel#head {{ font-weight: bold; }}
QLabel#dim, QLabel#join {{ color: {INK2}; }}
QLabel#onDark {{ color: {LIGHT}; }}

QPushButton {{ {btn("enabled", PLATE)} padding: 0px 8px; min-height: 14px; }}
QPushButton:hover {{ {btn("selected", "#cfd1d4")} }}
QPushButton:pressed {{ {btn("pressed", "#9fa3a7")} }}
QPushButton:checked {{ {btn("orange", ORANGE, "#8a5a14")} }}
QPushButton:disabled {{ {btn("disabled", "#9a9ea2")} color: #6c6d6f; }}
QPushButton#tab {{ padding: 2px 18px; font-size: 10pt; }}
QPushButton#accent {{ {btn("orange", ORANGE, "#8a5a14")} }}
QPushButton#flat {{ border: none; border-image: none; background: transparent; padding: 0 3px; color: {INK2}; }}
QPushButton#flat:hover {{ color: {INK}; }}
QToolButton {{ border: none; background: transparent; padding: 0; }}
QToolButton#plus {{ {img("plus")} }}
QToolButton#plus:hover {{ {img("plus_h")} }}
QToolButton#plus:pressed {{ {img("plus_p")} }}
QToolButton#minus {{ {img("minus")} }}
QToolButton#minus:hover {{ {img("minus_h")} }}
QToolButton#minus:pressed {{ {img("minus_p")} }}
QToolButton::menu-indicator {{ image: none; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{ {field} padding: 0px 2px;
    selection-background-color: {ORANGE}; selection-color: {INK}; }}
QPlainTextEdit#code {{ font-family: Consolas; font-size: 9.5pt; }}
QLineEdit:disabled {{ color: #6c6d6f; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}
QComboBox {{ {field} padding: 0px 2px; min-height: 14px; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox::down-arrow {{ {img("down")} width: 14px; height: 14px; }}
QComboBox QAbstractItemView, QAbstractItemView#completer {{ background: {FIELD}; border: 1px solid {RUST};
    selection-background-color: {ORANGE}; selection-color: {INK}; }}
QCheckBox {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; {img("chk")} }}
QCheckBox::indicator:hover {{ {img("chk_h")} }}
QCheckBox::indicator:checked {{ {img("chk_on")} }}
QCheckBox::indicator:checked:hover {{ {img("chk_on_h")} }}

QTreeView, QListView, QTableView {{ background: transparent; border: none; show-decoration-selected: 1;
    selection-background-color: {ORANGE}; selection-color: {INK}; }}
QTreeView::item, QListView::item {{ padding: 2px 2px; border: none; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {ORANGE}; color: {INK}; }}
QTreeView::item:hover:!selected, QListView::item:hover:!selected {{ background: rgba(255,255,255,50); }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:selected {{ background: {ORANGE}; }}
QTreeView::branch:has-children:closed {{ {img("open")} }}
QTreeView::branch:has-children:open {{ {img("close")} }}
QHeaderView, QTableCornerButton::section {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; border: none; border-bottom: 1px solid #7a7b7e; padding: 2px 4px; color: {INK2}; }}
QTableView {{ gridline-color: #8d8f92; }}

QScrollBar:vertical {{ width: 14px; margin: 14px 0 14px 0; {("border-image: url(" + url("track") + ") 16 0 16 0; border-width: 5px 0 5px 0;") if has("track") else "background: #77797c;"} }}
QScrollBar::handle:vertical {{ {btn("enabled", PLATE)} border-width: 5px; min-height: 22px; }}
QScrollBar::handle:vertical:hover {{ {btn("selected", "#cfd1d4")} border-width: 5px; }}
QScrollBar::sub-line:vertical {{ height: 14px; subcontrol-position: top; subcontrol-origin: margin; {bimg("up")} }}
QScrollBar::sub-line:vertical:hover {{ {bimg("up_h")} }}
QScrollBar::add-line:vertical {{ height: 14px; subcontrol-position: bottom; subcontrol-origin: margin; {bimg("down")} }}
QScrollBar::add-line:vertical:hover {{ {bimg("down_h")} }}
QScrollBar:horizontal {{ height: 14px; margin: 0 14px 0 14px; {("border-image: url(" + url("track_h") + ") 0 16 0 16; border-width: 0 5px 0 5px;") if has("track_h") else "background: #77797c;"} }}
QScrollBar::handle:horizontal {{ {btn("enabled", PLATE)} border-width: 5px; min-width: 22px; }}
QScrollBar::sub-line:horizontal {{ width: 14px; subcontrol-position: left; subcontrol-origin: margin; {bimg("left")} }}
QScrollBar::add-line:horizontal {{ width: 14px; subcontrol-position: right; subcontrol-origin: margin; {bimg("right")} }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar::up-arrow, QScrollBar::down-arrow, QScrollBar::left-arrow, QScrollBar::right-arrow {{ image: none; width: 0; height: 0; }}
QAbstractScrollArea::corner {{ background: transparent; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

QMenu {{ background-color: {BG}; {popup} padding: 2px; }}
QMenu::item {{ padding: 3px 18px 3px 10px; background: transparent; }}
QMenu::item:selected {{ background: {ORANGE}; }}
QMenu::item:disabled {{ color: {INK2}; }}
QMenu::separator {{ height: 1px; background: #7a7b7e; margin: 3px 6px; }}
QSplitter::handle {{ background: transparent; }}
#chip {{ {field} }}
#chipOn {{ {_bi("field_on", 16, 5, f"border: 1px solid #8a5a14; border-radius: 4px; background: {ORANGE};")} }}
QMessageBox, QInputDialog {{ background: {PLATE}; }}
"""

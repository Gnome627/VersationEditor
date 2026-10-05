"""Application icon, drawn in code: a wheel whose tyre is a hex nut."""
from __future__ import annotations

import math
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int = 256):
    from PIL import Image, ImageDraw
    k = 4 if size < 128 else 2                  # supersampling for clean edges
    s = size * k
    u = s / 64.0                                # the design is laid out on a 64-unit grid
    c = 32.0
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    edge, ink = (24, 22, 20, 255), (40, 32, 24, 255)
    sand, light, rust = (226, 178, 104, 255), (246, 216, 160, 255), (138, 107, 71, 255)

    def pt(r, deg):
        a = math.radians(deg)
        return ((c + r * math.cos(a)) * u, (c + r * math.sin(a)) * u)

    def disc(r, fill):
        d.ellipse(((c - r) * u, (c - r) * u, (c + r) * u, (c + r) * u), fill=fill)

    # the nut: six steel faces, lit from above
    d.polygon([pt(31, 60 * i) for i in range(6)], fill=edge)
    for i in range(6):
        mid = 60 * i + 30                       # direction the face looks at
        shade = 0.5 - 0.5 * math.sin(math.radians(mid))        # 1 at the top, 0 at the bottom
        g = int(74 + 86 * shade)
        d.polygon([(c * u, c * u), pt(28.6, 60 * i), pt(28.6, 60 * i + 60)], fill=(g, g + 2, g + 6, 255))
    for i in range(6):                          # seams between the faces
        d.line([pt(19, 60 * i), pt(28.6, 60 * i)], fill=edge, width=max(int(0.9 * u), 1))

    # the wheel in the nut's hole
    disc(21.5, edge)
    disc(19.5, rust)
    disc(17.6, sand)
    d.arc(((c - 16.2) * u, (c - 16.2) * u, (c + 16.2) * u, (c + 16.2) * u), 200, 340, fill=light,
          width=max(int(1.5 * u), 1))
    for i in range(6):                          # windows of the rim, spokes stay between them
        a0 = 60 * i - 90 - 19
        d.arc(((c - 14.6) * u, (c - 14.6) * u, (c + 14.6) * u, (c + 14.6) * u), a0, a0 + 38, fill=ink,
              width=max(int(5.4 * u), 1))
    disc(7.4, rust)                             # hub with its bolts
    disc(6.2, light)
    for i in range(6):
        x, y = pt(4.1, 60 * i - 60)
        r = 0.95 * u
        d.ellipse((x - r, y - r, x + r, y + r), fill=ink)
    disc(1.7, ink)
    return im.resize((size, size), Image.LANCZOS)


def save_ico(path: Path) -> Path:
    """Write a multi-size .ico (for the exe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    draw(256).save(path, format="ICO", sizes=[(n, n) for n in SIZES], append_images=[draw(n) for n in SIZES[:-1]])
    return path


def qicon():
    """The same picture as a window icon."""
    from PySide6.QtGui import QIcon, QImage, QPixmap
    icon = QIcon()
    for n in (16, 32, 48, 256):
        im = draw(n)
        icon.addPixmap(QPixmap.fromImage(QImage(im.tobytes(), n, n, QImage.Format_RGBA8888).copy()))
    return icon

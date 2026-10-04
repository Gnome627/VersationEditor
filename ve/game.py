"""Game files: root lookup, cp1251 I/O with backup, undo, terrain, DDS images."""
from __future__ import annotations

import math
import os
import shutil
import struct
import sys
from array import array
from pathlib import Path

from . import xmlrt

ENC = "cp1251"
BACKUP_DIR = "VersationEditor_backup"
CELL = 8.0  # displace.bin grid step, game units


class GameError(Exception):
    pass


def find_root(start: Path | None = None) -> Path | None:
    """Game root is the folder with data/maps; searched upwards from the exe/script."""
    env = os.environ.get("VERSATION_GAME_ROOT")
    if env and (Path(env) / "data" / "maps").is_dir():
        return Path(env)
    starts = [start] if start else []
    if getattr(sys, "frozen", False):
        starts.append(Path(sys.executable).resolve().parent)
    starts += [Path.cwd(), Path(__file__).resolve().parent]
    for s in starts:
        for p in [s, *s.parents]:
            if (p / "data" / "maps").is_dir():
                return p
    return None


class Doc:
    """One game XML file: lossless tree plus a dirty flag."""

    def __init__(self, game: "Game", rel: str, create_root: str | None = None):
        self.game = game
        self.rel = rel
        self.path = game.root / rel
        self.dirty = False
        if self.path.is_file():
            self.src = self.path.read_bytes().decode(ENC, errors="replace")
            self.exists = True
        elif create_root:
            self.src = ('<?xml version="1.0" encoding="windows-1251" standalone="yes" ?>\r\n'
                        f"<{create_root}>\r\n</{create_root}>\r\n")
            self.exists = False
        else:
            raise GameError(f"нет файла {rel}")
        self.tree = xmlrt.parse(self.src)
        if not self.tree.elements():
            raise GameError(f"{rel}: нет корневого элемента")
        self._base = self.src       # text at the last undo step
        self._touched = False

    @property
    def root(self):
        return self.tree.elements()[0]

    def touch(self):
        self.dirty = True
        self._touched = True
        self.game.changed()

    def restore(self, text: str):
        """Restore the document to a text from history (undo/redo)."""
        self.tree = xmlrt.parse(text)
        self._base = text
        self._touched = False
        self.dirty = text != self.src or not self.exists

    def text(self) -> str:
        return self.tree.dump()

    def save(self):
        out = self.text()
        try:
            data = out.encode(ENC)
        except UnicodeEncodeError as e:
            bad = sorted({c for c in out if not _encodable(c)})
            raise GameError(f"{self.rel}: символы не входят в windows-1251: {' '.join(bad)}") from e
        xmlrt.parse(out)  # never write what we cannot read back
        if self.path.is_file():
            bak = self.game.root / BACKUP_DIR / self.rel
            if not bak.exists():
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.path, bak)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self.path)
        self.src = out
        self.exists = True
        self.dirty = False
        if not self._touched:
            self._base = out


def _encodable(c: str) -> bool:
    try:
        c.encode(ENC)
        return True
    except UnicodeEncodeError:
        return False


class Game:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.data = self.root / "data"
        self._docs: dict[str, Doc] = {}
        self._listeners: list = []
        self._terrain: dict[str, tuple[int, array] | None] = {}
        self._undo: list[list] = []
        self._redo: list[list] = []

    # --- documents ---
    def doc(self, rel: str, create_root: str | None = None) -> Doc:
        key = rel.replace("\\", "/").lower()
        if key not in self._docs:
            self._docs[key] = Doc(self, self.resolve(rel), create_root)
        return self._docs[key]

    def resolve(self, rel: str) -> str:
        """File name case varies between maps (QuestStates.xml / queststates.xml)."""
        p = self.root / rel
        if p.exists():
            return rel
        parent = p.parent
        if parent.is_dir():
            low = p.name.lower()
            for f in parent.iterdir():
                if f.name.lower() == low:
                    return str(Path(rel).parent / f.name).replace("\\", "/")
        return rel

    def has(self, rel: str) -> bool:
        return (self.root / self.resolve(rel)).is_file()

    def on_change(self, fn):
        self._listeners.append(fn)

    def changed(self):
        for fn in self._listeners:
            fn()

    # --- undo ---
    def commit(self) -> bool:
        """Close an undo step: remember what touched documents looked like before."""
        group = []
        for d in self._docs.values():
            if d._touched:
                d._touched = False
                t = d.text()
                if t != d._base:
                    group.append((d, d._base, t))
                    d._base = t
        if group:
            self._undo.append(group)
            del self._undo[:-100]
            self._redo.clear()
        return bool(group)

    def undo(self) -> bool:
        self.commit()
        if not self._undo:
            return False
        group = self._undo.pop()
        for d, before, _after in group:
            d.restore(before)
        self._redo.append(group)
        return True

    def redo(self) -> bool:
        self.commit()
        if not self._redo:
            return False
        group = self._redo.pop()
        for d, _before, after in group:
            d.restore(after)
        self._undo.append(group)
        return True

    def dirty_docs(self) -> list[Doc]:
        return [d for d in self._docs.values() if d.dirty]

    def save_all(self) -> list[str]:
        saved = []
        for d in self.dirty_docs():
            d.save()
            saved.append(d.rel)
        self.changed()
        return saved

    # --- maps ---
    def maps(self) -> list[str]:
        mdir = self.data / "maps"
        out = [p.name for p in mdir.iterdir()
               if p.is_dir() and not p.name.lower().endswith(".bak")
               and any((p / n).is_file() for n in ("dynamicscene.xml", "DynamicScene.xml", "triggers.xml"))]
        return sorted(out, key=str.lower)

    def terrain(self, map_name: str):
        if map_name not in self._terrain:
            self._terrain[map_name] = None
            p = self.data / "maps" / map_name / "displace.bin"
            if p.is_file() and p.stat().st_size % 4 == 0:
                buf = array("f")
                with p.open("rb") as fh:
                    buf.fromfile(fh, p.stat().st_size // 4)
                if sys.byteorder != "little":
                    buf.byteswap()
                n = math.isqrt(len(buf))
                if n * n == len(buf) and n > 1:
                    self._terrain[map_name] = (n, buf)
        return self._terrain[map_name]

    def world_size(self, map_name: str) -> float:
        t = self.terrain(map_name)
        return t[0] * CELL if t else 4096.0

    def height(self, map_name: str, x: float, z: float) -> float | None:
        """Terrain height at a point (bilinear); the engine does not snap objects to ground."""
        t = self.terrain(map_name)
        if not t:
            return None
        n, h = t
        fx = min(max(x / CELL, 0.0), n - 1.001)
        fz = min(max(z / CELL, 0.0), n - 1.001)
        ix, iz = int(fx), int(fz)
        tx, tz = fx - ix, fz - iz
        a, b = h[iz * n + ix], h[iz * n + ix + 1]
        c, d = h[(iz + 1) * n + ix], h[(iz + 1) * n + ix + 1]
        return (a * (1 - tx) + b * tx) * (1 - tz) + (c * (1 - tx) + d * tx) * tz

    def map_image_path(self, map_name: str) -> Path | None:
        """Map picture: data/if/map first, then a texture from the map folder."""
        cands = [self.data / "if" / "map" / f"{map_name}.dds"]
        mdir = self.data / "maps" / map_name
        cands += [mdir / "landscape.dds", mdir / f"lightmap_dayTime_{map_name}.dds",
                  mdir / f"lightmap_daytime_{map_name}.dds"]
        for c in cands:
            r = self.root / self.resolve(str(c.relative_to(self.root)).replace("\\", "/"))
            if r.is_file():
                return r
        return None


def load_image(path: Path):
    """DDS/PNG -> PIL RGBA image, or None."""
    try:
        from PIL import Image
        im = Image.open(path)
        im.load()
        return im.convert("RGBA")
    except Exception:
        return None


def fmt(v: float) -> str:
    return f"{v:.3f}"


def parse_vec(s: str) -> list[float]:
    out = []
    for p in s.replace(",", " ").split():
        try:
            out.append(float(p))
        except ValueError:
            out.append(0.0)
    return out

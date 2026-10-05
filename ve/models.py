"""Model library: ids from animmodels.xml, texture lookup, GAM files as saveable documents."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .game import BACKUP_DIR, ENC, Game
from .gam import Gam, GamError

ANIM = "data/models/animmodels.xml"
PROTOS = "data/gamedata/gameobjects"
KIND_TITLES = {"CHASSIS": "шасси", "CABIN": "кабина", "BASKET": "кузов", "WHEEL": "колесо", "SUSP": "подвеска"}
PART_CLASSES = {"Chassis": "CHASSIS", "Cabin": "CABIN", "Basket": "BASKET"}


def _attrs(text: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', text))


def infer_name(names: list[str], n: int) -> str | None:
    """names[i] is a texture of skin i; continue the row to skin n (cab01_0, cab01_1 -> cab01_n)."""
    if not names:
        return None
    if len({x.lower() for x in names}) == 1:
        return names[0]
    for m in re.finditer(r"\d+", names[0]):
        pre, suf, first, width = names[0][:m.start()], names[0][m.end():], int(m.group()), len(m.group())
        pad = width if m.group().startswith("0") and width > 1 else 0
        if all(x.lower() == f"{pre}{first + i:0{pad}d}{suf}".lower() for i, x in enumerate(names)):
            return f"{pre}{first + n:0{pad}d}{suf}"
    return None


class VPart:
    """A unique model of a vehicle: chassis, one of its cabins or baskets, a wheel."""

    def __init__(self, kind: str, model: str, rel: str, protos: list[str], lp: str = ""):
        self.kind, self.model, self.rel, self.protos, self.lp = kind, model, rel, protos, lp
TEXREG = "data/models/modeltextures.xml"


class GamDoc:
    """A GAM file inside Game's document list: dirty flag, undo, save with backup."""

    def __init__(self, game: Game, rel: str):
        self.game, self.rel = game, rel
        self.path = game.root / rel
        self.gam = Gam(self.path)
        self.exists = True
        self._base = self.text()
        self._touched = False
        self._dirty: bool | None = False

    @property
    def dirty(self) -> bool:
        if self._dirty is None:         # comparing serialised sections is not free: once per change
            self._dirty = self.gam.dirty
        return self._dirty

    def touch(self):
        self._touched = True
        self._dirty = None
        self.game.changed()

    def text(self) -> str:
        nb, bb = self.gam.nodes_bytes(), self.gam.limits_bytes()
        return (len(nb).to_bytes(4, "little") + nb + len(bb).to_bytes(4, "little") + bb
                + self.gam.materials_bytes()).decode("latin-1")

    def restore(self, text: str):
        raw = text.encode("latin-1")
        n = int.from_bytes(raw[:4], "little")
        self.gam.set_nodes_bytes(raw[4:4 + n])
        raw = raw[4 + n:]
        n = int.from_bytes(raw[:4], "little")
        self.gam.set_limits_bytes(raw[4:4 + n])
        self.gam.set_materials_bytes(raw[4 + n:])
        self._base = text
        self._touched = False
        self._dirty = None

    def save(self):
        bak = self.game.root / BACKUP_DIR / self.rel
        if not bak.exists():
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, bak)
        try:
            self.gam.save()
        except GamError as e:
            from .game import GameError
            raise GameError(str(e)) from e
        self._dirty = None
        if not self._touched:
            self._base = self.text()


class ModelLib:
    def __init__(self, game: Game):
        self.game = game
        self._models: list[tuple[str, str]] | None = None
        self._actions: dict[str, set[int]] = {}
        self._trans: set[str] = set()
        self._reg: dict[str, str] | None = None
        self._files: dict[str, Path] | None = None
        self._shaders: list[str] | None = None
        self._protos: dict[str, tuple[dict, str]] | None = None
        self._found: dict[tuple, Path | None] = {}
        self._dirs: dict[Path, dict] = {}
        self._folders: dict[Path, tuple] = {}
        self._anim_seen = None

    def _read(self, rel: str) -> str:
        doc = self.game._docs.get(rel.lower())      # an open document may be ahead of the disk
        if doc is not None and hasattr(doc, "tree"):
            return doc.text()
        p = self.game.root / self.game.resolve(rel)
        return p.read_bytes().decode(ENC, errors="replace") if p.is_file() else ""

    # --- models ---
    def models(self) -> list[tuple[str, str]]:
        """(id, file relative to the game root) for every registered GAM model that exists."""
        if self._models is None:
            raw = re.sub(r"<!--.*?-->", "", self._read(ANIM), flags=re.S)
            out = []
            for m in re.finditer(r"<model\b([^>]*)>(.*?)(?=<model\b|</AnimatedModels>|\Z)", raw, re.S):
                head, body = m.group(1), m.group(2)
                mid = re.search(r'\bid="([^"]*)"', head)
                mf = re.search(r'\bfile="([^"]*)"', head)
                if not mid or not mf or not mf.group(1).lower().endswith(".gam"):
                    continue
                rel = self.game.resolve(mf.group(1).replace("\\", "/"))
                rel = self._real(rel)
                if rel is None:
                    continue
                out.append((mid.group(1), rel))
                if re.search(r'\btrans="1"', head):
                    self._trans.add(mid.group(1))
                skins = {int(s) for s in re.findall(r'<action\b[^>]*\bskin="(\d+)"', body)}
                if skins:
                    self._actions[mid.group(1)] = skins
            self._models = out
        return self._models

    def _real(self, rel: str) -> str | None:
        """Path with the case used on disk, or None when the file is missing.
        Folders are listed once and remembered until the next reset."""
        p = self.game.root
        for part in rel.split("/"):
            names = self._dirs.get(p)
            if names is None:
                try:
                    names = {c.name.lower(): c for c in p.iterdir()}
                except OSError:
                    names = {}
                self._dirs[p] = names
            hit = names.get(part.lower())
            if hit is None:
                return None
            p = hit
        return str(p.relative_to(self.game.root)).replace("\\", "/")

    def file_of(self, model_id: str) -> str | None:
        low = model_id.lower()
        return next((f for i, f in self.models() if i.lower() == low), None)

    def ids_of(self, rel: str) -> list[str]:
        return [i for i, f in self.models() if f == rel]

    def transparent(self, model_id: str) -> bool:
        self.models()
        return model_id in self._trans

    def action_skins(self, rel: str) -> set[int]:
        """Skins named by <action skin=...> (wrecked look) of the models that use this file."""
        out: set[int] = set()
        for i in self.ids_of(rel):
            out |= self._actions.get(i, set())
        return out

    def doc(self, rel: str) -> GamDoc:
        key = rel.lower()
        docs = self.game._docs
        if key not in docs:
            docs[key] = GamDoc(self.game, rel)
        return docs[key]

    def shift_action_skins(self, rel: str, at: int, delta: int):
        """A skin was inserted (+1) or removed (-1) at index `at`: keep <action skin> pointing at the same look."""
        ids = {i.lower() for i in self.ids_of(rel) if self._actions.get(i)}
        if not ids:
            return
        d = self.game.doc(ANIM)
        changed = False
        for m in d.root.elements("model"):
            if m.get("id").lower() not in ids:
                continue
            for a in m.elements("action"):
                s = a.get("skin")
                if s.isdigit() and int(s) >= at + (1 if delta < 0 else 0):
                    a.set("skin", str(max(int(s) + delta, 0)))
                    changed = True
        if changed:
            d.touch()
            for i in self.ids_of(rel):
                if i in self._actions:
                    self._actions[i] = {s + delta if s >= at + (1 if delta < 0 else 0) else s for s in self._actions[i]}

    # --- vehicles ---
    def protos(self) -> dict[str, tuple[dict, str]]:
        """Game object prototypes: name -> (attributes, inner XML)."""
        if self._protos is None:
            self._protos = {}
            folder = self.game.root / PROTOS
            for f in sorted(folder.glob("*.xml")) if folder.is_dir() else []:
                raw = re.sub(r"<!--.*?-->", "", f.read_bytes().decode(ENC, errors="replace"), flags=re.S)
                for m in re.finditer(r"<Prototype\b([^>]*?)(?:/>|>(.*?)</Prototype>)", raw, re.S):
                    a = _attrs(m.group(1))
                    if a.get("Name"):
                        self._protos.setdefault(a["Name"], (a, m.group(2) or ""))
        return self._protos

    def vehicles(self) -> list[str]:
        """Vehicle prototypes that describe their parts by resource type."""
        return sorted((n for n, (a, body) in self.protos().items()
                       if a.get("Class") == "Vehicle" and "MainPartDescription" in body), key=str.lower)

    def vehicle_parts(self, name: str) -> list[VPart]:
        """Every distinct model that can stand on this vehicle, by the resource types of its slots."""
        a, body = self.protos().get(name, ({}, ""))
        out: list[VPart] = []
        seen: dict[str, VPart] = {}

        def add(kind, proto, lp="", attr="ModelFile"):
            pa = self.protos().get(proto, ({}, ""))[0]
            rel = self.file_of(pa.get(attr, ""))
            if rel is None:
                return
            if rel in seen:
                if proto not in seen[rel].protos:
                    seen[rel].protos.append(proto)
                return
            seen[rel] = VPart(kind, pa[attr], rel, [proto], lp)
            out.append(seen[rel])
        for d in re.findall(r"<(?:Main)?PartDescription\b([^>]*)>", body):
            da = _attrs(d)
            rt = da.get("partResourceType", "")
            for pn, (pa, _b) in self.protos().items():
                if pa.get("ResourceType") == rt and pa.get("Class") in PART_CLASSES and pa.get("ModelFile"):
                    add(PART_CLASSES[pa["Class"]], pn, da.get("lpName", ""))
        for w in re.findall(r"<Wheel\b([^>]*)>", body):
            add("WHEEL", _attrs(w).get("Prototype", ""))
        for w in re.findall(r"<Wheel\b([^>]*)>", body):
            add("SUSP", _attrs(w).get("Prototype", ""), attr="SuspensionModelFile")
        order = {"CHASSIS": 0, "CABIN": 1, "BASKET": 2, "WHEEL": 3, "SUSP": 4}
        return sorted(out, key=lambda q: order[q.kind])

    def vehicle_wheels(self, name: str) -> list[tuple[str | None, str | None]]:
        """(wheel file, suspension file) of every wheel in prototype order."""
        body = self.protos().get(name, ({}, ""))[1]
        out = []
        for w in re.findall(r"<Wheel\b([^>]*)>", body):
            pa = self.protos().get(_attrs(w).get("Prototype", ""), ({}, ""))[0]
            out.append((self.file_of(pa.get("ModelFile", "")), self.file_of(pa.get("SuspensionModelFile", ""))))
        return out

    def dead_skins(self, rel: str, gam: Gam) -> set[int]:
        """The wrecked-look skin: the last one, when an action names it or its textures say so.
        (Actions of some models point at stale numbers, so they alone prove nothing.)"""
        last = len(gam.skins) - 1
        if last < 1:
            return set()
        if last in self.action_skins(rel):
            return {last}
        names = [m.tex(0).lower().rsplit(".", 1)[0] for m in gam.skins[last] if m.tex(0)]
        prev = {m.tex(0).lower().rsplit(".", 1)[0] for m in gam.skins[last - 1] if m.tex(0)}
        if names and all("dead" in n or (n.endswith("d") and n not in prev) for n in names):
            return {last}
        return set()

    def live_count(self, rel: str, gam: Gam) -> int:
        return len(gam.skins) - len(self.dead_skins(rel, gam))

    def extend_skins(self, rel: str, target: int, ref: tuple[str, Gam] | None = None) -> tuple[int, int]:
        """Add skins until the model has `target` of them. New skins go before the wrecked one and
        continue the numbering of the existing textures; returns (added, textures that had no file to continue with)."""
        doc = self.doc(rel)
        g = doc.gam
        added = guessed = 0
        while len(g.skins) < target:
            dead = self.dead_skins(rel, g)
            live = [i for i in range(len(g.skins)) if i not in dead]
            n = len(live)
            wrecked = {t.name.lower() for i in dead for m in g.skins[i] for t in m.textures}
            new = [m.clone() for m in g.skins[live[-1]]]
            for mi, mat in enumerate(new):
                for ti, tex in enumerate(mat.textures):
                    row = [g.skins[i][mi].textures[ti].name for i in live
                           if ti < len(g.skins[i][mi].textures)]
                    name = infer_name(row, n) if len(row) == len(live) else None
                    if name and name.lower() != tex.name.lower() and (
                            self.find_texture(rel, name) is None or name.lower() in wrecked):
                        name = None         # no such file, or the numbering ran into the wrecked texture
                    if name is None and ref is not None and tex.type == 0:
                        name = self._paint_of(ref, n, mat.shader, rel)
                    if name is None:
                        if len({x.lower() for x in row}) > 1:
                            guessed += 1
                        continue
                    if name != tex.name:
                        tex.name, tex.raw = name, b""
            at = min(dead) if dead and min(dead) > live[-1] else len(g.skins)
            g.skins.insert(at, new)
            self.shift_action_skins(rel, at, +1)
            added += 1
        if added:
            doc.touch()
        return added, guessed

    def _paint_of(self, ref: tuple[str, Gam], n: int, shader: str, rel: str) -> str | None:
        """Shared paint texture (color5.dds) that the reference part uses in skin n."""
        ref_rel, rg = ref
        dead = self.dead_skins(ref_rel, rg)
        live = [i for i in range(len(rg.skins)) if i not in dead]
        if n >= len(live):
            return None
        for m in rg.skins[live[n]]:
            name = m.tex(0)
            if m.shader.lower() == shader.lower() and name and self.find_texture(rel, name) is not None \
                    and (self.game.root / rel).parent != self.find_texture(rel, name).parent:
                return name
        return None

    def reset(self):
        self._protos = None
        self._models = None
        self._actions, self._trans = {}, set()
        self._files = None
        self._found = {}
        self._dirs = {}

    def refresh(self):
        """Cheap catch-up after undo/redo/save: texture lookups are redone, and the model list is
        re-read only when animmodels.xml is open as a document (skin numbers of actions were shifted)."""
        self._found = {}
        self._dirs = {}
        doc = self.game._docs.get(ANIM)
        state = doc.text() if doc is not None and hasattr(doc, "tree") else None
        if state != self._anim_seen:
            self._anim_seen = state
            self._models = None
            self._actions, self._trans = {}, set()

    # --- textures ---
    def _registry(self) -> dict[str, str]:
        if self._reg is None:
            self._reg = {}
            for name, path in re.findall(r'<file\s+name="([^"]+)"\s+path="([^"]+)"', self._read(TEXREG)):
                self._reg.setdefault(name.lower(), path.replace("\\", "/"))
        return self._reg

    def _all_files(self) -> dict[str, Path]:
        if self._files is None:
            self._files = {}
            for p in (self.game.data / "models").rglob("*"):
                if p.suffix.lower() in (".dds", ".tga"):
                    self._files.setdefault(p.name.lower(), p)
        return self._files

    def find_texture(self, model_rel: str, name: str) -> Path | None:
        """Texture file by name: the model's folder, then modeltextures.xml, then anywhere in data/models."""
        if not name:
            return None
        key = (model_rel.rsplit("/", 1)[0], name.lower())
        if key not in self._found:
            self._found[key] = self._find_texture(model_rel, name)
        return self._found[key]

    def _folder(self, folder: Path) -> dict[str, Path]:
        """Files of a folder by lower-case name; re-listed when the folder changes."""
        try:
            stamp = folder.stat().st_mtime_ns
        except OSError:
            return {}
        hit = self._folders.get(folder)
        if hit is None or hit[0] != stamp:
            hit = (stamp, {c.name.lower(): c for c in folder.iterdir()})
            self._folders[folder] = hit
        return hit[1]

    def _find_texture(self, model_rel: str, name: str) -> Path | None:
        low = name.lower()
        folder = (self.game.root / model_rel).parent
        if folder.is_dir():
            hit = self._folder(folder).get(low)
            if hit is not None:
                return hit
        reg = self._registry().get(low)
        if reg:
            p = self.game.root / reg
            if p.is_file():
                return p
            real = self._real(reg)
            if real:
                return self.game.root / real
        return self._all_files().get(low)

    def texture_choices(self, model_rel: str) -> list[str]:
        folder = (self.game.root / model_rel).parent
        own = sorted((c.name for c in folder.iterdir() if c.suffix.lower() in (".dds", ".tga")), key=str.lower) \
            if folder.is_dir() else []
        seen = {n.lower() for n in own}
        return own + sorted(n for n in self._registry() if n not in seen)

    def shaders(self) -> list[str]:
        """Shader names used by the game's models, most common first."""
        if self._shaders is None:
            count: dict[str, int] = {}
            for rel in dict.fromkeys(f for _i, f in self.models()):
                try:
                    g = Gam(self.game.root / rel)
                except (GamError, OSError, ValueError, IndexError):
                    continue
                for m in g.skins[0] if g.skins else []:
                    count[m.shader] = count.get(m.shader, 0) + 1
            self._shaders = sorted(count, key=lambda k: -count[k])
        return self._shaders

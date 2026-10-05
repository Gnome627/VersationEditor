"""Old vehicle materials (bumpdiffuse_envalphagloss_spec) -> the Skinned shader of Community Remaster.

Image maths follows the author's SkinnedConverter (green_mask.py, orig_to_skinned.py), done with
Pillow only so that every slider can be previewed live.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import gam as G
from .game import BACKUP_DIR, load_image

OLD_SHADER = "bumpdiffuse_envalphagloss_spec"
NEW_SHADER = "Skinned"
# paint of skins 0..15 on the vehicles Community Remaster has already converted
PAINTS = ["color1.dds", "color2.dds", "color3.dds", "color4.dds", "color5.dds", "camo1.dds", "camo2.dds", "camo3.dds",
          "camo4.dds", "color6.dds", "color7.dds", "color8.dds", "color9.dds", "color10.dds", "color11.dds",
          "color12.dds"]


@dataclass
class MaskParams:
    hue_lo: float = 0.18        # green range of the hue circle, 0..1
    hue_hi: float = 0.58
    sat_pow: float = 1.2        # softens weakly saturated paint
    gain: float = 3.0
    blur: float = 0.3


@dataclass
class SetParams:
    gray_pow: float = 1.2       # darkness of the grey that replaces paint in the detail texture
    rough: float = 0.5          # Roughness coefficient: lightmap R from inverted bump alpha
    metal: float = 1.25         # Metallic coefficient: lightmap G from diffuse alpha
    ao: float = 1.0             # strength of the baked ambient occlusion in lightmap B (0 = none)


@dataclass
class Job:
    """One texture set to convert, or an already converted one to tune again."""
    rel: str                    # a model that uses the set (textures are looked up from its folder)
    model: str
    mat: int
    title: str                  # stem of the produced files: <title>_detail.dds, <title>_lightmap.dds
    checked: bool = True
    retune: bool = False        # the materials are on Skinned already: only the textures are rewritten
    choices: list = field(default_factory=list)     # old diffuse textures the set can be made from
    green: str = ""             # diffuse with the green paint, the mask is taken from it
    base: str = ""              # diffuse the detail texture is made from
    bump: str = ""
    mask_file: str = ""         # ready mask (M113 camouflage mask) instead of the green one
    mask: MaskParams = field(default_factory=MaskParams)
    set: SetParams = field(default_factory=SetParams)


def sidecar(lib, job: Job) -> Path:
    """Settings of a converted set are kept next to its textures, so that it can be tuned again."""
    return (lib.game.root / job.rel).parent / f"{job.title}_skinned.json"


def save_settings(lib, job: Job):
    data = {"green": job.green, "base": job.base, "bump": job.bump, "mask_file": job.mask_file,
            "mask": asdict(job.mask), "set": asdict(job.set)}
    sidecar(lib, job).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def load_settings(lib, job: Job) -> bool:
    try:
        data = json.loads(sidecar(lib, job).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for key in ("green", "base", "bump", "mask_file"):
        if isinstance(data.get(key), str) and data[key]:
            setattr(job, key, data[key])
    for obj, key in ((job.mask, "mask"), (job.set, "set")):
        for k, v in (data.get(key) or {}).items():
            if hasattr(obj, k) and isinstance(v, (int, float)):
                setattr(obj, k, float(v))
    return True


def is_old(shader: str) -> bool:
    return shader.lower() == OLD_SHADER


def old_materials(gam: G.Gam) -> list[int]:
    return [i for i, m in enumerate(gam.skins[0])
            if is_old(m.shader) and m.tex(G.TEX_DIFFUSE) and m.tex(G.TEX_BUMP)] if gam.skins else []


def _lut(fn):
    return [min(max(int(round(fn(i / 255.0) * 255.0)), 0), 255) for i in range(256)]


def green_mask(diffuse, p: MaskParams):
    """Where the green paint is: saturation * value inside the green hue range."""
    from PIL import ImageChops, ImageFilter
    h, s, v = diffuse.convert("RGB").convert("HSV").split()
    green = h.point([255 if p.hue_lo < i / 255.0 < p.hue_hi else 0 for i in range(256)])
    m = ImageChops.multiply(s.point(_lut(lambda x: x ** p.sat_pow)), v)
    m = ImageChops.multiply(m.point(_lut(lambda x: x * p.gain)), green)
    return m.filter(ImageFilter.GaussianBlur(p.blur)) if p.blur > 0.01 else m


def file_mask(path: Path, size):
    """A ready mask: the brightest channel, fitted to the diffuse."""
    from PIL import Image, ImageChops
    im = load_image(path)
    if im is None:
        return None
    r, g, b, _a = im.split()
    m = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return m if m.size == size else m.resize(size, Image.LANCZOS)


def detail_texture(diffuse, mask, p: SetParams):
    """Detail slot: the old diffuse with paint replaced by grey; alpha says where paint shows."""
    from PIL import Image
    rgb = diffuse.convert("RGB")
    gray = rgb.convert("L").point(_lut(lambda x: x ** p.gray_pow))
    out = Image.composite(Image.merge("RGB", (gray, gray, gray)), rgb, mask)
    out.putalpha(mask)
    return out


def lightmap_texture(diffuse, bump, p: SetParams, ao=None):
    """Lightmap slot: R specular from inverted bump alpha, G reflection from diffuse alpha,
    B ambient occlusion baked from the model's geometry (white when there is none)."""
    from PIL import Image
    ba = bump.getchannel("A")
    if ba.size != diffuse.size:
        ba = ba.resize(diffuse.size, Image.BILINEAR)
    r = ba.point(_lut(lambda x: (1.0 - x) * p.rough))
    g = diffuse.getchannel("A").point(_lut(lambda x: x * p.metal))
    full = Image.new("L", diffuse.size, 255)
    b = full
    if ao is not None and p.ao > 0.001:
        if ao.size != diffuse.size:
            ao = ao.resize(diffuse.size, Image.BILINEAR)
        b = ao.point(_lut(lambda x: 1.0 - (1.0 - x) * p.ao))
    return Image.merge("RGBA", (r, g, b, full))


def greenest(lib, rel: str, names: list[str]) -> str:
    """The texture with the most green paint. The one numbered 4 is the usual green skin and wins when
    it is close to the best: teal and yellow skins also score inside the green range."""
    scores = {}
    for name in dict.fromkeys(names):
        path = lib.find_texture(rel, name)
        im = load_image(path) if path else None
        if im is not None:
            scores[name] = sum(green_mask(im.resize((64, 64)), MaskParams(blur=0)).getdata())
    if not scores:
        return names[0] if names else ""
    best = max(scores, key=scores.get)
    four = next((n for n in scores if re.search(r"_0*4\.\w+$", n)), None)
    if four and scores[four] >= scores[best] * 0.5 > 0:
        return four
    return best


def old_choices(lib, rel: str, gam: G.Gam, mat: int) -> list[str]:
    """Diffuse textures of the ordinary skins of an old material."""
    dead = lib.dead_skins(rel, gam)
    return list(dict.fromkeys(s[mat].tex(G.TEX_DIFFUSE) for i, s in enumerate(gam.skins)
                              if is_old(s[mat].shader) and i not in dead and s[mat].tex(G.TEX_DIFFUSE)))


def retunable(lib, rel: str, gam: G.Gam) -> list[tuple[int, str, list[str], str]]:
    """Materials already on Skinned whose old textures are still next to the model:
    (material, stem, old diffuse textures, bump)."""
    out = []
    folder = (lib.game.root / rel).parent
    for mi, m in enumerate(gam.skins[0] if gam.skins else []):
        det = m.tex(G.TEX_DETAIL)
        hit = re.fullmatch(r"(.+)_detail\.\w+", det, re.I)
        if m.shader.lower() != NEW_SHADER.lower() or not hit or not m.tex(G.TEX_BUMP):
            continue
        stem = hit.group(1)
        job = Job(rel, "", mi, stem)
        names = []
        if load_settings(lib, job):
            names = [n for n in dict.fromkeys([job.green, job.base]) if n]
        pat = re.compile(re.escape(stem) + r"_?\d+\.(dds|tga)", re.I)
        names += sorted(c.name for c in lib._folder(folder).values() if pat.fullmatch(c.name) and c.name not in names)
        names = [n for n in names if lib.find_texture(rel, n) is not None]
        if names:
            out.append((mi, stem, names, m.tex(G.TEX_BUMP)))
    return out


def paintable(gam: G.Gam, mat: int, dead=()) -> bool:
    """The diffuse differs between the ordinary skins: the material carries paint."""
    return len({s[mat].tex(G.TEX_DIFFUSE).lower() for i, s in enumerate(gam.skins)
                if is_old(s[mat].shader) and i not in dead}) > 1


def base_name(gam: G.Gam, mat: int) -> str:
    """cab01_0.dds -> cab01: stem of the produced files."""
    stem = Path(gam.skins[0][mat].tex(G.TEX_DIFFUSE)).stem
    return (re.sub(r"_\d+$", "", stem) or stem)[:G.NAME_MAX - len("_lightmap.dds")]


class Source:
    """Loaded textures of a job, cached while the wizard is open."""

    def __init__(self, lib):
        self.lib = lib
        self._img: dict[Path, object] = {}

    def image(self, rel: str, name: str):
        path = self.lib.find_texture(rel, name)
        if path is None:
            return None
        if path not in self._img:
            self._img[path] = load_image(path)
        return self._img[path]

    def build(self, job: Job, limit: int = 0, ao=None):
        """(mask, detail, lightmap) of a job, or None when a source texture is missing.
        `limit` caps the size for the live preview."""
        from PIL import Image
        diffuse = self.image(job.rel, job.base)
        bump = self.image(job.rel, job.bump)
        if diffuse is None or bump is None:
            return None
        size = diffuse.size
        if limit and max(size) > limit:
            size = (max(size[0] * limit // max(size), 1), max(size[1] * limit // max(size), 1))
            diffuse = diffuse.resize(size, Image.BILINEAR)
        if job.mask_file:
            mask = file_mask(Path(job.mask_file), size)
        else:
            green = self.image(job.rel, job.green)
            if green is not None and green.size != size:
                green = green.resize(size, Image.BILINEAR)
            mask = green_mask(green, job.mask) if green is not None else None
        if mask is None:
            mask = Image.new("L", size, 0)
        return mask, detail_texture(diffuse, mask, job.set), lightmap_texture(diffuse, bump, job.set, ao)


def new_material(old: G.Material, paint: str, detail: str, lightmap: str) -> G.Material:
    """The material on the Skinned shader: paint, the same bump and cube map, new lightmap and detail."""
    m = old.clone()
    m.shader, m.raw = NEW_SHADER, b""
    m.textures = [G.Texture(paint, 0, G.TEX_DIFFUSE), G.Texture(old.tex(G.TEX_BUMP), 0, G.TEX_BUMP),
                  G.Texture(lightmap, 0, G.TEX_PARAMS), G.Texture(old.tex(G.TEX_CUBE) or "lobbycube.dds", 0, G.TEX_CUBE),
                  G.Texture(detail, 0, G.TEX_DETAIL)]
    return m


def full_skins(lib, rel: str) -> int:
    """Bring a model to a skin per paint plus the wrecked one. Skinned materials get the standard
    row of paints over all ordinary skins; other materials are simply repeated. Returns skins added."""
    doc = lib.doc(rel)
    g = doc.gam
    want = len(PAINTS) + 1
    if not 1 < len(g.skins) < want:
        return 0
    added = lib.extend_skins(rel, want)[0]
    dead = lib.dead_skins(rel, g)
    live = [i for i in range(len(g.skins)) if i not in dead]
    known = {p.lower() for p in PAINTS}
    for n, i in enumerate(live):
        for m in g.skins[i]:
            tex = next((t for t in m.textures if t.type == G.TEX_DIFFUSE), None)
            if m.shader.lower() == NEW_SHADER.lower() and tex is not None and tex.name.lower() in known:
                tex.name, tex.raw = PAINTS[n % len(PAINTS)], b""
    if added:
        doc.touch()
    return added


def has_skinned(gam: G.Gam) -> bool:
    return any(m.shader.lower() == NEW_SHADER.lower() for m in gam.skins[0]) if gam.skins else False


def convert(lib, jobs: list[Job], source: Source | None = None, targets: dict | None = None,
            ao=None) -> tuple[int, list[str]]:
    """Write the textures of the checked jobs and switch their materials to Skinned in every skin
    that still uses the old shader. Returns (materials converted, problems)."""
    source = source or Source(lib)
    done, problems, written = 0, [], {}
    for n, job in enumerate(jobs):
        if not job.checked:
            continue
        g = lib.doc(job.rel).gam
        built = source.build(job, ao=ao(n) if ao else None)
        if built is None:
            problems.append(f"{job.model}: нет исходной текстуры материала {job.title}")
            continue
        _mask, detail, lightmap = built
        folder = (lib.game.root / job.rel).parent
        names = (f"{job.title}_detail.dds", f"{job.title}_lightmap.dds")
        if job.retune:                              # keep the names the materials already use
            old = g.skins[0][job.mat]
            names = (old.tex(G.TEX_DETAIL) or names[0], old.tex(G.TEX_PARAMS) or names[1])
        for name, im in zip(names, (detail, lightmap)):
            path = folder / name
            if path in written:                     # the same texture set is shared by several models
                continue
            if path.exists():
                bak = lib.game.root / BACKUP_DIR / path.relative_to(lib.game.root)
                if not bak.exists():
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, bak)
            im.save(path, format="DDS")
            written[path] = True
        save_settings(lib, job)
        if job.retune:
            done += 1
            continue
        for rel, _model, mat in (targets or {}).get(n) or [(job.rel, job.model, job.mat)]:
            doc = lib.doc(rel)
            live = 0
            dead = lib.dead_skins(rel, doc.gam)
            for i, skin in enumerate(doc.gam.skins):
                m = skin[mat]
                if i in dead:               # the wrecked look keeps its own texture
                    continue
                if is_old(m.shader):
                    skin[mat] = new_material(m, PAINTS[live % len(PAINTS)], *names)
                live += 1
            doc.touch()
        done += 1
    lib.refresh()
    return done, problems

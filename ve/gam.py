"""GAM model files: geometry for the viewer, skins read and rewritten in place.

Layout follows ThePlain/HTAToolchain (htaparser.py). Only the materials section is ever
rebuilt; every other byte of the file is copied through.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

ENC = "cp1251"
INFO, NODES, MESHES, MATERIALS, BOUNDS, GROUPS = 0x1, 0x2, 0x4, 0xF, 0x80, 0xF0
TEX_DIFFUSE, TEX_BUMP, TEX_PARAMS, TEX_CUBE, TEX_DETAIL = 0, 1, 2, 3, 4
TEX_TITLES = {0: "Diffuse", 1: "Bump", 2: "Lightmap", 3: "Cubemap", 4: "Detail"}     # slots as the shaders name them
NODE_SIZE = 136
NAME_MAX, SHADER_MAX = 39, 99

# vertex type -> (normal offset, uv offset); position is always first
VERTEX = {0: (None, None), 1: (None, 12), 2: (None, None), 3: (None, None), 4: (None, 20), 5: (12, None),
          6: (None, 16), 7: (12, 24), 8: (12, 28), 9: (12, 28), 10: (12, 24), 11: (12, 24), 12: (None, 16),
          13: (None, 16), 14: (None, 16), 15: (12, 24), 16: (12, 28)}


class GamError(Exception):
    pass


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode(ENC, errors="replace")


def _field(text: str, raw: bytes, limit: int) -> bytes:
    """Fixed-size string field; the original bytes (with their padding junk) if unchanged."""
    return raw if raw and _cstr(raw) == text else text.encode(ENC, errors="replace")[:limit]


@dataclass
class Texture:
    name: str = ""
    uv: int = 0
    type: int = 0
    raw: bytes = b""


@dataclass
class Material:
    diffuse: tuple = (1.0, 1.0, 1.0, 1.0)
    ambient: tuple = (1.0, 1.0, 1.0, 1.0)
    specular: tuple = (1.0, 1.0, 1.0, 1.0)
    emissive: tuple = (0.0, 0.0, 0.0, 1.0)
    power: float = 1.0
    shader: str = "diffuse"
    textures: list[Texture] = field(default_factory=list)
    raw: bytes = b""

    def clone(self) -> "Material":
        return Material(self.diffuse, self.ambient, self.specular, self.emissive, self.power, self.shader,
                        [Texture(t.name, t.uv, t.type, t.raw) for t in self.textures], self.raw)

    def tex(self, type_: int) -> str:
        return next((t.name for t in self.textures if t.type == type_), "")


@dataclass
class Node:
    name: str
    parent: int
    loc: tuple          # position in the parent's frame
    rot: tuple          # quaternion x y z w in the parent's frame
    matrix: tuple       # world-to-node, row vectors; its 3x3 part R = R(parent) * matrix(rot)


@dataclass
class Mesh:
    name: str
    type: int
    parent: int
    group: int
    material: int
    tris: list        # flat floats per triangle corner: x y z nx ny nz u v (model space)


@dataclass
class Group:
    name: str
    lo: int
    hi: int
    nodes: list[int]
    variants: list[list[int]]     # node indices visible in each variant


class Gam:
    def __init__(self, path: Path, data: bytes | None = None):
        self.path = Path(path)
        self.data = self.path.read_bytes() if data is None else data
        d = self.data
        if len(d) < 12:
            raise GamError("файл слишком короткий")
        n = struct.unpack_from("<I", d, 8)[0]
        if n > 64 or 12 + n * 16 > len(d):
            raise GamError("это не GAM-модель")
        self.sections: list[list[int]] = [list(struct.unpack_from("<IIQ", d, 12 + i * 16)) for i in range(n)]
        if not self._has(INFO) or not self._has(MATERIALS):
            raise GamError("в файле нет секции материалов")
        o = self._off(INFO)
        (self.n_tri, self.n_skinned, self.n_static, self.n_anims, self.n_mats, self.n_nodes,
         self.n_config) = struct.unpack_from("<6hi", d, o)
        self.skins: list[list[Material]] = self._parse_skins(d, self._off(MATERIALS))
        self.groups: list[Group] = self._read_groups()
        self._nodes: list[Node] | None = None
        self._saved = self.materials_bytes()
        self._saved_nodes = self.nodes_bytes()
        self.limits: dict[int, list[float]] = self._read_limits()   # node -> min x y z, max x y z (radians)
        self._saved_limits = self.limits_bytes()
        self._meshes: list[Mesh] | None = None

    # --- sections ---
    def _has(self, tag: int) -> bool:
        return any(s[0] == tag for s in self.sections)

    def _off(self, tag: int) -> int:
        return next(s[2] for s in self.sections if s[0] == tag)

    def _parse_skins(self, d: bytes, o: int) -> list[list[Material]]:
        count = struct.unpack_from("<I", d, o)[0]
        o += 4
        skins = []
        for _ in range(count):
            skin = []
            for _ in range(self.n_mats):
                v = struct.unpack_from("<17fI100s", d, o)
                o += 172
                m = Material(v[0:4], v[4:8], v[8:12], v[12:16], v[16], _cstr(v[18]), raw=v[18])
                for _ in range(v[17]):
                    name, uv, type_ = struct.unpack_from("<40sII", d, o)
                    o += 48
                    m.textures.append(Texture(_cstr(name), uv, type_, name))
                skin.append(m)
            skins.append(skin)
        return skins

    def _read_groups(self) -> list[Group]:
        if not self._has(GROUPS):
            return []
        d, o = self.data, self._off(GROUPS)
        out = []
        count = struct.unpack_from("<I", d, o)[0]
        o += 4
        for _ in range(count):
            name, lo, hi, n = struct.unpack_from("<20sIII", d, o)
            o += 32
            nodes = list(struct.unpack_from(f"<{n}I", d, o))
            o += 4 * n
            nv = struct.unpack_from("<I", d, o)[0]
            o += 4
            variants = []
            for _ in range(nv):
                k = struct.unpack_from("<I", d, o)[0]
                o += 4
                variants.append([nodes[i] for i in struct.unpack_from(f"<{k}I", d, o) if i < n])
                o += 4 * k
            out.append(Group(_cstr(name), lo, hi, nodes, variants))
        return out

    def _read_limits(self) -> dict[int, list[float]]:
        if not self._has(BOUNDS):
            return {}
        d, o = self.data, self._off(BOUNDS)
        out = {}
        for i in range(struct.unpack_from("<I", d, o)[0]):
            node, *v = struct.unpack_from("<I6f", d, o + 4 + i * 28)
            out[node] = list(v)
        return out

    def limits_bytes(self) -> bytes:
        return struct.pack("<I", len(self.limits)) + b"".join(struct.pack("<I6f", n, *v) for n, v in self.limits.items())

    def set_limits_bytes(self, blob: bytes):
        self.limits = {}
        for i in range(struct.unpack_from("<I", blob, 0)[0] if len(blob) >= 4 else 0):
            node, *v = struct.unpack_from("<I6f", blob, 4 + i * 28)
            self.limits[node] = list(v)

    # --- geometry (lazy: scanning skins of many files must stay cheap) ---
    @property
    def nodes(self) -> list[Node]:
        if self._nodes is None:
            d, o = self.data, self._off(NODES) if self._has(NODES) else 0
            self._nodes = []
            for i in range(self.n_nodes if o else 0):
                name, parent = struct.unpack_from("<40si", d, o + i * NODE_SIZE)
                self._nodes.append(Node(_cstr(name), parent, struct.unpack_from("<3f", d, o + i * NODE_SIZE + 44),
                                        struct.unpack_from("<4f", d, o + i * NODE_SIZE + 56),
                                        struct.unpack_from("<16f", d, o + i * NODE_SIZE + 72)))
        return self._nodes

    # --- load points ---
    def load_points(self) -> list[int]:
        """Indices of the nodes other models and effects are attached to (LP_*)."""
        return [i for i, n in enumerate(self.nodes) if n.name.upper().startswith("LP_")]

    def node_pos(self, i: int) -> tuple[float, float, float]:
        """Model-space position of a node: the origin taken back through its world-to-node matrix."""
        m = self.nodes[i].matrix
        return tuple(-(m[12] * m[k * 4] + m[13] * m[k * 4 + 1] + m[14] * m[k * 4 + 2]) for k in range(3))

    def move_node(self, i: int, pos):
        """Put a node at a model-space position; its rotation, local offset and matrix stay consistent."""
        n = self.nodes[i]
        old = self.node_pos(i)
        d = [pos[k] - old[k] for k in range(3)]
        m = list(n.matrix)
        for j in range(3):
            m[12 + j] = -(pos[0] * m[j] + pos[1] * m[4 + j] + pos[2] * m[8 + j])
        if 0 <= n.parent < len(self.nodes):          # the offset is stored in the parent's frame
            pm = self.nodes[n.parent].matrix
            d = [d[0] * pm[j] + d[1] * pm[4 + j] + d[2] * pm[8 + j] for j in range(3)]
        n.loc = tuple(n.loc[k] + d[k] for k in range(3))
        n.matrix = tuple(m)

    def _rot3(self, i: int) -> list[list[float]]:
        m = self.nodes[i].matrix
        return [[m[r * 4 + c] for c in range(3)] for r in range(3)]

    def node_axes(self, i: int) -> list[tuple]:
        """The node's own X, Y, Z directions in model space (columns of R)."""
        r = self._rot3(i)
        return [(r[0][k], r[1][k], r[2][k]) for k in range(3)]

    def node_euler(self, i: int) -> tuple[float, float, float]:
        """Orientation as angles in degrees: turn about X, then Y, then Z of the model."""
        r = self._rot3(i)
        sy = min(max(-r[2][0], -1.0), 1.0)
        if abs(sy) > 0.99999:               # looking straight along Y's pole: Z is folded into X
            return math.degrees(math.atan2(-r[1][2], r[1][1])), math.degrees(math.asin(sy)), 0.0
        return (math.degrees(math.atan2(r[2][1], r[2][2])), math.degrees(math.asin(sy)),
                math.degrees(math.atan2(r[1][0], r[0][0])))

    def set_node_euler(self, i: int, deg):
        x, y, z = (math.radians(v) for v in deg)
        cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
        self._set_rot3(i, [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
                           [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
                           [-sy, cy * sx, cy * cx]])

    def rotate_node(self, i: int, axis: int, deg: float):
        """Turn a node about a model axis through its own position."""
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        a, b = (axis + 1) % 3, (axis + 2) % 3
        q = [[1.0 if r == k else 0.0 for k in range(3)] for r in range(3)]
        q[a][a], q[a][b], q[b][a], q[b][b] = c, -s, s, c
        r = self._rot3(i)
        self._set_rot3(i, [[sum(q[x][k] * r[k][y] for k in range(3)) for y in range(3)] for x in range(3)])

    def _set_rot3(self, i: int, r: list[list[float]]):
        """New orientation at the same position: matrix, its translation and the local quaternion."""
        n = self.nodes[i]
        pos = self.node_pos(i)
        m = list(n.matrix)
        for x in range(3):
            for y in range(3):
                m[x * 4 + y] = r[x][y]
        for j in range(3):
            m[12 + j] = -(pos[0] * m[j] + pos[1] * m[4 + j] + pos[2] * m[8 + j])
        n.matrix = tuple(m)
        loc = r
        if 0 <= n.parent < len(self.nodes):          # local = parent^T * R
            p = self._rot3(n.parent)
            loc = [[sum(p[k][x] * r[k][y] for k in range(3)) for y in range(3)] for x in range(3)]
        q = _quat(loc)
        if sum(a * b for a, b in zip(q, n.rot)) < 0:  # same rotation, the sign the file already uses
            q = tuple(-v for v in q)
        n.rot = q

    def nodes_bytes(self) -> bytes:
        """The nodes section with current positions; untouched nodes keep their bytes."""
        if not self._has(NODES):
            return b""
        o = self._off(NODES)
        size = self.n_nodes * NODE_SIZE
        if self._nodes is None:
            return self.data[o:o + size]
        out = bytearray(self.data[o:o + size])
        for i, n in enumerate(self._nodes):
            p = i * NODE_SIZE
            if struct.unpack_from("<7f16f", out, p + 44) != (*n.loc, *n.rot, *n.matrix):
                struct.pack_into("<7f16f", out, p + 44, *n.loc, *n.rot, *n.matrix)
        return bytes(out)

    def set_nodes_bytes(self, blob: bytes):
        """Undo/redo: node positions from a previously serialised section."""
        for i, n in enumerate(self.nodes):
            if (i + 1) * NODE_SIZE <= len(blob):
                n.loc = struct.unpack_from("<3f", blob, i * NODE_SIZE + 44)
                n.rot = struct.unpack_from("<4f", blob, i * NODE_SIZE + 56)
                n.matrix = struct.unpack_from("<16f", blob, i * NODE_SIZE + 72)

    @property
    def meshes(self) -> list[Mesh]:
        if self._meshes is None:
            self._meshes = self._read_meshes() if self._has(MESHES) else []
        return self._meshes

    def _read_meshes(self) -> list[Mesh]:
        d, o = self.data, self._off(MESHES)     # vertices are stored in model space already
        out = []
        for _ in range(self.n_tri + self.n_skinned + self.n_static):
            name, type_, parent, group, material, vsize, vtype, vcount, icount = struct.unpack_from("<40s4i4I", d, o)
            o += 72
            if vtype not in VERTEX or o + vsize * vcount > len(d):
                raise GamError("неизвестный формат вершин")
            n_off, uv_off = VERTEX[vtype]
            base = o
            o += vsize * vcount * (2 if type_ == 1 else 1)
            if type_ == 2:
                o += 122 * vcount
            idx = struct.unpack_from(f"<{icount * 3}H", d, o)
            o += icount * 6
            verts = []
            for i in range(vcount):
                p = base + i * vsize
                x, y, z = struct.unpack_from("<3f", d, p)
                nx, ny, nz = struct.unpack_from("<3f", d, p + n_off) if n_off is not None else (0.0, 0.0, 0.0)
                u, v = struct.unpack_from("<2f", d, p + uv_off) if uv_off is not None else (0.0, 0.0)
                verts.append((x, y, z, nx, ny, nz, u, v))
            tris: list[float] = []
            for i in idx:
                if i < vcount:
                    tris.extend(verts[i])
            if n_off is None:
                _flat_normals(tris)
            out.append(Mesh(_cstr(name), type_, parent, group, material, tris))
        return out

    def load_point(self, name: str) -> tuple[float, float, float] | None:
        """Model-space position of a node (LP_CAB01...): the stored matrix is world-to-node."""
        low = name.lower()
        for i, n in enumerate(self.nodes):
            if n.name.lower() == low:
                return self.node_pos(i)
        return None

    def bounds(self, visible=None):
        lo, hi = [1e9] * 3, [-1e9] * 3
        for i, m in enumerate(self.meshes):
            if visible is not None and i not in visible:
                continue
            t = m.tris
            for k in range(3):
                col = t[k::8]
                if col:
                    lo[k], hi[k] = min(lo[k], min(col)), max(hi[k], max(col))
        if lo[0] > hi[0]:
            return (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)
        return tuple(lo), tuple(hi)

    # --- visibility configs ---
    def config_count(self) -> int:
        n = 1
        for g in self.groups:
            n *= max(len(g.variants), 1)
        return n

    def config_to_choice(self, cfg: int) -> list[int]:
        """Config number -> variant index per group. The LAST group is the lowest digit
        (checked in game: r1_man cfg 172)."""
        out = []
        for g in reversed(self.groups):
            n = max(len(g.variants), 1)
            out.append(cfg % n)
            cfg //= n
        return out[::-1]

    def choice_to_config(self, choice: list[int]) -> int:
        cfg, mul = 0, 1
        for g, c in zip(reversed(self.groups), reversed(choice)):
            cfg += c * mul
            mul *= max(len(g.variants), 1)
        return cfg

    def visible(self, choice: list[int]) -> set[int]:
        """Mesh indices shown for a choice of variants."""
        grouped, shown = set(), set()
        for g, c in zip(self.groups, choice):
            grouped.update(g.nodes)
            if g.variants:
                shown.update(g.variants[min(c, len(g.variants) - 1)])
        return {i for i in range(len(self.meshes)) if i not in grouped or i in shown}

    # --- skins ---
    def materials_bytes(self) -> bytes:
        out = [struct.pack("<I", len(self.skins))]
        for skin in self.skins:
            for m in skin:
                out.append(struct.pack("<17fI100s", *m.diffuse, *m.ambient, *m.specular, *m.emissive, m.power,
                                       len(m.textures), _field(m.shader, m.raw, SHADER_MAX)))
                for t in m.textures:
                    out.append(struct.pack("<40sII", _field(t.name, t.raw, NAME_MAX), t.uv, t.type))
        return b"".join(out)

    def set_materials_bytes(self, blob: bytes):
        """Undo/redo: replace skins with a previously serialised state."""
        self.skins = self._parse_skins(blob, 0)

    @property
    def dirty(self) -> bool:
        return (self.materials_bytes() != self._saved or self.nodes_bytes() != self._saved_nodes
                or self.limits_bytes() != self._saved_limits)

    def dump(self, blob: bytes | None = None) -> bytes:
        """Whole file with the materials, nodes and angle limits replaced; other sections are copied
        through and shifted. A limits section is added before the groups when the file had none."""
        d = self.data
        secs = sorted(self.sections, key=lambda x: x[2])
        new = {MATERIALS: self.materials_bytes() if blob is None else blob}
        if self._has(NODES):
            new[NODES] = self.nodes_bytes()
        if self._has(BOUNDS) or self.limits:
            new[BOUNDS] = self.limits_bytes()
        parts = []          # (tag, declared size, bytes)
        for i, (tag, size, off) in enumerate(secs):
            # a section ends where the next one begins (declared sizes are not always exact)
            end = secs[i + 1][2] if i + 1 < len(secs) else len(d)
            if tag in new:
                body = new[tag]
                if tag == NODES:
                    body += d[off + len(body):end]
                parts.append((tag, len(new[tag]), body))
            else:
                parts.append((tag, size, d[off:end]))
        if BOUNDS in new and not self._has(BOUNDS):
            at = next((i for i, q in enumerate(parts) if q[0] == GROUPS), len(parts))
            parts.insert(at, (BOUNDS, len(new[BOUNDS]), new[BOUNDS]))
        out = bytearray(d[:8]) + struct.pack("<I", len(parts))
        off = 12 + 16 * len(parts)
        for tag, size, body in parts:
            out += struct.pack("<IIQ", tag, size, off)
            off += len(body)
        for _tag, _size, body in parts:
            out += body
        return bytes(out)

    def save(self):
        out = self.dump()
        back = Gam(self.path, out)          # never write what does not read back
        if (back.materials_bytes(), back.nodes_bytes(), back.limits_bytes()) != (
                self.materials_bytes(), self.nodes_bytes(), self.limits_bytes()):
            raise GamError(f"{self.path.name}: запись не сошлась, файл не тронут")
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(out)
        tmp.replace(self.path)
        self.data = out
        self.sections = back.sections
        self._saved = self.materials_bytes()
        self._saved_nodes = self.nodes_bytes()
        self._saved_limits = self.limits_bytes()

    # --- skin editing ---
    def add_skin(self, source: int | None = None) -> int:
        src = self.skins[source if source is not None else len(self.skins) - 1]
        self.skins.append([m.clone() for m in src])
        return len(self.skins) - 1

    def remove_skin(self, i: int):
        if len(self.skins) > 1:
            del self.skins[i]


def _quat(r: list[list[float]]) -> tuple:
    """Rotation matrix -> quaternion x y z w."""
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        return (r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, s / 4
    if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        return s / 4, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s
    if r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        return (r[0][1] + r[1][0]) / s, s / 4, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s
    s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
    return (r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, s / 4, (r[1][0] - r[0][1]) / s


def _flat_normals(t: list[float]):
    for i in range(0, len(t) - 23, 24):
        a, b, c = t[i:i + 3], t[i + 8:i + 11], t[i + 16:i + 19]
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        ln = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
        for k in (0, 8, 16):
            t[i + k + 3:i + k + 6] = [n[0] / ln, n[1] / ln, n[2] / ln]

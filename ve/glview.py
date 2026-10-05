"""OpenGL model preview. Lighting repeats the game's template.fx: reflection is mixed in by
bump alpha, specular is driven by diffuse (or bump) alpha."""
from __future__ import annotations

import math
import struct
import time
from array import array
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (QGuiApplication, QImage, QMatrix4x4, QOffscreenSurface, QOpenGLContext, QSurfaceFormat,
                           QVector3D)
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader,
                              QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QToolTip

from . import gam as G

GL_TRIANGLES, GL_FLOAT, GL_LINES = 0x0004, 0x1406, 0x0001
AXIS_COLORS = ((0.93, 0.30, 0.26), (0.42, 0.80, 0.30), (0.33, 0.56, 0.96))
RING = 0.72         # ring radius in arrow lengths
FAN = 0.55          # radius of the angle-limit fans
GL_DEPTH_TEST, GL_BLEND, GL_CULL_FACE = 0x0B71, 0x0BE2, 0x0B44
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
BG = (0.105, 0.11, 0.115)

VERT = """#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
layout(location=2) in vec2 aUV;
uniform mat4 uMVP; uniform vec3 uOffset; uniform vec3 uScale;
out vec3 vPos; out vec3 vNormal; out vec2 vUV;
void main() { vPos = aPos * uScale + uOffset; vNormal = aNormal * uScale; vUV = aUV; gl_Position = uMVP * vec4(vPos, 1.0); }
"""

FRAG = """#version 330 core
in vec3 vPos; in vec3 vNormal; in vec2 vUV;
uniform sampler2D uDiffuse; uniform sampler2D uBump; uniform samplerCube uCube;
uniform sampler2D uDetail; uniform sampler2D uParams; uniform int uHasDetail; uniform int uHasParams;
uniform vec3 uEye; uniform vec3 uLight;        // uLight points from the surface to the lamp
uniform int uMode;      // 0 diffuse, 1 specular, 2 bump, 3 bump + reflection, 4 Skinned (paint over details)
uniform int uHasTex; uniform int uHasBump; uniform int uHasCube;
uniform float uGlass;
out vec4 frag;

vec3 bumped(vec3 N, vec3 tn) {
    vec3 dp1 = dFdx(vPos), dp2 = dFdy(vPos);
    vec2 du1 = dFdx(vUV), du2 = dFdy(vUV);
    vec3 dp2p = cross(dp2, N), dp1p = cross(N, dp1);
    vec3 T = dp2p * du1.x + dp1p * du2.x, B = dp2p * du1.y + dp1p * du2.y;
    float m = max(dot(T, T), dot(B, B));
    if (m < 1e-12) return N;
    float inv = inversesqrt(m);
    return normalize(mat3(T * inv, B * inv, N) * tn);
}

void main() {
    vec4 D = uHasTex == 1 ? texture(uDiffuse, vUV) : vec4(0.55, 0.55, 0.55, 1.0);
    vec4 Bm = uHasBump == 1 ? texture(uBump, vUV) : vec4(0.5, 0.5, 1.0, 0.0);
    vec3 V = normalize(uEye - vPos);
    vec3 N = normalize(vNormal);
    if (dot(N, N) < 0.5) N = normalize(cross(dFdx(vPos), dFdy(vPos)));
    if (dot(N, V) < 0.0) N = -N;
    if (uMode >= 2 && uHasBump == 1) N = bumped(N, Bm.rgb * 2.0 - 1.0);
    float lum = max(0.0, dot(uLight, N));
    vec3 light = clamp(vec3(0.42) + vec3(0.78) * lum, 0.0, 1.0);
    vec3 color = D.rgb;
    float opacity = 1.0, spec = 0.0;
    if (uMode == 0) opacity = D.a;
    if (uMode == 1) spec = D.a;
    if (uMode == 2) spec = Bm.a;
    if (uMode == 3) {
        spec = D.a;
        vec3 env = uHasCube == 1 ? texture(uCube, reflect(-V, N)).rgb : vec3(0.5);
        color = mix(color, env, Bm.a);
    }
    if (uMode == 4) {      // Skinned.fx: paint shows through the detail mask, lightmap holds the factors
        vec4 Mask = uHasDetail == 1 ? texture(uDetail, vUV) : vec4(D.rgb, 1.0);
        vec4 P = uHasParams == 1 ? texture(uParams, vUV) : vec4(0.0, 0.0, 1.0, 1.0);
        vec3 env = uHasCube == 1 ? texture(uCube, reflect(-V, N)).rgb : vec3(0.5);
        color = mix(mix(Mask.rgb, D.rgb, Mask.a), env, P.g * P.g);
        spec = P.r * P.r;
        light *= P.b;
    }
    color *= light;
    float RdotV = max(0.0, dot(reflect(-uLight, N), V));
    color += vec3(pow(RdotV * lum * spec, 4.0) * 4.0);
    if (uGlass > 0.0) opacity = min(opacity, uGlass);
    if (opacity < 0.02) discard;
    frag = vec4(color, opacity);
}
"""


LINE_VERT = """#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aColor;
uniform mat4 uMVP;
out vec3 vColor;
void main() { vColor = aColor; gl_Position = uMVP * vec4(aPos, 1.0); }
"""

LINE_FRAG = """#version 330 core
in vec3 vColor;
out vec4 frag;
void main() { frag = vec4(vColor, 1.0); }
"""


# --- ambient occlusion bake: light from many directions, gathered in the texture's UV space ---
AO_DEPTH_VERT = """#version 330 core
layout(location=0) in vec3 aPos;
uniform mat4 uLight;
void main() { gl_Position = uLight * vec4(aPos, 1.0); }
"""
AO_DEPTH_FRAG = """#version 330 core
out vec4 frag;
void main() { frag = vec4(gl_FragCoord.z); }
"""
AO_BAKE_VERT = """#version 330 core
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
layout(location=2) in vec2 aUV;
uniform mat4 uLight; uniform float uPush;
out vec4 vL; out vec3 vN;
void main() {
    vN = aNormal;
    vL = uLight * vec4(aPos + aNormal * uPush, 1.0);
    gl_Position = vec4(aUV.x * 2.0 - 1.0, 1.0 - aUV.y * 2.0, 0.0, 1.0);   // v = 0 is the top row of the image
}
"""
AO_BAKE_FRAG = """#version 330 core
in vec4 vL; in vec3 vN;
uniform sampler2D uDepth; uniform vec3 uDir; uniform float uW; uniform float uTexel;
out vec4 frag;
void main() {
    float w = max(dot(normalize(vN), uDir), 0.0);
    vec3 p = vL.xyz * 0.5 + 0.5;
    float vis = 0.0;
    for (int x = 0; x < 2; x++) for (int y = 0; y < 2; y++)
        vis += texture(uDepth, p.xy + (vec2(x, y) - 0.5) * uTexel).r + 0.004 >= p.z ? 0.25 : 0.0;
    frag = vec4(vis * w * uW, w * uW, 0.0, uW);
}
"""
AO_QUAD_VERT = """#version 330 core
layout(location=0) in vec3 aPos;
out vec2 vUV;
void main() { vUV = aPos.xy * 0.5 + 0.5; gl_Position = vec4(aPos.xy, 0.0, 1.0); }
"""
AO_RESOLVE_FRAG = """#version 330 core
in vec2 vUV;
uniform sampler2D uAcc; uniform vec2 uTexel;
out vec4 frag;
float ao(vec4 a) { return a.g > 1e-4 ? clamp(a.r / a.g, 0.0, 1.0) : 1.0; }
void main() {
    vec4 a = texture(uAcc, vUV);
    float v = 1.0;
    if (a.a > 0.01) v = ao(a);
    else {                                  // outside the unwrap: bleed from the nearest covered texels
        float sum = 0.0, n = 0.0;
        for (int x = -4; x <= 4; x++) for (int y = -4; y <= 4; y++) {
            vec4 b = texture(uAcc, vUV + vec2(x, y) * uTexel);
            if (b.a > 0.01) { sum += ao(b); n += 1.0; }
        }
        if (n > 0.0) v = sum / n;
    }
    frag = vec4(v, v, v, 1.0);
}
"""
GL_TEXTURE_2D, GL_TEXTURE0, GL_ONE = 0x0DE1, 0x84C0, 1
GL_R32F, GL_RGBA16F = 0x822E, 0x881A


def sphere_dirs(n: int) -> list[tuple]:
    """Evenly spread directions (Fibonacci sphere)."""
    out = []
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(max(1 - y * y, 0.0))
        a = i * math.pi * (3 - math.sqrt(5))
        out.append((math.cos(a) * r, y, math.sin(a) * r))
    return out


def ring_points(p, axis: int, radius: float, n: int = 48) -> list[tuple]:
    """Circle around a model axis through p."""
    a, b = (axis + 1) % 3, (axis + 2) % 3
    out = []
    for i in range(n):
        q = list(p)
        q[a] += radius * math.cos(2 * math.pi * i / n)
        q[b] += radius * math.sin(2 * math.pi * i / n)
        out.append(tuple(q))
    return out


def limit_fan(p, axes, axis: int, lo: float, hi: float, radius: float) -> list[tuple]:
    """Arc of the directions a mounted part may take when it turns about one of the point's own axes.
    The zero direction is the point's Z (its X for the turn about Z)."""
    k = axes[axis]
    ref = axes[0] if axis == 2 else axes[2]
    side = (k[1] * ref[2] - k[2] * ref[1], k[2] * ref[0] - k[0] * ref[2], k[0] * ref[1] - k[1] * ref[0])
    n = max(int(abs(hi - lo) / math.radians(6)) + 1, 6)
    out = []
    for i in range(n + 1):
        t = lo + (hi - lo) * i / n
        c, s = math.cos(t), math.sin(t)
        out.append(tuple(p[j] + radius * (ref[j] * c + side[j] * s) for j in range(3)))
    return out


def gl_format() -> QSurfaceFormat:
    f = QSurfaceFormat()
    f.setVersion(3, 3)
    f.setProfile(QSurfaceFormat.CoreProfile)
    f.setDepthBufferSize(24)
    f.setSamples(4)
    return f


def shader_mode(name: str) -> int:
    n = name.lower()
    if n == "skinned":
        return 4
    if "env" in n:
        return 3
    if "bump" in n:
        return 2
    if "specular" in n:
        return 1
    return 0


def _cube_faces(path: Path) -> list[QImage]:
    """Six faces of a DDS cube map: every face is re-wrapped as a plain DDS for Pillow."""
    from PIL import Image
    d = path.read_bytes()
    if d[:4] != b"DDS " or not struct.unpack_from("<I", d, 112)[0] & 0x200:
        return []
    h, w = struct.unpack_from("<II", d, 12)
    mips = max(struct.unpack_from("<I", d, 28)[0], 1)
    fourcc = d[84:88]
    block = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16}.get(fourcc)
    bpp = struct.unpack_from("<I", d, 88)[0] // 8

    def level(i):
        lw, lh = max(w >> i, 1), max(h >> i, 1)
        return max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4) * block if block else lw * lh * bpp
    face = sum(level(i) for i in range(mips))
    head = bytearray(d[:128])
    struct.pack_into("<I", head, 28, 1)                     # one mip level
    struct.pack_into("<I", head, 8, struct.unpack_from("<I", head, 8)[0] & ~0x20000)
    struct.pack_into("<II", head, 108, 0x1000, 0)           # plain texture, not a cube
    out = []
    for i in range(6):
        o = 128 + i * face
        try:
            im = Image.open(BytesIO(bytes(head) + d[o:o + level(0)])).convert("RGBA")
        except Exception:
            return []
        out.append(QImage(im.tobytes(), im.width, im.height, QImage.Format_RGBA8888).copy())
    return out


def _qimage(path: Path) -> QImage | None:
    from .game import load_image
    im = load_image(path)
    if im is None:
        return None
    return QImage(im.tobytes(), im.width, im.height, QImage.Format_RGBA8888).copy()


class Part:
    """One model of the scene: a single GAM, or a piece of an assembled vehicle."""

    def __init__(self, gam: G.Gam, resolve, offset=(0.0, 0.0, 0.0), visible: set[int] | None = None,
                 mirror: bool = False, mats=None):
        self.gam, self.resolve, self.offset, self.visible, self.mirror = gam, resolve, offset, visible, mirror
        self.mats = mats        # fn(skin) -> materials shown instead of the file's (live preview), or None

    def bounds(self):
        lo, hi = self.gam.bounds(self.visible)
        if self.mirror:
            lo, hi = (-hi[0], lo[1], lo[2]), (-lo[0], hi[1], hi[2])
        return tuple(a + o for a, o in zip(lo, self.offset)), tuple(a + o for a, o in zip(hi, self.offset))


def bounds_of(parts: list[Part]):
    if not parts:
        return (-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)
    bs = [q.bounds() for q in parts]
    return tuple(min(b[0][k] for b in bs) for k in range(3)), tuple(max(b[1][k] for b in bs) for k in range(3))


class Scene:
    """GL resources of one model. Every method needs a current context."""

    def __init__(self, ctx: QOpenGLContext):
        self.f = ctx.functions()
        self.prog = QOpenGLShaderProgram()
        self.prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self.prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        self.ok = self.prog.link()
        self.lines = QOpenGLShaderProgram()
        self.lines.addShaderFromSourceCode(QOpenGLShader.Vertex, LINE_VERT)
        self.lines.addShaderFromSourceCode(QOpenGLShader.Fragment, LINE_FRAG)
        self.lines.link()
        self._lvao = self._lvbo = None
        self.batches: list[tuple] = []          # (vao, vbo, count, mesh index, material index, part index)
        self.geo: dict[int, tuple] = {}         # id(gam) -> (gam, its buffers): kept while parts are re-seated
        self.tex: dict[str, QOpenGLTexture | None] = {}
        self.parts: list[Part] = []
        self._resolve = lambda name: None
        self._stamps: dict[Path, tuple] = {}
        self.mem: dict[str, tuple] = {}         # "@name" -> (version, QImage), owned by the view

    def clear(self):
        for _gam, items in self.geo.values():
            for vao, vbo, *_ in items:
                vao.destroy()
                vbo.destroy()
        self.geo = {}
        self.batches = []
        self.drop_textures()

    def drop_textures(self):
        for t in self.tex.values():
            if t is not None:
                t.destroy()
        self.tex = {}

    def set_parts(self, parts: list[Part]):
        """Buffers of models already on the card are reused; textures stay too (keyed by file and its time)."""
        self.parts = list(parts)
        self.batches = []
        if not self.ok:
            return
        used = {id(q.gam) for q in self.parts}
        if len(self.geo) > 40:                  # forget models that left the scene
            for key in [k for k in self.geo if k not in used]:
                for vao, vbo, *_ in self.geo.pop(key)[1]:
                    vao.destroy()
                    vbo.destroy()
        for pi, part in enumerate(self.parts):
            key = id(part.gam)
            if key not in self.geo:
                items = []
                for i, m in enumerate(part.gam.meshes):
                    if m.tris and m.material >= 0:
                        items.append(self._batch(i, m))
                self.geo[key] = (part.gam, items)
            self.batches += [(*b, pi) for b in self.geo[key][1]]

    def _batch(self, i: int, m):
        vao = QOpenGLVertexArrayObject()
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        data = array("f", m.tris).tobytes()
        vbo.allocate(data, len(data))
        self.prog.bind()
        for loc, off, size in ((0, 0, 3), (1, 12, 3), (2, 24, 2)):
            self.prog.enableAttributeArray(loc)
            self.prog.setAttributeBuffer(loc, GL_FLOAT, off, size, 32)
        vao.release()
        vbo.release()
        return vao, vbo, len(m.tris) // 8, i, m.material

    def _memory(self, name: str) -> QOpenGLTexture | None:
        """Texture made in memory by the conversion wizard ("@..."): re-uploaded when its version changes."""
        ver, image = self.mem.get(name, (0, None))
        key = f"{name}#{ver}"
        if key not in self.tex:
            for old in [k for k in self.tex if k.startswith(name + "#")]:
                t = self.tex.pop(old)
                if t is not None:
                    t.destroy()
            t = None
            if image is not None:
                t = QOpenGLTexture(image, QOpenGLTexture.GenerateMipMaps)
                t.setMinMagFilters(QOpenGLTexture.LinearMipMapLinear, QOpenGLTexture.Linear)
                t.setWrapMode(QOpenGLTexture.Repeat)
            self.tex[key] = t
        return self.tex[key]

    def _texture(self, name: str, cube: bool = False) -> QOpenGLTexture | None:
        if name.startswith("@"):
            return self._memory(name)
        path = self._resolve(name) if name else None
        key = ("cube:" if cube else "") + str(path).lower()
        if path is not None:
            key += self._stamp(path)
        if key not in self.tex:
            t = None
            if path is not None:
                if cube:
                    faces = _cube_faces(path)
                    if len(faces) == 6:
                        t = QOpenGLTexture(QOpenGLTexture.TargetCubeMap)
                        t.setSize(faces[0].width(), faces[0].height())
                        t.setFormat(QOpenGLTexture.RGBA8_UNorm)
                        t.allocateStorage()
                        for i, im in enumerate(faces):
                            t.setData(0, 0, QOpenGLTexture.CubeMapFace(int(QOpenGLTexture.CubeMapPositiveX.value) + i),
                                      QOpenGLTexture.RGBA, QOpenGLTexture.UInt8, im.constBits())
                        t.setMinMagFilters(QOpenGLTexture.Linear, QOpenGLTexture.Linear)
                        t.setWrapMode(QOpenGLTexture.ClampToEdge)
                else:
                    im = _qimage(path)
                    if im is not None:
                        t = QOpenGLTexture(im, QOpenGLTexture.GenerateMipMaps)
                        t.setMinMagFilters(QOpenGLTexture.LinearMipMapLinear, QOpenGLTexture.Linear)
                        t.setWrapMode(QOpenGLTexture.Repeat)
                        t.setMaximumAnisotropy(8.0)
            self.tex[key] = t
        return self.tex[key]

    def _program(self, vert: str, frag: str) -> QOpenGLShaderProgram:
        p = QOpenGLShaderProgram()
        p.addShaderFromSourceCode(QOpenGLShader.Vertex, vert)
        p.addShaderFromSourceCode(QOpenGLShader.Fragment, frag)
        p.link()
        return p

    def _geometry(self, gam: G.Gam) -> list[tuple]:
        """Buffers of a model, built once: (vao, vbo, count, mesh index, material index)."""
        key = id(gam)
        if key not in self.geo:
            self.geo[key] = (gam, [self._batch(i, m) for i, m in enumerate(gam.meshes) if m.tris and m.material >= 0])
        return self.geo[key][1]

    def bake_ao(self, targets: list[tuple], size: int = 1024, dirs: int = 96, shadow: int = 1024) -> QImage | None:
        """Ambient occlusion of the texels of one texture set. `targets` are (gam, material, visible meshes):
        every model is lit from `dirs` directions, its own geometry casts the shadows, and what reaches a
        surface is summed where that surface lies in the texture."""
        if not self.ok or not targets:
            return None
        f = self.f
        if getattr(self, "_ao", None) is None:
            self._ao = (self._program(AO_DEPTH_VERT, AO_DEPTH_FRAG), self._program(AO_BAKE_VERT, AO_BAKE_FRAG),
                        self._program(AO_QUAD_VERT, AO_RESOLVE_FRAG))
        depth_p, bake_p, resolve_p = self._ao
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Depth)
        fmt.setInternalTextureFormat(GL_R32F)
        depth = QOpenGLFramebufferObject(shadow, shadow, fmt)
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setInternalTextureFormat(GL_RGBA16F)
        acc = QOpenGLFramebufferObject(size, size, fmt)
        out = QOpenGLFramebufferObject(size, size)
        if not (depth.isValid() and acc.isValid() and out.isValid()):
            return None
        acc.bind()
        f.glViewport(0, 0, size, size)
        f.glClearColor(0.0, 0.0, 0.0, 0.0)
        f.glClear(GL_COLOR_BUFFER_BIT)
        f.glDisable(GL_CULL_FACE)
        all_dirs = sphere_dirs(dirs)
        weight = 1.0 / (dirs * len(targets))
        for gam, mat, visible in targets:
            geo = [b for b in self._geometry(gam) if visible is None or b[3] in visible]
            lo, hi = gam.bounds(visible)
            c = QVector3D(*[(a + b) / 2 for a, b in zip(lo, hi)])
            r = max(math.dist(lo, hi) / 2, 1e-3)
            for d in all_dirs:
                dv = QVector3D(*d)
                light = QMatrix4x4()
                light.ortho(-r, r, -r, r, 0.01 * r, 4.0 * r)
                view = QMatrix4x4()
                view.lookAt(c + dv * (2.0 * r), c, QVector3D(0, 1, 0) if abs(d[1]) < 0.95 else QVector3D(1, 0, 0))
                light = light * view
                depth.bind()
                f.glViewport(0, 0, shadow, shadow)
                f.glEnable(GL_DEPTH_TEST)
                f.glDisable(GL_BLEND)
                f.glClearColor(1.0, 1.0, 1.0, 1.0)
                f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
                depth_p.bind()
                depth_p.setUniformValue("uLight", light)
                for b in geo:
                    b[0].bind()
                    f.glDrawArrays(GL_TRIANGLES, 0, b[2])
                    b[0].release()
                acc.bind()
                f.glViewport(0, 0, size, size)
                f.glDisable(GL_DEPTH_TEST)
                f.glEnable(GL_BLEND)
                f.glBlendFunc(GL_ONE, GL_ONE)
                bake_p.bind()
                bake_p.setUniformValue("uLight", light)
                bake_p.setUniformValue("uDir", dv)
                bake_p.setUniformValue1f(bake_p.uniformLocation("uW"), weight)
                bake_p.setUniformValue1f(bake_p.uniformLocation("uPush"), r * 0.004)
                bake_p.setUniformValue1f(bake_p.uniformLocation("uTexel"), 1.0 / shadow)
                bake_p.setUniformValue1i(bake_p.uniformLocation("uDepth"), 0)
                f.glActiveTexture(GL_TEXTURE0)
                f.glBindTexture(GL_TEXTURE_2D, depth.texture())
                for b in geo:
                    if b[4] == mat:
                        b[0].bind()
                        f.glDrawArrays(GL_TRIANGLES, 0, b[2])
                        b[0].release()
        # coverage was summed per model: bring it back to "covered or not" in the resolve pass
        out.bind()
        f.glViewport(0, 0, size, size)
        f.glDisable(GL_BLEND)
        f.glDisable(GL_DEPTH_TEST)
        resolve_p.bind()
        resolve_p.setUniformValue1i(resolve_p.uniformLocation("uAcc"), 0)
        resolve_p.setUniformValue("uTexel", 1.0 / size, 1.0 / size)
        f.glActiveTexture(GL_TEXTURE0)
        f.glBindTexture(GL_TEXTURE_2D, acc.texture())
        quad = array("f", (-1, -1, 0, 3, -1, 0, -1, 3, 0)).tobytes()
        vao = QOpenGLVertexArrayObject()
        vao.create()
        vao.bind()
        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        vbo.allocate(quad, len(quad))
        resolve_p.enableAttributeArray(0)
        resolve_p.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        vao.release()
        vbo.release()
        vao.destroy()
        vbo.destroy()
        img = out.toImage()
        f.glBindTexture(GL_TEXTURE_2D, 0)
        out.release()
        QOpenGLFramebufferObject.bindDefault()
        f.glEnable(GL_DEPTH_TEST)
        return img

    def _stamp(self, path: Path) -> str:
        """File time as part of the texture key, looked up at most once a second per file."""
        now = time.monotonic()
        hit = self._stamps.get(path)
        if hit is None or now - hit[0] > 1.0:
            try:
                hit = (now, str(path.stat().st_mtime_ns))
            except OSError:
                hit = (now, "")
            self._stamps[path] = hit
        return hit[1]

    def draw_points(self, w: int, h: int, cam: "Camera", points: list[tuple], selected: int, hot: int = -1,
                    limits=None):
        """Load points as small crosses over the model; the selected one carries a move gizmo:
        X red, Y green, Z blue arrows, the hovered one turns yellow."""
        if not points:
            return
        data = array("f")
        for i, (p, axes) in enumerate(points):
            # the point's own axes: short and pale, longer on the selected one
            k = cam.gizmo() * 0.5 if i == selected else cam.radius * 0.045
            for axis in range(3):
                col = tuple(c * 0.55 + 0.45 for c in AXIS_COLORS[axis])
                data.extend((*p, *col, *(p[j] + axes[axis][j] * k for j in range(3)), *col))
        if 0 <= selected < len(points) and limits:
            # allowed turn about each of the point's own axes: a fan from min to max
            p, axes = points[selected]
            for axis in range(3):
                lo, hi = limits[axis], limits[axis + 3]
                if hi - lo < 1e-4:
                    continue
                fan = limit_fan(p, axes, axis, lo, hi, cam.gizmo() * FAN)
                col = tuple(c * 0.75 + 0.25 for c in AXIS_COLORS[axis])
                for a, b in zip(fan, fan[1:]):
                    data.extend((*a, *col, *b, *col))
                for q in (fan[0], fan[-1], *fan[2:-2:3]):
                    data.extend((*p, *col, *q, *col))
        if 0 <= selected < len(points):
            p = points[selected][0]
            ln = cam.gizmo()
            for axis in range(3):           # rotation rings about the model axes
                col = (1.0, 0.92, 0.25) if axis + 3 == hot else AXIS_COLORS[axis]
                ring = ring_points(p, axis, ln * RING)
                for a, b in zip(ring, ring[1:] + ring[:1]):
                    data.extend((*a, *col, *b, *col))
            for axis in range(3):
                col = (1.0, 0.92, 0.25) if axis == hot else AXIS_COLORS[axis]
                tip = list(p)
                tip[axis] += ln
                data.extend((*p, *col, *tip, *col))
                for other in range(3):              # arrow head
                    if other == axis:
                        continue
                    for sign in (-1.0, 1.0):
                        q = list(p)
                        q[axis] += ln * 0.8
                        q[other] += sign * ln * 0.07
                        data.extend((*tip, *col, *q, *col))
        raw = data.tobytes()
        if self._lvao is None:
            self._lvao = QOpenGLVertexArrayObject()
            self._lvao.create()
            self._lvbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._lvbo.create()
        self._lvao.bind()
        self._lvbo.bind()
        self._lvbo.allocate(raw, len(raw))
        self.lines.bind()
        self.lines.enableAttributeArray(0)
        self.lines.setAttributeBuffer(0, GL_FLOAT, 0, 3, 24)
        self.lines.enableAttributeArray(1)
        self.lines.setAttributeBuffer(1, GL_FLOAT, 12, 3, 24)
        self.lines.setUniformValue("uMVP", cam.matrices(w / max(h, 1))[0])
        self.f.glDisable(GL_DEPTH_TEST)
        self.f.glDrawArrays(GL_LINES, 0, len(data) // 6)
        self._lvao.release()
        self._lvbo.release()
        self.lines.release()

    def draw(self, w: int, h: int, cam: "Camera", skin: int, glass: bool = False):
        f = self.f
        f.glViewport(0, 0, w, h)
        f.glClearColor(*BG, 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if not self.batches:
            return
        f.glEnable(GL_DEPTH_TEST)
        f.glDisable(GL_CULL_FACE)
        mvp, eye, light = cam.matrices(w / max(h, 1))
        p = self.prog
        p.bind()
        p.setUniformValue("uMVP", mvp)
        p.setUniformValue("uEye", eye)
        p.setUniformValue("uLight", light)
        p.setUniformValue1i(p.uniformLocation("uDiffuse"), 0)
        p.setUniformValue1i(p.uniformLocation("uBump"), 1)
        p.setUniformValue1i(p.uniformLocation("uCube"), 2)
        p.setUniformValue1i(p.uniformLocation("uDetail"), 3)
        p.setUniformValue1i(p.uniformLocation("uParams"), 4)
        todo = []
        for b in self.batches:
            part = self.parts[b[5]]
            mats = part.gam.skins[min(max(skin, 0), len(part.gam.skins) - 1)]
            if part.mats is not None:
                mats = part.mats(max(skin, 0))          # the preview picks the skin itself
            if (part.visible is None or b[3] in part.visible) and b[4] < len(mats):
                m = mats[b[4]]
                mode = shader_mode(m.shader)
                see = glass and "glass" in m.tex(G.TEX_DIFFUSE).lower()
                todo.append((mode == 0 or see, b, m, mode, see))
        todo.sort(key=lambda t: t[0])           # opaque first
        for blend, b, m, mode, see in todo:
            if blend:
                f.glEnable(GL_BLEND)
                f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            else:
                f.glDisable(GL_BLEND)
            part = self.parts[b[5]]
            self._resolve = part.resolve
            p.setUniformValue("uOffset", QVector3D(*part.offset))
            p.setUniformValue("uScale", QVector3D(-1.0 if part.mirror else 1.0, 1.0, 1.0))
            d = self._texture(m.tex(G.TEX_DIFFUSE))
            bm = self._texture(m.tex(G.TEX_BUMP)) if mode >= 2 else None
            cb = self._texture(m.tex(G.TEX_CUBE), True) if mode >= 3 else None
            dt = self._texture(m.tex(G.TEX_DETAIL)) if mode == 4 else None
            pr = self._texture(m.tex(G.TEX_PARAMS)) if mode == 4 else None
            for unit, t in ((0, d), (1, bm), (2, cb), (3, dt), (4, pr)):
                if t is not None:
                    t.bind(unit)
            p.setUniformValue1i(p.uniformLocation("uMode"), mode)
            p.setUniformValue1i(p.uniformLocation("uHasTex"), int(d is not None))
            p.setUniformValue1i(p.uniformLocation("uHasBump"), int(bm is not None))
            p.setUniformValue1i(p.uniformLocation("uHasCube"), int(cb is not None))
            p.setUniformValue1i(p.uniformLocation("uHasDetail"), int(dt is not None))
            p.setUniformValue1i(p.uniformLocation("uHasParams"), int(pr is not None))
            p.setUniformValue1f(p.uniformLocation("uGlass"), 0.45 if see else 0.0)
            b[0].bind()
            f.glDrawArrays(GL_TRIANGLES, 0, b[2])
            b[0].release()
        f.glDisable(GL_BLEND)
        p.release()


class Camera:
    def __init__(self):
        self.yaw, self.pitch, self.dist = 35.0, 18.0, 5.0
        self.target = QVector3D(0, 0, 0)
        self.radius = 1.0

    def frame(self, lo, hi):
        self.target = QVector3D(*[(a + b) / 2 for a, b in zip(lo, hi)])
        self.radius = max(math.dist(lo, hi) / 2, 0.01)
        self.dist = self.radius * 2.6
        self.yaw, self.pitch = 35.0, 18.0

    def matrices(self, aspect: float):
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        d = QVector3D(math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p))
        eye = self.target + d * self.dist
        proj = QMatrix4x4()
        proj.perspective(35.0, aspect, max(self.dist - self.radius * 3, self.radius * 0.02), self.dist + self.radius * 6)
        view = QMatrix4x4()
        view.lookAt(eye, self.target, QVector3D(0, 1, 0))
        mirror = QMatrix4x4()
        mirror.scale(-1, 1, 1)              # the game is left-handed
        side = QVector3D.crossProduct(QVector3D(0, 1, 0), d).normalized()
        light = (d + side * 0.7 + QVector3D(0, 0.9, 0)).normalized()
        return proj * mirror * view, eye, light

    def gizmo(self) -> float:
        """Arrow length of the move gizmo: about the same size on screen at any zoom."""
        return self.dist * 0.13

    def orbit(self, dx: float, dy: float):
        self.yaw = (self.yaw + dx * 0.5) % 360
        self.pitch = min(max(self.pitch + dy * 0.5, -89.0), 89.0)

    def pan(self, dx: float, dy: float):
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        d = QVector3D(math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p))
        side = QVector3D.crossProduct(QVector3D(0, 1, 0), d).normalized()
        up = QVector3D.crossProduct(d, side)
        k = self.dist * 0.0012
        self.target += side * (dx * k) + up * (dy * k)

    def zoom(self, steps: float):
        self.dist = min(max(self.dist * (0.88 ** steps), self.radius * 0.15), self.radius * 30)


class ModelView(QOpenGLWidget):
    """Orbit with the left button, pan with the right or middle one, zoom with the wheel.
    With load points shown, the left button on a point selects and drags it."""

    pointPicked = Signal(int)
    pointMoved = Signal(int, float, float, float)
    pointRotated = Signal(int, int, float)      # point, model axis, degrees
    pointDropped = Signal()

    def __init__(self):
        super().__init__()
        self.setFormat(gl_format())
        self.setMinimumSize(200, 160)
        self.cam = Camera()
        self.scene: Scene | None = None
        self.parts: list[Part] = []
        self.skin = 0
        self.glass = False
        self._pending = False
        self._stale = False
        self._last = QPoint()
        self.points: list[tuple] = []       # (name, (x, y, z), own axes) in scene space
        self.point = -1
        self._drag = -1
        self._axis = -1                     # gizmo handle being dragged: arrow 0..2, ring 3..5, -1 = free move
        self._hot = -1                      # gizmo handle under the cursor
        self._moved = False
        self.limits = None                  # angle limits of the selected point: min xyz, max xyz (radians)
        self.mem: dict[str, tuple] = {}     # textures made in memory: "@name" -> (version, QImage)
        self.setMouseTracking(True)

    def set_memory(self, name: str, image):
        """Give the scene a PIL image under a "@name" that materials can refer to."""
        ver = self.mem.get(name, (0, None))[0] + 1
        im = image.convert("RGBA")
        self.mem[name] = (ver, QImage(im.tobytes(), im.width, im.height, QImage.Format_RGBA8888).copy())
        self.update()

    def bake_ao(self, targets: list[tuple], size: int = 1024):
        """Ambient occlusion of a texture set as a PIL "L" image (see Scene.bake_ao), or None without OpenGL."""
        if self.scene is None or not self.isValid():
            return None
        from PIL import Image
        self.makeCurrent()
        try:
            img = self.scene.bake_ao(targets, size)
        finally:
            self.doneCurrent()
        if img is None:
            return None
        img = img.convertToFormat(QImage.Format_RGBA8888)
        return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGBA",
                                img.bytesPerLine(), 1).getchannel("R")

    def set_points(self, points: list[tuple], selected: int = -1, limits=None):
        self.points, self.point, self.limits = list(points), selected, limits
        self.update()

    def _mvp(self):
        return self.cam.matrices(self.width() / max(self.height(), 1))[0]

    def _screen(self, p) -> tuple[float, float, float]:
        v = self._mvp().map(QVector3D(*p))
        return (v.x() + 1) / 2 * self.width(), (1 - v.y()) / 2 * self.height(), v.z()

    def _axis_at(self, pos) -> int:
        """Gizmo arrow of the selected point under the cursor."""
        if not 0 <= self.point < len(self.points):
            return -1
        p = self.points[self.point][1]
        ax, ay, _ = self._screen(p)
        best, hit = 8.0, -1
        for axis in range(3):
            tip = list(p)
            tip[axis] += self.cam.gizmo()
            bx, by, _ = self._screen(tip)
            vx, vy = bx - ax, by - ay
            ln2 = vx * vx + vy * vy
            if ln2 < 16:                    # the arrow points at the camera
                continue
            t = min(max(((pos.x() - ax) * vx + (pos.y() - ay) * vy) / ln2, 0.15), 1.0)
            d = math.hypot(pos.x() - ax - vx * t, pos.y() - ay - vy * t)
            if d < best:
                best, hit = d, axis
        if hit >= 0:
            return hit
        best = 7.0
        for axis in range(3):
            pts = [self._screen(q) for q in ring_points(p, axis, self.cam.gizmo() * RING, 32)]
            for (x0, y0, _), (x1, y1, _) in zip(pts, pts[1:] + pts[:1]):
                vx, vy = x1 - x0, y1 - y0
                t = min(max(((pos.x() - x0) * vx + (pos.y() - y0) * vy) / (vx * vx + vy * vy or 1.0), 0.0), 1.0)
                d = math.hypot(pos.x() - x0 - vx * t, pos.y() - y0 - vy * t)
                if d < best:
                    best, hit = d, axis + 3
        return hit

    def _ring_angle(self, p, axis: int, x: float, y: float) -> float:
        """Angle (degrees) of the ring point that is nearest to the cursor on screen."""
        n = 240
        best, hit = 1e18, 0
        for i, q in enumerate(ring_points(p, axis, self.cam.gizmo() * RING, n)):
            sx, sy, _ = self._screen(q)
            d = (sx - x) ** 2 + (sy - y) ** 2
            if d < best:
                best, hit = d, i
        return 360.0 * hit / n

    def _point_at(self, pos) -> int:
        best, hit = 14.0, -1
        for i, (_n, p, _a) in enumerate(self.points):
            x, y, _z = self._screen(p)
            d = math.hypot(x - pos.x(), y - pos.y())
            if d < best:
                best, hit = d, i
        return hit

    def set_model(self, gam: G.Gam | None, resolve, glass: bool = False):
        self.set_parts([Part(gam, resolve)] if gam is not None else [], glass)

    def set_parts(self, parts: list[Part], glass: bool = False, refit: bool = True):
        self.parts, self.glass = list(parts), glass
        self._pending = True
        if refit:
            self.skin = 0
            self.cam.frame(*bounds_of(self.parts))
        self.update()

    def show_state(self, skin: int, visible: set[int] | None = None, refit: bool = False):
        self.skin = skin
        if visible is not None and len(self.parts) == 1:
            self.parts[0].visible = visible
        if refit and self.parts:
            keep = (self.cam.yaw, self.cam.pitch)
            self.cam.frame(*bounds_of(self.parts))
            self.cam.yaw, self.cam.pitch = keep
        self.update()

    def reload_textures(self):
        self._stale = True
        self.update()

    def initializeGL(self):
        self.scene = Scene(self.context())
        self.scene.mem = self.mem
        self.context().aboutToBeDestroyed.connect(self._cleanup)
        self._pending = True

    def _cleanup(self):
        if self.scene is not None:
            self.makeCurrent()
            self.scene.clear()
            self.scene = None
            self.doneCurrent()

    def paintGL(self):
        if self.scene is None:
            return
        if self._pending:
            self._pending = self._stale = False
            self.scene.set_parts(self.parts)
        if self._stale:
            self._stale = False
            self.scene._stamps.clear()      # changed files are noticed at once, the rest is kept
        r = self.devicePixelRatioF()
        self.scene.draw(int(self.width() * r), int(self.height() * r), self.cam, self.skin, self.glass)
        if self.points:
            self.scene.draw_points(int(self.width() * r), int(self.height() * r), self.cam,
                                   [(p, a) for _n, p, a in self.points], self.point,
                                   self._axis if self._axis >= 0 else self._hot, self.limits)

    def mousePressEvent(self, e):
        self._last = e.position().toPoint()
        self._drag, self._moved, self._axis = -1, False, -1
        if self.points and e.button() == Qt.LeftButton:
            axis = self._axis_at(self._last)
            if axis >= 0:
                self._drag, self._axis = self.point, axis
                return
            hit = self._point_at(self._last)
            if hit >= 0:
                self._drag = self.point = hit
                self.pointPicked.emit(hit)
                self.update()

    def mouseReleaseEvent(self, e):
        if self._drag >= 0 and self._moved:
            self.pointDropped.emit()
        self._drag = self._axis = -1
        self.update()

    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        dx, dy = p.x() - self._last.x(), p.y() - self._last.y()
        self._last = p
        if not e.buttons():
            hot = self._axis_at(p) if self.points else -1
            if hot != self._hot:
                self._hot = hot
                self.update()
            hit = self._point_at(p) if self.points and hot < 0 else -1
            if hit >= 0:
                QToolTip.showText(e.globalPosition().toPoint(), self.points[hit][0], self)
            else:
                QToolTip.hideText()
            return
        if self._drag >= 0 and self._axis >= 3 and e.buttons() & Qt.LeftButton:
            # on a ring: the angle the cursor sweeps around the point on screen
            old = self.points[self._drag][1]
            a0 = self._ring_angle(old, self._axis - 3, p.x() - dx, p.y() - dy)
            a1 = self._ring_angle(old, self._axis - 3, p.x(), p.y())
            turn = (a1 - a0 + 180.0) % 360.0 - 180.0
            if turn:
                self._moved = True
                self.pointRotated.emit(self._drag, self._axis - 3, turn)
            self.update()
            return
        if self._drag >= 0 and self._axis >= 0 and e.buttons() & Qt.LeftButton:
            # along one arrow: the mouse path is projected onto the arrow as seen on screen
            name, old, axes = self.points[self._drag]
            ax, ay, _ = self._screen(old)
            tip = list(old)
            tip[self._axis] += self.cam.gizmo()
            bx, by, _ = self._screen(tip)
            vx, vy = bx - ax, by - ay
            ln2 = vx * vx + vy * vy
            if ln2 > 1:
                new = list(old)
                new[self._axis] += (dx * vx + dy * vy) / ln2 * self.cam.gizmo()
                self.points[self._drag] = (name, tuple(new), axes)
                self._moved = True
                self.pointMoved.emit(self._drag, *new)
            self.update()
            return
        if self._drag >= 0 and e.buttons() & Qt.LeftButton:
            # by the point itself: slide in the plane parallel to the screen
            name, old, axes = self.points[self._drag]
            mvp = self._mvp()
            ndc = mvp.map(QVector3D(*old))
            inv, ok = mvp.inverted()
            if ok:
                w = inv.map(QVector3D(ndc.x() + 2.0 * dx / max(self.width(), 1),
                                      ndc.y() - 2.0 * dy / max(self.height(), 1), ndc.z()))
                self.points[self._drag] = (name, (w.x(), w.y(), w.z()), axes)
                self._moved = True
                self.pointMoved.emit(self._drag, w.x(), w.y(), w.z())
            self.update()
            return
        if e.buttons() & Qt.LeftButton:
            self.cam.orbit(dx, dy)
        elif e.buttons() & (Qt.RightButton | Qt.MiddleButton):
            self.cam.pan(dx, dy)
        self.update()

    def wheelEvent(self, e):
        self.cam.zoom(e.angleDelta().y() / 120)
        self.update()


def render_image(gam: G.Gam, resolve, skin: int = 0, visible=None, size=(640, 480), glass=False,
                 view: tuple[float, float] | None = None) -> QImage | None:
    return render_parts([Part(gam, resolve, visible=visible)], skin, size, glass, view)


def render_parts(parts: list[Part], skin: int = 0, size=(640, 480), glass=False,
                 view: tuple[float, float] | None = None) -> QImage | None:
    """Render without a window (checks, screenshots). None when OpenGL 3.3 is not available."""
    if QGuiApplication.platformName() == "offscreen":
        return None
    ctx = QOpenGLContext()
    ctx.setFormat(gl_format())
    surf = QOffscreenSurface()
    surf.setFormat(gl_format())
    surf.create()
    if not ctx.create() or not ctx.makeCurrent(surf):
        return None
    fmt = QOpenGLFramebufferObjectFormat()
    fmt.setAttachment(QOpenGLFramebufferObject.Depth)
    fmt.setSamples(4)
    fbo = QOpenGLFramebufferObject(size[0], size[1], fmt)
    fbo.bind()
    scene = Scene(ctx)
    scene.set_parts(parts)
    cam = Camera()
    cam.frame(*bounds_of(parts))
    if view:
        cam.yaw, cam.pitch = view
    scene.draw(size[0], size[1], cam, skin, glass)
    img = fbo.toImage()
    scene.clear()
    fbo.release()
    del fbo
    ctx.doneCurrent()
    return img if scene.ok else None

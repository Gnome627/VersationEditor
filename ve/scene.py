"""Compound trigger blocks: team, object access, camera shots.

Recognises the Lua patterns the original authors repeat (TeamCreate plus
per-vehicle setup under 'if', stopping the player, camera orbits) and writes
them back as the same Lua. Source variable names are kept, since raw lua
further down may use them; Env tracks what each variable refers to.
"""
from __future__ import annotations

import math
import re

from . import lua
from .lua import F, Spec

NUM = r"-?(?:\d+\.?\d*|\.\d+)"
PLAYER = "@player"
_NAME_CALL = re.compile(r"(?:GetEntityByName|getObj)\((['\"])([^'\"]+)\1\)\Z")
_VAR = re.compile(r"[A-Za-z_]\w*\Z")
ANIMS = [(f"AT_{n}", t) for n, t in (
    ("STAND1", "стоит 1"), ("STAND2", "стоит 2"), ("MOVE1", "идёт 1"), ("MOVE2", "идёт 2"),
    ("ATTACK1", "атака 1"), ("ATTACK2", "атака 2"), ("PAIN1", "боль 1"), ("PAIN2", "боль 2"),
    ("DEATH1", "смерть 1"), ("DEATH2", "смерть 2"), ("RESERVED1", "жест 1"), ("RESERVED2", "жест 2"),
    ("RESERVED3", "жест 3"), ("RESERVED4", "жест 4"))]
ON = lua.Field("on", "check", None, "1", "включено")

# what can be done to an object: 'var:Method(...)' calls
OP_SPECS = [
    Spec("skin", "Задать скин", [F("n", "int", default="0")], "SetSkin({n:n})", group="Вид"),
    Spec("rskin", "Задать случайный скин", [], "SetRandomSkin()", group="Вид"),
    Spec("pos", "Поставить на землю в точке", [F("pos", "vec")], "SetGamePositionOnGround({pos:v})", group="Положение"),
    Spec("setpos", "Переместить в точку без посадки на землю", [F("pos", "vec")], "SetPosition({pos:v})", group="Положение"),
    Spec("rot", "Повернуть", [F("rot", "quat")], "SetRotation({rot:q})", group="Положение"),
    Spec("mod", "Изменить характеристику", [F("stat", "enum", [("hp", "здоровье"), ("maxhp", "максимум здоровья"), ("fuel", "топливо")], "hp"),
                             F("val", "text", default="= 1000")],
         {"hp": 'AddModifier("hp", {val:s})', "maxhp": 'AddModifier("maxhp", {val:s})',
          "fuel": 'AddModifier("fuel", {val:s})'}, variant="stat", group="Живучесть"),
    Spec("immortal", "Бессмертие: получает урон, но не умирает", [ON], "setImmortalMode({on:a})", group="Живучесть"),
    Spec("god", "Неуязвимость: не получает урон", [ON], "setGodMode({on:a})", group="Живучесть"),
    Spec("custom", "Установить кастомное управление", [lua.Field("on", "check", None, "true", "включено")], "SetCustomControlEnabled({on:a})",
         group="Движение"),
    Spec("vel", "Задать скорость движения", [F("n", "num", default="0")], "SetCustomLinearVelocity({n:n})", group="Движение"),
    Spec("throttle", "Задать газ", [F("n", "num", default="0")], "SetThrottle({n:n})", group="Движение"),
    Spec("path", "Ехать по пути", [F("path", "extpath")], "SetExternalPathByName({path:s})", group="Движение"),
    Spec("endpath", "Переставить в конец текущего пути", [], "PlaceToEndOfPath()", group="Движение"),
    Spec("endpath2", "Переставить в конец пути", [F("path", "extpath")], "PlaceToEndOfPath({path:s})",
         group="Движение"),
    Spec("cruise", "Задать крейсерскую скорость", [F("n", "num", default="10")], "SetCruisingSpeed({n:n})", group="Движение"),
    Spec("maxspeed", "Задать максимальную скорость", [F("n", "num", default="0")], "SetMaxSpeed({n:n})", group="Движение"),
    Spec("limit", "Ограничить скорость", [F("n", "num", default="10")], "LimitMaxSpeed({n:n})", group="Движение"),
    Spec("unlimit", "Снять ограничение скорости", [], "UnlimitMaxSpeed()", group="Движение"),
    Spec("torque", "Задать тягу двигателя", [F("n", "num", default="0")], "SetMaxTorque({n:n})", group="Движение"),
    Spec("item", "Положить в кузов", [F("item", "item"), F("n", "int", default="1")],
         "AddItemsToRepository({item:s}, {n:n})", group="Груз"),
    Spec("gun", "Положить в кузов оружие со случайными аффиксами", [F("gun", "gun"), F("n", "int", default="1"), F("cls", "text", default="5")],
         "", group="Груз"),
    Spec("trailer", "Прицепить прицеп", [F("name", "text", default="MolokovozTrailer")], "AttachTrailer({name:s})", group="Груз"),
    Spec("anim", "Проиграть анимацию", [F("a", "enum", ANIMS, "AT_STAND1")], "SetNodeAction({a:c})", group="Актёр"),
    Spec("nextanim", "После одной анимации включить другую", [F("a", "enum", ANIMS, "AT_RESERVED1"), F("b", "enum", ANIMS, "AT_STAND1")],
         "SetNextForAnimation({a:c}, {b:c})", group="Актёр"),
    Spec("dest", "Ехать в точку", [F("pos", "vec")], "SetDestination({pos:v})", group="Команда"),
    Spec("tactic", "Задать тактику команды", [F("name", "tactic")], 'SetProperty("TeamTacticPrototype", {name:s})', group="Команда"),
    Spec("adjust", "Применить тактику (_AdjustBehaviour)", [], "_AdjustBehaviour()", group="Команда"),
    Spec("stackopen", "Маршрут: начать список точек", [], "StackOpen()", group="Команда"),
    Spec("stackloop", "Маршрут: ходить по кругу", [], "StackLoop()", group="Команда"),
    Spec("stackclose", "Маршрут: закончить список точек", [], "StackClose()", group="Команда"),
    Spec("hold", "Не стрелять, мс", [F("n", "num", default="5000")], "HoldFire({n:n})", group="Прочее"),
    Spec("belong", "Сменить группировку", [F("n", "belong", default="1002")], "SetBelong({n:n})", group="Прочее"),
    Spec("active", "Включить или выключить объект (Active)", [ON], 'SetProperty("Active", {on:a})', group="Прочее"),
    Spec("remove", "Удалить объект", [], "Remove()", group="Прочее"),
]
for _s in OP_SPECS:
    _s._rx = [(v, t, lua._compile(t, _s)) for v, t in _s.template_items() if t]
OP_BY = {s.kind: s for s in OP_SPECS}
_GUN_RX = re.compile(r"AddVehicleGunsWithRandomAffix\((.+)\)\Z")
_TEAM_RX = re.compile(r"(?:(local)?([A-Za-z_]\w*)=)?(TeamCreate|CreateTeam|TeamCreateWithWarez)\((.*)\)\Z")

STOP_PLAYER = [{"k": "custom", "on": "true"}, {"k": "vel", "n": "0"}, {"k": "throttle", "n": "0"},
               {"k": "custom", "on": "false"}]


def split_args(s: str) -> list[str]:
    out, depth, q, start = [], 0, "", 0
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(s[start:i])
            start = i + 1
    if s[start:].strip() or out:
        out.append(s[start:])
    return out


def vec(sq: str) -> str | None:
    m = re.fullmatch(rf"\(*CVector\(({NUM}),({NUM}),({NUM})\)\)*", sq)
    return " ".join(m.groups()) if m else None


def _strlit(sq: str) -> str | None:
    return sq[1:-1] if len(sq) >= 2 and sq[0] in "\"'" and sq[-1] == sq[0] and sq[0] not in sq[1:-1] else None


def _is_num(sq: str) -> bool:
    return re.fullmatch(NUM, sq) is not None


def yaw_quat(deg: float) -> str:
    a = math.radians(deg) / 2
    return f"0.000 {math.sin(a):.3f} 0.000 {math.cos(a):.3f}"


def quat_yaw(q: str) -> float | None:
    """Yaw in degrees if the quaternion is a pure rotation about the vertical."""
    try:
        x, y, z, w = (float(v) for v in q.split())
    except ValueError:
        return None
    if abs(x) > 0.02 or abs(z) > 0.02:
        return None
    return math.degrees(2 * math.atan2(y, w)) % 360


class Env:
    """What script variables mean: object, its id, its position, a team."""

    def __init__(self):
        self.vars: dict[str, tuple[str, str]] = {}

    def ref(self, sq: str) -> str | None:
        """Expression -> object: '@player' or a name; None if not an object."""
        if sq == "GetPlayerVehicle()":
            return PLAYER
        m = _NAME_CALL.match(sq)
        if m:
            return m.group(2)
        if _VAR.match(sq):
            v = self.vars.get(sq)
            return v[1] if v and v[0] in ("obj", "team") else None
        m = re.fullmatch(r"([A-Za-z_]\w*):GetVehicle\((\d+)\)", sq)
        if m and self.vars.get(m.group(1), ("",))[0] == "team":
            return f"{self.vars[m.group(1)][1]}_vehicle_{m.group(2)}"
        return None

    def direct_ref(self, sq: str) -> str | None:
        return None if _VAR.match(sq) else self.ref(sq)

    def id(self, sq: str) -> str | None:
        if sq == "GetPlayerVehicleId()":
            return PLAYER
        if sq.endswith(":GetId()"):
            return self.ref(sq[:-8])
        v = self.vars.get(sq)
        return v[1] if v and v[0] == "id" else None

    def note(self, sq: str):
        m = re.match(r"(?:local)?([A-Za-z_]\w*)=(.+)\Z", sq)
        if not m:
            return
        var, rhs = m.groups()
        self.vars.pop(var, None)
        r = self.ref(rhs)
        if r:
            self.vars[var] = ("obj", r)
            return
        r = self.id(rhs)
        if r:
            self.vars[var] = ("id", r)
            return
        if rhs.endswith(":GetPosition()") and self.ref(rhs[:-14]):
            self.vars[var] = ("pos", self.ref(rhs[:-14]))
            return
        m2 = re.match(r"CreateVehicleEx\((['\"])[^'\"]*\1,(['\"])([^'\"]+)\2", rhs)
        if m2:
            self.vars[var] = ("obj", m2.group(3))

    def note_block(self, b: dict):
        if b.get("var"):
            if b["k"] == "team":
                self.vars[b["var"]] = ("team", b["name"])
            elif b["k"] == "vehicle":
                self.vars[b["var"]] = ("obj", b["name"])
            elif b["k"] == "obj" and b.get("decl"):
                self.vars[b["var"]] = ("obj", b["ref"])


def ref_expr(ref: str, quote: str = '"') -> str:
    if ref == PLAYER or not ref:
        return "GetPlayerVehicle()"
    if ref.startswith("="):
        return ref[1:]
    return f"GetEntityByName({lua._q(ref, quote)})"


def id_expr(ref: str, quote: str = '"') -> str:
    if ref == PLAYER or not ref:
        return "GetPlayerVehicleId()"
    if ref.startswith("="):
        return ref[1:]
    return f"getObj({lua._q(ref, quote)}):GetId()"


def ref_title(ref: str) -> str:
    return "игрок" if ref in (PLAYER, "") else ref.lstrip("=")


# --- object ---

def _parse_op(call: str) -> dict:
    """'Method(args)' -> op; unknown calls stay raw lua."""
    return lua._match(OP_SPECS, call)


def _parse_gun(sq: str, expect: str | None) -> tuple[str, dict] | None:
    m = _GUN_RX.match(sq)
    if not m:
        return None
    a = split_args(m.group(1))
    if len(a) < 3 or _strlit(a[1]) is None or not _is_num(a[2]):
        return None
    if expect is not None and a[0] != expect:
        return None
    return a[0], {"k": "gun", "gun": _strlit(a[1]), "n": a[2], "cls": ",".join(a[3:])}


def _parse_body(body: str, var: str) -> list[dict] | None:
    ops, known = [], 0
    for a, b in lua.split_statements(body):
        t = body[a:b]
        sq = lua.squeeze(t)
        if re.match(r"(else|elseif)\b", t) or (re.search(r"\belse(if)?\b", t) and not re.match(r"(if|for|while|function|do|repeat)\b", t)):
            return None                       # the 'if' has an else branch: not a plain object-access block
        op = None
        if sq.startswith(var + ":"):
            op = _parse_op(t.strip()[len(var):].lstrip()[1:].lstrip())
        if op is None:
            g = _parse_gun(sq, var)
            op = g[1] if g else None
        if op is None:
            ops.append({"k": "lua", "code": lua._dedent(body, a, b)})
        else:
            ops.append(op)
            known += 1
    return ops if known else None


_GUARD = re.compile(r"if\s+([A-Za-z_]\w*)\s+then\b(.*)\bend\s*\Z", re.S)
_CALL_HEAD = re.compile(r"((?:GetPlayerVehicle\(\s*\))|(?:(?:GetEntityByName|getObj)\(\s*(['\"])[^'\"]+\2\s*\))"
                        r"|(?:[A-Za-z_]\w*:GetVehicle\(\s*\d+\s*\))|(?:[A-Za-z_]\w*))\s*:\s*([A-Za-z_]\w*\s*\(.*)\Z", re.S)


def _try_obj(texts: list[str], i: int, env: Env):
    t = texts[i].strip()
    sq = lua.squeeze(t)
    # local v = <object> / if v then ... end
    m = re.fullmatch(r"(local)?([A-Za-z_]\w*)=(.+)", sq)
    if m and i + 1 < len(texts):
        ref = env.direct_ref(m.group(3))
        g = _GUARD.match(texts[i + 1].strip())
        if ref and g and g.group(1) == m.group(2):
            ops = _parse_body(g.group(2), m.group(2))
            if ops:
                env.note(sq)
                return {"k": "obj", "ref": ref, "var": m.group(2), "decl": True, "local": bool(m.group(1)),
                        "guard": True, "ops": ops}, 2
    # if v then ... end, with v declared above
    g = _GUARD.match(t)
    if g and env.ref(g.group(1)):
        ops = _parse_body(g.group(2), g.group(1))
        if ops:
            return {"k": "obj", "ref": env.ref(g.group(1)), "var": g.group(1), "decl": False, "guard": True,
                    "ops": ops}, 1
    # consecutive v:Method(...) without a guard
    head = _head(t, env)
    if head:
        expr, ref, op = head
        ops, n = [op], 1
        while i + n < len(texts):
            nx = _head(texts[i + n].strip(), env)
            if not nx or lua.squeeze(nx[0]) != lua.squeeze(expr):
                break
            ops.append(nx[2])
            n += 1
        return {"k": "obj", "ref": ref, "var": expr, "decl": False, "guard": False, "ops": ops}, n
    return None


def _head(t: str, env: Env):
    m = _CALL_HEAD.match(t)
    if m:
        ref = env.ref(lua.squeeze(m.group(1)))
        op = _parse_op(m.group(3)) if ref else None
        if op:
            return m.group(1), ref, op
        return None
    g = _parse_gun(lua.squeeze(t), None)
    if g and env.ref(g[0]):
        return g[0], env.ref(g[0]), g[1]
    return None


def render_op(op: dict, var: str, quote: str) -> str:
    if op["k"] == "lua":
        return op.get("code", "")
    if op["k"] == "gun":
        cls = f", {op['cls']}" if str(op.get("cls", "")).strip() else ""
        return f"AddVehicleGunsWithRandomAffix({var}, {lua._q(op['gun'], quote)}, {op.get('n') or 1}{cls})"
    return f"{var}:{lua.render_block(OP_BY, op, quote)}"


def render_obj(b: dict, quote: str) -> str:
    var = b.get("var") or "obj"
    lines = []
    if b.get("decl", True):
        lines.append(("local " if b.get("local", True) else "") + f"{var} = {ref_expr(b.get('ref', PLAYER), quote)}")
    body = [ln for op in b.get("ops", []) for ln in render_op(op, var, quote).replace("\r\n", "\n").split("\n")]
    if b.get("guard", True):
        lines += [f"if {var} then"] + ["\t" + ln for ln in body] + ["end"]
    else:
        lines += body
    return "\n".join(lines)


def retarget(b: dict, ref: str):
    """Change the block's object: the source expression no longer fits, declare our own."""
    b["ref"] = ref
    if not b.get("decl") or not _VAR.match(b.get("var") or ""):
        b.update(decl=True, guard=True, local=True, var="obj")


# --- team ---

def _try_team(sq: str):
    m = _TEAM_RX.match(sq)
    if not m:
        return None
    a = split_args(m.group(4))
    if len(a) < 4:
        return None
    name, pos = _strlit(a[0]), vec(a[2])
    lst = re.fullmatch(r"\{(.*)\}", a[3])
    if name is None or pos is None or not _is_num(a[1]) or not lst:
        return None
    protos = [_strlit(p) for p in split_args(lst.group(1)) if p]
    if not protos or None in protos:
        return None
    walk = ""
    if len(a) > 4 and a[4] != "nil":
        walk = vec(a[4])
        if walk is None:
            return None
    if len(a) > 5 and a[5] not in ("1", "0", "nil"):
        return None
    return {"k": "team", "fn": m.group(3), "var": m.group(2) or "", "local": bool(m.group(1)) or not m.group(2),
            "name": name,
            "belong": a[1], "pos": pos, "protos": protos, "walk": walk,
            "wares": "1" if (len(a) > 5 and a[5] == "1") or m.group(3) == "TeamCreateWithWarez" else "0",
            "tail": ",".join(a[6:])}


def fmt_vec(v: str) -> str:
    p = (v.split() + ["0", "0", "0"])[:3]
    return f"CVector({p[0]}, {p[1]}, {p[2]})"


def render_team(b: dict, quote: str) -> str:
    args = [lua._q(b["name"], quote), str(b.get("belong") or 1002), fmt_vec(b.get("pos", "0 0 0")),
            "{" + ", ".join(lua._q(p, quote) for p in b.get("protos", [])) + "}"]
    fn = b.get("fn") or "TeamCreate"
    wares = b.get("wares") == "1"
    tail = b.get("tail", "")
    if fn == "TeamCreateWithWarez" and not wares:
        fn = "TeamCreate"
    if b.get("walk") or (wares and fn != "TeamCreateWithWarez") or tail:
        args.append(fmt_vec(b["walk"]) if b.get("walk") else "nil")
    if (wares and fn != "TeamCreateWithWarez") or tail:
        args.append("1" if wares else "nil")
    if tail:
        args.append(tail)
    head = (("local " if b.get("local", True) else "") + b["var"] + " = ") if b.get("var") else ""
    return f"{head}{fn}({', '.join(args)})"


# --- camera ---

def _id_or_raw(sq: str, env: Env) -> str:
    r = env.id(sq)
    return r if r else "=" + sq


def _try_fly(sq: str, env: Env):
    m = re.fullmatch(r"FlyLinked\((.*)\)", sq)
    if m:
        a = split_args(m.group(1))
        if len(a) >= 6 and _strlit(a[0]) is not None and all(_is_num(x) for x in a[2:5]):
            return {"k": "flylinked", "path": _strlit(a[0]), "base": _id_or_raw(a[1], env), "time": a[2], "fin": a[3],
                    "fout": a[4], "look": "" if a[5] == "nil" else _id_or_raw(a[5], env), "tail": ",".join(a[6:])}
    m = re.fullmatch(r"FlyAround\((.*)\)", sq)
    if m:
        a = split_args(m.group(1))
        if len(a) >= 8 and all(_is_num(x) for x in a[:4] + a[6:8]):
            return {"k": "flyaround", "phi": a[0], "theta": a[1], "radius": a[2], "time": a[3], "posexpr": a[4],
                    "target": _id_or_raw(a[5], env), "fin": a[6], "fout": a[7], "tail": ",".join(a[8:]), "dy": ""}
    return None


def render_flylinked(b: dict, quote: str) -> str:
    args = [lua._q(b.get("path", ""), quote), id_expr(b.get("base", PLAYER), quote), b.get("time") or "5",
            b.get("fin") or "0", b.get("fout") or "0", id_expr(b["look"], quote) if b.get("look") else "nil"]
    if b.get("tail"):
        args.append(b["tail"])
    return f"FlyLinked({', '.join(args)})"


_PRE = [r"localcamObj=(.+)", r"localcamPos=camObj:GetPosition\(\)", rf"camPos\.y=camPos\.y\+({NUM})",
        rf"camPos\.z=camPos\.z\+({NUM})"]


def render_flyaround(b: dict, quote: str) -> str:
    r, t = b.get("radius") or "25", b.get("time") or "6"
    fades = f"{b.get('fin') or '1'}, {b.get('fout') or '1'}" + (f", {b['tail']}" if b.get("tail") else "")
    head = f"FlyAround({b.get('phi') or '1'}, {b.get('theta') or '0'}, {r}, {t}, "
    if b.get("posexpr"):
        return f"{head}{b['posexpr']}, {id_expr(b.get('target', PLAYER), quote)}, {fades})"
    try:
        rr = float(r)
        dy = float(b.get("dy") or rr * 0.74)
    except ValueError:
        rr, dy = 25.0, 18.5
    dz = math.sqrt(max(rr * rr - dy * dy, 0.0)) if dy < rr else 0.0
    return "\n".join([f"local camObj = {ref_expr(b.get('target', PLAYER), quote)}",
                      "local camPos = camObj:GetPosition()",
                      f"camPos.y = camPos.y + {dy:g}", f"camPos.z = camPos.z + {dz:.4g}",
                      f"{head}camPos, camObj:GetId(), {fades})"])


def absorb_preamble(b: dict, prev: list[str], env: Env) -> bool:
    """Are the four lines before FlyAround our own camera-point preamble? Then absorb them."""
    if b.get("posexpr") != "camPos" or len(prev) != 4:
        return False
    ms = [re.fullmatch(rx, lua.squeeze(t)) for rx, t in zip(_PRE, prev)]
    if not all(ms):
        return False
    ref = env.direct_ref(ms[0].group(1))
    if not ref:
        return False
    b.update(posexpr="", target=ref, dy=ms[2].group(1))
    return True


# --- entry points ---

KINDS = ("obj", "team", "flylinked", "flyaround")


def try_parse(texts: list[str], i: int, env: Env):
    sq = lua.squeeze(texts[i])
    b = _try_team(sq)
    if b:
        env.note_block(b)
        return b, 1
    b = _try_fly(sq, env)
    if b:
        return b, 1
    return _try_obj(texts, i, env)


def render(b: dict, quote: str) -> str:
    return {"obj": render_obj, "team": render_team, "flylinked": render_flylinked,
            "flyaround": render_flyaround}[b["k"]](b, quote)


def describe(b: dict) -> str:
    k = b["k"]
    if k == "team":
        return f"Создание команды {b['name']} ({len(b.get('protos', []))})"
    if k == "obj":
        ops = ", ".join((OP_BY[o["k"]].title.lower() if o["k"] != "lua" else "Lua") for o in b.get("ops", [])[:3])
        return f"Обращение к {ref_title(b.get('ref', PLAYER))}: {ops}"
    if k == "flylinked":
        return f"Камера у {ref_title(b.get('base', ''))}, {b.get('time')} с"
    return f"Облёт {ref_title(b.get('target', ''))}, {b.get('time')} с"


def new_block(kind: str) -> dict:
    if kind == "team":
        return {"k": "team", "fn": "TeamCreate", "var": "", "local": True, "name": "NewTeam", "belong": "1002",
                "pos": "0 0 0", "protos": [], "walk": "", "wares": "0", "tail": ""}
    if kind == "obj":
        return {"k": "obj", "ref": PLAYER, "var": "obj", "decl": True, "local": True, "guard": True, "ops": []}
    if kind == "stop":
        return {"k": "obj", "ref": PLAYER, "var": "obj", "decl": True, "local": True, "guard": True,
                "ops": [dict(o) for o in STOP_PLAYER]}
    if kind == "flylinked":
        return {"k": "flylinked", "path": "", "base": PLAYER, "time": "8", "fin": "1", "fout": "1", "look": "",
                "tail": ""}
    return {"k": "flyaround", "phi": "1", "theta": "0", "radius": "25", "time": "6", "posexpr": "", "target": PLAYER,
            "fin": "1", "fout": "1", "tail": "", "dy": "18.5"}

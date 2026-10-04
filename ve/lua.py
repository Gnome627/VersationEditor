"""Conditions and actions as blocks.

Game scripts are plain Lua made mostly of the same calls. Those are shown as
blocks with fields; everything else stays a raw 'lua' block, untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OPS = ["==", "~=", ">=", "<=", ">", "<"]
OP_TITLES = {"==": "=", "~=": "≠", ">=": "≥", "<=": "≤", ">": ">", "<": "<"}


@dataclass
class Field:
    name: str
    type: str            # quest trigger qitem item book history strid var obj map belong int num text enum op
    choices: list | None = None   # enum: [(value, caption)]
    default: str = ""
    label: str = ""               # checkbox caption (type="check")


@dataclass
class Spec:
    kind: str
    title: str
    fields: list[Field]
    templates: dict[str, str] | str   # one template or {variant value: template}
    variant: str = ""                 # field that selects the template
    where: str = "all"                # dialog | trigger | all | none: where the block is offered
    group: str = ""
    ret: bool = False                 # the call returns an object: 'local v = Call(...)' is allowed

    def template_items(self):
        if isinstance(self.templates, str):
            return [("", self.templates)]
        return list(self.templates.items())


_PH = re.compile(r"\{(\w+)(?::(\w))?\}")


NUM = r"-?(?:\d+\.?\d*|\.\d+)"


def _compile(template: str, spec=None) -> re.Pattern:
    """Call template -> regex over whitespace-free text.

    {name:s} string, :n number, :v CVector, :q Quaternion, :a number or word,
    no type - identifier.
    """
    out, pos = [], 0
    if spec is not None and spec.ret:
        out.append(r"(?:(?P<_loc>local)?(?P<_var>[A-Za-z_]\w*)=)?")
    lit = template.replace(" ", "")
    for m in _PH.finditer(lit):
        out.append(re.escape(lit[pos:m.start()]))
        name, t = m.group(1), m.group(2) or "c"
        if name == "op":
            out.append(r"(?P<op>==|~=|>=|<=|>|<)")
        elif t == "s":
            out.append(rf"(?P<{name}>'[^']*'|\"[^\"]*\")")
        elif t == "n":
            out.append(rf"(?P<{name}>{NUM})")
        elif t == "v":
            out.append(rf"(?P<{name}>\(*CVector\([^()]*\)\)*)")
        elif t == "q":
            out.append(rf"(?P<{name}>Quaternion\([^()]*\))")
        elif t == "a":
            out.append(rf"(?P<{name}>[\w.\-]+)")
        else:
            out.append(rf"(?P<{name}>[A-Za-z_]\w*)")
        pos = m.end()
    out.append(re.escape(lit[pos:]))
    return re.compile("".join(out) + r"\Z")


def F(name, type_, choices=None, default=""):
    return Field(name, type_, choices, default)


QSTATES = [("Q_TAKEN", "взят"), ("Q_COMPLETED", "выполнен"), ("Q_FAILED", "провален"),
           ("Q_CANBEGIVEN", "можно выдать"), ("Q_UNKNOWN", "недоступен")]
OPF = F("op", "op", default="==")

COND_SPECS = [
    Spec("qstatus", "Статус квеста", [F("quest", "quest"), OPF, F("state", "enum", QSTATES, "Q_TAKEN")],
         "QuestStatus({quest:s}) {op} {state:c}", group="Квесты"),
    Spec("quest", "Квест", [F("quest", "quest"),
                            F("is", "enum", [("taken", "взят"), ("complete", "выполнен"), ("failed", "провален"),
                                             ("can", "можно выдать"), ("taken_nc", "взят и не завершён")], "taken")],
         {"taken": "IsQuestTaken({quest:s})", "complete": "IsQuestComplete({quest:s})",
          "failed": "IsQuestFailed({quest:s})", "can": "CanQuestBeGiven({quest:s})",
          "taken_nc": "IsQuestTakenAndNotComplete({quest:s})"}, variant="is", group="Квесты"),
    Spec("spoken", "Разговоров с NPC", [OPF, F("n", "int", default="0")],
         "GetCurNpc():GetSpokenCount() {op} {n:n}", where="dialog", group="Разговор"),
    Spec("money", "Денег у игрока", [F("op", "op", default=">="), F("n", "int", default="100")],
         "GetPlayerMoney() {op} {n:n}", group="Игрок"),
    Spec("qitem", "Есть квестовый предмет", [F("item", "qitem")], "IsQuestItemPresent({item:s})", group="Игрок"),
    Spec("items", "В кузове есть", [F("item", "item"), F("n", "int", default="1")],
         "HasPlayerAmountOfItems({item:s}, {n:n})", group="Игрок"),
    Spec("place", "В кузове есть место под", [F("item", "item"), F("n", "int", default="1")],
         "HasPlayerFreePlaceForItems({item:s}, {n:n})", group="Игрок"),
    Spec("book", "Есть книга", [F("book", "book")], "BookExists({book:s})", group="Игрок"),
    Spec("varint", "Переменная", [F("var", "var"), OPF, F("n", "int", default="0")],
         "GetVar({var:s}).AsInt {op} {n:n}", group="Мир"),
    Spec("varstr", "Переменная-строка", [F("var", "var"), OPF, F("value", "text")],
         "GetVar({var:s}).AsString {op} {value:s}", group="Мир"),
    Spec("tolerance", "Отношение", [F("a", "belong", default="1100"), F("b", "belong"), OPF,
                                    F("rs", "enum", [("RS_ENEMY", "враг"), ("RS_NEUTRAL", "нейтрал"),
                                                     ("RS_ALLY", "союзник"), ("RS_OWN", "свой")], "RS_ENEMY")],
         "GetTolerance({a:n}, {b:n}) {op} {rs:c}", group="Мир"),
    Spec("level", "Карта", [F("map", "map"), F("is", "enum", [("known", "известна"), ("visited", "посещена")], "known")],
         {"known": "IsLevelKnown({map:s})", "visited": "IsLevelVisited({map:s})"}, variant="is", group="Мир"),
]

ACT_SPECS = [
    Spec("end", "Завершить разговор", [], "EndConversation()", where="dialog", group="Разговор"),
    Spec("leave", "Выехать из города", [F("cut", "enum", [("", "с роликом"), ("true", "без ролика")])],
         {"": "LeaveTown()", "true": "LeaveTown(true)"}, variant="cut", where="dialog", group="Разговор"),
    Spec("spoken", "Разговоров с NPC =", [F("n", "int", default="1")],
         "GetCurNpc():SetSpokenCount({n:n})", where="dialog", group="Разговор"),
    Spec("quest", "Квест", [F("do", "enum", [("take", "взять"), ("complete_if", "выполнить, если взят"),
                                             ("complete", "выполнить"), ("fail_if", "провалить, если взят"),
                                             ("fail", "провалить")], "take"), F("quest", "quest")],
         {"take": "TakeQuest({quest:s})", "complete": "CompleteQuest({quest:s})",
          "complete_if": "CompleteQuestIfTaken({quest:s})", "fail": "FailQuest({quest:s})",
          "fail_if": "FailQuestIfTaken({quest:s})"}, variant="do", group="Квесты"),
    Spec("trigger", "Триггер", [F("do", "enum", [("on", "включить"), ("off", "выключить")], "on"), F("trigger", "trigger")],
         {"on": "TActivate({trigger:s})", "off": "TDeactivate({trigger:s})"}, variant="do", group="Мир"),
    Spec("selfoff", "Выключить этот триггер", [], "trigger:Deactivate()", where="trigger", group="Мир"),
    Spec("money", "Деньги", [F("n", "int", default="100")], "AddPlayerMoney({n:n})", group="Игрок"),
    Spec("qitem", "Квестовый предмет", [F("do", "enum", [("add", "дать"), ("del", "забрать")], "add"), F("item", "qitem")],
         {"add": "AddQuestItem({item:s})", "del": "RemoveQuestItem({item:s})"}, variant="do", group="Игрок"),
    Spec("items", "Груз", [F("do", "enum", [("add", "дать"), ("del", "забрать")], "add"), F("item", "item"),
                           F("n", "int", default="1")],
         {"add": "AddItemsToPlayerRepository({item:s}, {n:n})",
          "del": "RemoveItemsFromPlayerRepository({item:s}, {n:n})"}, variant="do", group="Игрок"),
    Spec("book", "Дать книгу", [F("book", "book")], "AddBook({book:s})", group="Игрок"),
    Spec("history", "Добавить запись в историю", [F("id", "history")], "AddHistory({id:s})", group="Игрок"),
    Spec("fading", "Показать всплывающее сообщение", [F("id", "strid")], "AddFadingMsgId({id:s})", group="Игрок"),
    Spec("var", "Переменная =", [F("var", "var"), F("n", "int", default="1")], "SetVar({var:s}, {n:n})", group="Мир"),
    Spec("varstr", "Переменная-строка =", [F("var", "var"), F("value", "text")], "SetVar({var:s}, {value:s})", group="Мир"),
    Spec("circle", "Показать круг на карте у объекта", [F("obj", "obj")], "ShowCircleOnMinimapByName({obj:s})", group="Мир"),
    Spec("tolerance", "Отношение", [F("a", "belong", default="1100"), F("b", "belong"),
                                    F("rs", "enum", [("RS_ENEMY", "враг"), ("RS_NEUTRAL", "нейтрал"),
                                                     ("RS_ALLY", "союзник")], "RS_ENEMY")],
         "SetTolerance({a:n}, {b:n}, {rs:c})", group="Мир"),
    Spec("music", "Включить музыку", [F("name", "text")], "PlayCustomMusic({name:s})", group="Мир"),
    Spec("msgbox", "Показать окно с сообщением", [F("id", "int", default="0")], "SpawnMessageBox({id:s})", where="trigger", group="Ролик"),
    Spec("cinemsg", "Показать реплику ролика", [F("id", "int", default="0"), F("delay", "num", default="0.25")],
         "AddCinematicMessage({id:n}, {delay:n})", where="trigger", group="Ролик"),
    Spec("cinestart", "Начать ролик: запустить пролёты выше", [], "StartCinematic()", where="trigger", group="Ролик"),
    Spec("neutral", "Запомнить отношения и сделать всех нейтральными", [], "SaveAllToleranceStatus(RS_NEUTRAL)", where="trigger",
         group="Ролик"),
    Spec("restore", "Вернуть запомненные отношения", [], "RestoreAllToleranceStatus()", where="trigger", group="Ролик"),
    Spec("cambehind", "Поставить камеру за машиной игрока", [], "SetCameraBehindPlayerVehicle()", where="trigger", group="Ролик"),
    Spec("fly", "Пролёт камеры по пути", [F("path", "campath"), F("aim", "hidden", default="0"),
                                  Field("time", "num", None, "5", "время, с"),
                                  Field("fin", "check", None, "1", "fade-in в начале"),
                                  Field("fout", "check", None, "1", "в конце")],
         "Fly({path:s}, CINEMATIC_NO_AIM, {aim:a}, {time:n}, {fin:a}, {fout:a})", where="trigger", group="Ролик"),
    Spec("stopmusic", "Остановить музыку", [], "StopPlayingCustomMusic()", group="Мир"),
    Spec("model", "Создать объект-модель (dummy)", [F("model", "actor", default="dweller_white"),
                                       Field("name", "text", None, "Actor01", "имя"), Field("pos", "vec", None, "", "точка"),
                                       Field("rot", "quat", None, "0.000 0.000 0.000 1.000", "поворот"),
                                       Field("skin", "int", None, "0", "скин")],
         "CreateNewDummyObject({model:s}, {name:s}, -1, -1, {pos:v}, {rot:q}, {skin:n})", where="trigger",
         group="Сцена", ret=True),
    Spec("vehicle", "Создать автомобиль без ИИ", [F("proto", "vehicle"), Field("name", "text", None, "ActorCar01", "имя"),
                               Field("pos", "vec", None, "", "точка"),
                               Field("belong", "belong", None, "1002", "группировка")],
         "CreateVehicleEx({proto:s}, {name:s}, {pos:v}, {belong:n})", where="trigger", group="Сцена", ret=True),
    Spec("godmode", "Неуязвимость игрока", [F("do", "enum", [("on", "включить"), ("off", "выключить")], "on")],
         {"on": "EnableGodMode()", "off": "DisableGodMode()"}, variant="do", where="trigger", group="Игрок"),
    Spec("passmap", "Перейти на карту", [F("map", "map"), F("loc", "text", default="")],
         "PassToMap({map:s}, {loc:s}, -1)", where="trigger", group="Мир"),
    Spec("console", "Команда консоли", [F("cmd", "text")], "RuleConsole({cmd:s})", where="trigger", group="Мир"),
    Spec("cinefilt", "Включить фильтры ролика", [], "CinemaFiltersUse()", where="none", group="Ролик"),
    Spec("gamefilt", "Вернуть фильтры игры", [], "GameFiltersUse()", where="none", group="Ролик"),
]

for _specs in (COND_SPECS, ACT_SPECS):
    for _s in _specs:
        _s._rx = [(v, t, _compile(t, _s)) for v, t in _s.template_items()]

COND_BY = {s.kind: s for s in COND_SPECS}
ACT_BY = {s.kind: s for s in ACT_SPECS}


def new_block(spec: Spec) -> dict:
    b = {"k": spec.kind}
    for f in spec.fields:
        b[f.name] = f.default
    return b


# --- lexing ---

def squeeze(s: str) -> str:
    """Drop whitespace outside string literals."""
    out, q = [], ""
    for ch in s:
        if q:
            out.append(ch)
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif not ch.isspace():
            out.append(ch)
    return "".join(out)


def _strip_parens(s: str) -> str:
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth, q, ok = 0, "", True
        for i, ch in enumerate(s):
            if q:
                if ch == q:
                    q = ""
            elif ch in "\"'":
                q = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(s) - 1:
                    ok = False
                    break
        if not ok:
            break
        s = s[1:-1].strip()
    return s


def _split_bool(s: str) -> list[tuple[str, str]] | None:
    """'a and b or c' -> [('', a), ('and', b), ('or', c)] at the top paren level."""
    parts, depth, q, start, join = [], 0, "", 0, ""
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if q:
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        elif depth == 0 and ch in "ao" and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_")):
            for kw in ("and", "or"):
                if s.startswith(kw, i) and (i + len(kw) >= n or not (s[i + len(kw)].isalnum() or s[i + len(kw)] == "_")):
                    parts.append((join, s[start:i]))
                    join, start = kw, i + len(kw)
                    i += len(kw) - 1
                    break
        i += 1
    if depth != 0 or q:
        return None
    parts.append((join, s[start:]))
    return parts


def _match(specs, text: str) -> dict | None:
    sq = squeeze(text)
    for spec in specs:
        for variant, _t, rx in spec._rx:
            m = rx.match(sq)
            if not m:
                continue
            b = {"k": spec.kind}
            types = {}
            for _v, t in spec.template_items():
                for pm in _PH.finditer(t):
                    types[pm.group(1)] = pm.group(2) or "c"
            for f in spec.fields:
                b[f.name] = f.default
            for name, val in m.groupdict().items():
                if name in ("_var", "_loc"):
                    continue
                typ = types.get(name)
                if val is not None and typ == "s":
                    val = val[1:-1]
                elif val is not None and typ in ("v", "q"):
                    nums = re.fullmatch(r"\(*\w+\((" + ",".join([NUM] * (3 if typ == "v" else 4)) + r")\)\)*", val)
                    if not nums:        # an expression instead of numbers: cannot be a block
                        b = None
                        break
                    val = nums.group(1).replace(",", " ")
                b[name] = val
            if b is None:
                continue
            if spec.ret and m.group("_var"):
                b["var"], b["local"] = m.group("_var"), bool(m.group("_loc"))
            if spec.variant:
                b[spec.variant] = variant
            # enum value not in the list (e.g. a mod constant): not ours
            for f in spec.fields:
                if f.type == "enum" and f.name != spec.variant and b[f.name] not in [c[0] for c in f.choices]:
                    b = None
                    break
            if b:
                return b
    return None


# --- conditions ---

def parse_condition(src: str) -> list[dict]:
    """Expression -> blocks, each with 'join' (and/or, empty for the first) and 'neg'."""
    s = _strip_parens(src or "")
    if not s:
        return []
    parts = _split_bool(s)
    if parts is None:
        return [{"k": "lua", "code": src.strip(), "join": "", "neg": False}]
    out = []
    for join, term in parts:
        t = _strip_parens(term)
        neg = False
        m = re.match(r"not\b\s*", t)
        while m:
            neg = not neg
            t = _strip_parens(t[m.end():])
            m = re.match(r"not\b\s*", t)
        b = None
        if not _has_bool(t):
            b = _match(COND_SPECS, t)
        elif _split_bool(t) is not None:
            b = {"k": "group", "items": parse_condition(t)}
        if b is None:
            b = {"k": "lua", "code": term.strip()}
            neg = False
        b["join"], b["neg"] = join, neg
        out.append(b)
    return out


def _has_bool(t: str) -> bool:
    p = _split_bool(t)
    return p is None or len(p) > 1


def _q(v: str, quote: str) -> str:
    other = '"' if quote == "'" else "'"
    return (other + v + other) if quote in v else (quote + v + quote)


def render_block(specs_by, b: dict, quote: str = "'") -> str:
    if b["k"] == "lua":
        return b.get("code", "")
    if b["k"] in ("obj", "team", "flylinked", "flyaround"):
        from . import scene
        return scene.render(b, quote)
    spec = specs_by[b["k"]]
    t = spec.templates if isinstance(spec.templates, str) else spec.templates.get(b.get(spec.variant, ""), "")
    if not t:
        t = spec.template_items()[0][1]

    def rep(m):
        name, typ = m.group(1), m.group(2) or "c"
        v = str(b.get(name, ""))
        if typ == "s":
            return _q(v, quote)
        if typ == "n":
            return v.strip() or "0"
        if typ == "v":
            p = (v.split() + ["0", "0", "0"])[:3]
            return f"CVector({p[0]}, {p[1]}, {p[2]})"
        if typ == "q":
            p = (v.split() + ["0", "0", "0", "1"])[:4]
            return f"Quaternion({p[0]}, {p[1]}, {p[2]}, {p[3]})"
        if typ == "a":
            return v.strip() or "0"
        return v
    head = (("local " if b.get("local", True) else "") + b["var"] + " = ") if spec.ret and b.get("var") else ""
    return head + _PH.sub(rep, t)


def render_condition(blocks: list[dict], quote: str = "'") -> str:
    out = []
    multi = len(blocks) > 1
    for i, b in enumerate(blocks):
        if b["k"] == "group":
            t = render_condition(b.get("items", []), quote)
            if t and (multi or b.get("neg")):
                t = "(" + t + ")"
        else:
            t = render_block(COND_BY, b, quote).strip()
        if not t:
            continue
        if b["k"] == "lua" and multi and _has_bool(t):
            t = "(" + t + ")"
        if b.get("neg"):
            simple = re.fullmatch(r"[\w:.]+\([^()]*\)", t) or (b["k"] == "group" and t.startswith("("))
            t = "not " + (t if simple else "(" + t + ")")
        if out:
            out.append(b.get("join") or "and")
        out.append(t)
    return " ".join(out)


# --- actions ---

_OPENERS = {"if", "function", "repeat"}
_WORD = re.compile(r"[A-Za-z_]\w*")


def split_statements(src: str) -> list[tuple[int, int]]:
    """Top-level statement spans (start, end) in src."""
    spans, n = [], len(src)
    i = start = 0
    paren = block = 0
    pending_do = False

    def close(end):
        nonlocal start
        if src[start:end].strip():
            a = start
            while src[a].isspace():
                a += 1
            b = end
            while src[b - 1].isspace():
                b -= 1
            spans.append((a, b))
        start = end

    while i < n:
        ch = src[i]
        if ch in "\"'":
            j = i + 1
            while j < n and src[j] != ch:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if src.startswith("--", i):
            if src.startswith("--[[", i):
                j = src.find("]]", i)
                i = n if j < 0 else j + 2
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        if src.startswith("[[", i):
            j = src.find("]]", i)
            i = n if j < 0 else j + 2
            continue
        if ch in "({[":
            paren += 1
        elif ch in ")}]":
            paren -= 1
            if paren == 0 and block == 0 and ch == ")":
                # call ended; a following name starts a new statement
                j = i + 1
                while j < n and src[j] in " \t":
                    j += 1
                m = _WORD.match(src, j)
                if m and m.group(0) not in ("and", "or", "then", "do", "end", "else", "elseif", "until"):
                    close(i + 1)
        elif ch == ";" and paren == 0 and block == 0:
            close(i)
            start = i + 1
        elif ch == "\n" and paren == 0 and block == 0:
            # a line may continue with an operator: the pieces then merge into one lua block
            close(i)
        elif ch.isalpha() or ch == "_":
            m = _WORD.match(src, i)
            w = m.group(0)
            if i == 0 or not (src[i - 1].isalnum() or src[i - 1] in "_."):
                if w in _OPENERS:
                    block += 1
                elif w in ("for", "while"):
                    block += 1
                    pending_do = True
                elif w == "do":
                    if pending_do:
                        pending_do = False
                    else:
                        block += 1
                elif w in ("end", "until"):
                    block = max(0, block - 1)
                    if block == 0 and paren == 0 and w == "end":
                        i = m.end()
                        close(i)
                        continue
            i = m.end()
            continue
        i += 1
    close(n)
    return spans


def parse_actions(src: str) -> list[dict]:
    """Script -> blocks; adjacent unrecognised statements merge into one lua block."""
    from . import scene
    src = src or ""
    spans = split_statements(src)
    texts = [src[a:b] for a, b in spans]
    env = scene.Env()
    items: list = []                 # block or ("raw", start, end)
    i = 0
    while i < len(spans):
        got = scene.try_parse(texts, i, env)
        if got:
            blk, used = got
            if blk["k"] == "flyaround" and len(items) >= 4 and all(isinstance(x, tuple) for x in items[-4:]):
                if scene.absorb_preamble(blk, [src[x[1]:x[2]] for x in items[-4:]], env):
                    del items[-4:]
            items.append(blk)
            i += used
            continue
        blk = _match(ACT_SPECS, texts[i])
        if blk is not None:
            env.note_block(blk)
            items.append(blk)
        else:
            env.note(squeeze(texts[i]))
            items.append(("raw",) + spans[i])
        i += 1
    out: list[dict] = []
    for it in items:
        if isinstance(it, tuple):
            if out and out[-1].get("_span"):
                out[-1]["_span"][1] = it[2]
            else:
                out.append({"k": "lua", "_span": [it[1], it[2]]})
        else:
            out.append(it)
    for b in out:
        if "_span" in b:
            a, e = b.pop("_span")
            b["code"] = _dedent(src, a, e)
    return out


def _dedent(src: str, a: int, b: int) -> str:
    ls = src.rfind("\n", 0, a) + 1
    first_indent = src[ls:a] if not src[ls:a].strip() else ""
    lines = src[a:b].replace("\r\n", "\n").split("\n")
    out = [lines[0]]
    for ln in lines[1:]:
        out.append(ln[len(first_indent):] if ln.startswith(first_indent) else ln.lstrip() if not ln.strip() else ln)
    return "\n".join(out)


def render_actions(blocks: list[dict], quote: str = "'", multiline: bool = False,
                   indent: str = "\t\t\t", nl: str = "\r\n") -> str:
    parts = [render_block(ACT_BY, b, quote) for b in blocks]
    parts = [p for p in parts if p.strip()]
    if not multiline:
        return "; ".join(p.strip().rstrip(";").replace("\r\n", " ").replace("\n", " ") for p in parts)
    lines = []
    for p in parts:
        for ln in p.replace("\r\n", "\n").split("\n"):
            lines.append(indent + ln if ln.strip() else "")
    return nl + nl.join(lines) + nl + indent[:-1]


def describe(specs_by, b: dict, names=None) -> str:
    """One-line block caption (graph nodes, preview)."""
    if b["k"] == "group":
        inner = ""
        for i, it in enumerate(b.get("items", [])):
            inner += ((" или " if it.get("join") == "or" else " и ") if i else "") + describe(specs_by, it)
        return ("не " if b.get("neg") else "") + "(" + inner + ")"
    if b["k"] == "lua":
        code = b.get("code", "").strip()
        n = code.count("\n") + 1
        return "Lua · " + (code if n == 1 and len(code) < 60 else f"{n} стр.")
    if b["k"] in ("obj", "team", "flylinked", "flyaround"):
        from . import scene
        return scene.describe(b)
    spec = specs_by[b["k"]]
    bits = [spec.title]
    for f in spec.fields:
        v = str(b.get(f.name, ""))
        if f.type == "enum":
            v = dict(f.choices).get(v, v)
        elif f.type == "op":
            v = OP_TITLES.get(v, v)
        elif f.type in ("hidden", "check", "vec", "quat"):
            v = ""
        if v:
            bits.append(v)
    return ("не " if b.get("neg") else "") + " ".join(bits)

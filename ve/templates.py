"""Templates: ready-made sets of blocks for common constructs.

A template is just several ordinary blocks inserted at once. Points are taken
from the centre of the visible map area.
"""
from __future__ import annotations

from . import scene

P = scene.PLAYER


def _ctx(index):
    """Returns pt(dx, dz): a point offset from the visible map centre, on the ground."""
    cx, cz = index.center() if index is not None and index.center else (0.0, 0.0)

    def pt(dx: float = 0.0, dz: float = 0.0) -> str:
        x, z = cx + dx, cz + dz
        h = index.height(x, z) if index is not None else None
        return f"{x:.3f} {h if h is not None else 0:.3f} {z:.3f}"
    return pt


def _name(index, base: str) -> str:
    m = index.current_map if index is not None else None
    return m.unique_trigger(base) if m is not None else base


def _obj(ref: str, ops: list[dict]) -> dict:
    b = scene.new_block("obj")
    b["ref"], b["ops"] = ref, ops
    return b


def _team(index, pt, protos, walk: str = "") -> dict:
    b = scene.new_block("team")
    choices = index.choices("vehicle") if index is not None else []
    first = choices[0] if choices else "Sml101"
    b.update(name=_name(index, "NewTeam"), pos=pt(), protos=[p if p in choices or not choices else first for p in protos],
             walk=walk)
    return b


def _fly(path: str = "", time: str = "5") -> dict:
    return {"k": "fly", "path": path, "aim": "0", "time": time, "fin": "1", "fout": "1"}


def _msg(index) -> dict:
    m = index.current_map if index is not None else None
    return {"k": "cinemsg", "id": m.new_message_id() if m is not None else "0", "delay": "0.5"}


# --- trigger ---

def team_placed(index):
    pt = _ctx(index)
    t = _team(index, pt, ["Sml101", "Sml201"])
    return [t,
            _obj(f"{t['name']}_vehicle_0", [{"k": "pos", "pos": pt(0, 0)}, {"k": "rot", "rot": scene.yaw_quat(0)}]),
            _obj(f"{t['name']}_vehicle_1", [{"k": "pos", "pos": pt(8, 0)}, {"k": "rot", "rot": scene.yaw_quat(0)}])]


def team_tactic(index):
    pt = _ctx(index)
    t = _team(index, pt, ["Sml101", "Sml101"])
    tactics = index.choices("tactic") if index is not None else []
    name = "TeamTacticTerroristsR1M1" if "TeamTacticTerroristsR1M1" in tactics or not tactics else tactics[0]
    return [t, _obj(t["name"], [{"k": "tactic", "name": name}, {"k": "adjust"}])]


def team_patrol(index):
    pt = _ctx(index)
    t = _team(index, pt, ["Sml101"])
    return [t, _obj(t["name"], [{"k": "stackopen"}, {"k": "dest", "pos": pt(60, 0)}, {"k": "dest", "pos": pt(60, 60)},
                                {"k": "dest", "pos": pt(0, 60)}, {"k": "stackloop"}, {"k": "stackclose"}])]


def team_boss(index):
    pt = _ctx(index)
    t = _team(index, pt, ["Sml101"])
    return [t, _obj(f"{t['name']}_vehicle_0", [{"k": "mod", "stat": "maxhp", "val": "= 2500"},
                                               {"k": "mod", "stat": "hp", "val": "= 2500"},
                                               {"k": "gun", "gun": "hornet01", "n": "3", "cls": "8"}])]


def rolik_flyaround(index):
    return [{"k": "neutral"}, scene.new_block("stop"), scene.new_block("flyaround"), {"k": "cinestart"}, _msg(index)]


def rolik_path(index):
    return [{"k": "neutral"}, scene.new_block("stop"), _fly(), {"k": "cinestart"}, _msg(index)]


def rolik_two_shots(index):
    a, b = _fly(time="6"), _fly(time="6")
    a["fout"], b["fin"] = "0", "0"          # fade-in only at the start of the first and the end of the last
    return [{"k": "neutral"}, scene.new_block("stop"), a, b, {"k": "cinestart"}, _msg(index)]


def rolik_linked(index):
    f = scene.new_block("flylinked")
    f["look"] = P
    return [{"k": "neutral"}, f, {"k": "cinestart"}]


def rolik_end(index):
    return [{"k": "restore"}, {"k": "stopmusic"}, {"k": "cambehind"}]


def actor_car(index):
    pt = _ctx(index)
    name = _name(index, "ActorCar01")
    return [{"k": "vehicle", "proto": "Sml101", "name": name, "pos": pt(), "belong": "1002"},
            _obj(name, [{"k": "rot", "rot": scene.yaw_quat(0)}, {"k": "path", "path": ""}, {"k": "throttle", "n": "1"},
                        {"k": "vel", "n": "10"}])]


def actor_human(index):
    pt = _ctx(index)
    name = _name(index, "Actor01")
    return [{"k": "model", "model": "dweller_white", "name": name, "pos": pt(), "rot": scene.yaw_quat(0), "skin": "0"},
            _obj(name, [{"k": "anim", "a": "AT_RESERVED1"}, {"k": "nextanim", "a": "AT_RESERVED1", "b": "AT_STAND1"}])]


def actor_remove(index):
    return [_obj("Actor01", [{"k": "remove"}])]


def player_drive(index):
    pt = _ctx(index)
    return [_obj(P, [{"k": "pos", "pos": pt()}, {"k": "rot", "rot": scene.yaw_quat(0)}, {"k": "custom", "on": "true"},
                     {"k": "path", "path": ""}, {"k": "custom", "on": "false"}, {"k": "throttle", "n": "1"},
                     {"k": "vel", "n": "10"}])]


def player_endpath(index):
    return [_obj(P, [{"k": "custom", "on": "true"}, {"k": "endpath2", "path": ""}, {"k": "custom", "on": "false"}])]


TRIGGER = [
    ("Команда с расстановкой машин по точкам", team_placed),
    ("Команда с тактикой", team_tactic),
    ("Команда с маршрутом патруля по кругу", team_patrol),
    ("Команда из одной усиленной машины с добычей", team_boss),
    None,
    ("Ролик: облёт камерой вокруг игрока и реплика", rolik_flyaround),
    ("Ролик: пролёт камеры по пути и реплика", rolik_path),
    ("Ролик: два пролёта подряд и реплика", rolik_two_shots),
    ("Ролик: камера у игрока следит за ним", rolik_linked),
    ("Конец ролика: вернуть отношения, музыку и камеру", rolik_end),
    None,
    ("Актёр-автомобиль едет по пути", actor_car),
    ("Актёр-человек с жестом", actor_human),
    ("Убрать актёра, если он есть", actor_remove),
    ("Переставить игрока и пустить по пути", player_drive),
    ("Игрока в конец пути после ролика", player_endpath),
]

# --- dialog ---

DIALOG = [
    ("Выдать квест и закончить разговор",
     lambda index: [{"k": "quest", "do": "take", "quest": ""}, {"k": "spoken", "n": "1"}, {"k": "end"}]),
    ("Принять выполненный квест с наградой",
     lambda index: [{"k": "quest", "do": "complete_if", "quest": ""}, {"k": "money", "n": "100"}, {"k": "end"}]),
    ("Забрать квестовый предмет и заплатить",
     lambda index: [{"k": "qitem", "do": "del", "item": ""}, {"k": "money", "n": "100"}, {"k": "end"}]),
]

# --- object access ---

OPS = [
    ("Остановить", lambda index: [dict(o) for o in scene.STOP_PLAYER]),
    ("Пустить по пути", lambda index: [{"k": "path", "path": ""}, {"k": "throttle", "n": "1"}, {"k": "vel", "n": "10"}]),
    ("Поставить в точку и повернуть",
     lambda index: [{"k": "pos", "pos": _ctx(index)()}, {"k": "rot", "rot": scene.yaw_quat(0)}]),
    ("Усилить: здоровье 2500", lambda index: [{"k": "mod", "stat": "maxhp", "val": "= 2500"},
                                               {"k": "mod", "stat": "hp", "val": "= 2500"}]),
    ("Тактика команды", lambda index: [{"k": "tactic", "name": "TeamTacticTerroristsR1M1"}, {"k": "adjust"}]),
    ("Маршрут патруля по кругу",
     lambda index: [{"k": "stackopen"}, {"k": "dest", "pos": _ctx(index)(60, 0)}, {"k": "dest", "pos": _ctx(index)(0, 60)},
                    {"k": "stackloop"}, {"k": "stackclose"}]),
]


def for_list(mode: str, where: str):
    if mode == "ops":
        return OPS
    if mode == "act" and where == "trigger":
        return TRIGGER
    if mode == "act" and where == "dialog":
        return DIALOG
    return []

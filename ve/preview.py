"""Preview: walk a dialog as in game and log everything that happens.

The world is a model: quest states, money, items, variables, spoken count.
Blocks are executed; raw lua is only logged and counts as true in conditions.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from . import lua
from .widgets import Panel, clear_layout

Q_NAMES = {"Q_UNKNOWN": "недоступен", "Q_CANBEGIVEN": "можно выдать", "Q_TAKEN": "взят",
           "Q_COMPLETED": "выполнен", "Q_FAILED": "провален"}


class World:
    def __init__(self, quests):
        self.quests = quests
        self.reset()

    def reset(self):
        self.status: dict[str, str] = {}
        self.money = 1000
        self.spoken: dict[str, int] = {}
        self.vars: dict[str, str] = {}
        self.items: dict[str, int] = {}
        self.qitems: set[str] = set()
        self.books: set[str] = set()
        self.log: list[str] = []
        self.npc = ""
        self.ended = False

    # --- quests ---
    def qstatus(self, q: str) -> str:
        s = self.status.get(q)
        if s:
            return s
        el = self.quests.items.get(q)
        if el is None:
            return "Q_UNKNOWN"
        prec = el.get("PrecedingQuests").split()
        if not prec:
            return "Q_CANBEGIVEN"
        mode, state = (el.get("ConditionToGive") or "all complete").split()[:2]
        want = {"complete": "Q_COMPLETED", "taken": "Q_TAKEN", "failed": "Q_FAILED"}.get(state, "Q_COMPLETED")
        hits = [self.status.get(p) == want or (state == "taken" and self.status.get(p) == "Q_COMPLETED") for p in prec]
        return "Q_CANBEGIVEN" if (all(hits) if mode == "all" else any(hits)) else "Q_UNKNOWN"

    def _set_status(self, q: str, st: str, why: str):
        if q not in self.quests.items:
            self.log.append(f"! квеста «{q}» нет в quests.xml")
            return
        self.status[q] = st
        self.log.append(f"{why}: {self.quests.title(q)}")
        hook = {"Q_TAKEN": "OnTake", "Q_COMPLETED": "OnComplete", "Q_FAILED": "OnFail"}[st]
        code = self.quests.items[q].get(hook)
        if code:
            self.run(lua.parse_actions(code))
        parent = self.quests.parent_of(q)
        if parent and self.status.get(parent) == "Q_TAKEN":
            kids = [self.status.get(k) for k in self.quests.children_of(parent)]
            mode = self.quests.items[parent].get("SubQuestsCondition") or "and"
            if mode == "and" and "Q_FAILED" in kids:
                self._set_status(parent, "Q_FAILED", "Провален квест")
            elif (mode == "and" and all(k == "Q_COMPLETED" for k in kids)) or (mode != "and" and "Q_COMPLETED" in kids):
                self._set_status(parent, "Q_COMPLETED", "Выполнен квест")
        self._automatic()

    def _automatic(self):
        for name, el in self.quests.items.items():
            if el.get("Automatic") == "1" and name not in self.status and el.get("PrecedingQuests") \
                    and self.qstatus(name) == "Q_CANBEGIVEN":
                parent = self.quests.parent_of(name)
                if parent is None or self.status.get(parent) == "Q_TAKEN":
                    self._set_status(name, "Q_TAKEN", "Взят сам квест")

    def take(self, q: str):
        if self.qstatus(q) != "Q_CANBEGIVEN":
            self.log.append(f"! TakeQuest: квест «{q}» сейчас {Q_NAMES[self.qstatus(q)]} — движок его не выдаст")
            return
        self._set_status(q, "Q_TAKEN", "Взят квест")
        for k in self.quests.children_of(q):
            if self.quests.items[k].get("Automatic") == "1" and k not in self.status and self.qstatus(k) == "Q_CANBEGIVEN":
                self._set_status(k, "Q_TAKEN", "Взят сам квест")

    # --- conditions ---
    @staticmethod
    def _cmp(a, op, b) -> bool:
        return {"==": a == b, "~=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]

    def test(self, blocks: list[dict]) -> bool:
        """Lua: 'and' binds tighter than 'or'."""
        result, acc = False, True
        for i, b in enumerate(blocks):
            v = self._test_one(b)
            if i and b.get("join") == "or":
                result = result or acc
                acc = v
            else:
                acc = acc and v
        return result or acc

    def _test_one(self, b: dict) -> bool:
        k = b["k"]
        if k == "group":
            v = self.test(b.get("items", []))
        elif k == "lua":
            self.log.append(f"? условие Lua не вычисляется, считаю истинным: {b.get('code', '').strip()}")
            v = True
        elif k == "qstatus":
            v = self._cmp(self.qstatus(b["quest"]), b["op"], b["state"]) if b["op"] in ("==", "~=") else False
        elif k == "quest":
            s = self.qstatus(b["quest"])
            v = {"taken": s in ("Q_TAKEN", "Q_COMPLETED", "Q_FAILED"), "complete": s == "Q_COMPLETED",
                 "failed": s == "Q_FAILED", "can": s == "Q_CANBEGIVEN", "taken_nc": s == "Q_TAKEN"}[b["is"]]
        elif k == "spoken":
            v = self._cmp(self.spoken.get(self.npc, 0), b["op"], int(float(b["n"])))
        elif k == "money":
            v = self._cmp(self.money, b["op"], float(b["n"]))
        elif k == "qitem":
            v = b["item"] in self.qitems
        elif k == "items":
            v = self.items.get(b["item"], 0) >= int(float(b["n"]))
        elif k == "place":
            v = True
        elif k == "book":
            v = b["book"] in self.books
        elif k == "varint":
            v = self._cmp(int(float(self.vars.get(b["var"], 0) or 0)), b["op"], int(float(b["n"])))
        elif k == "varstr":
            v = self._cmp(str(self.vars.get(b["var"], "")), b["op"], b["value"])
        else:
            self.log.append(f"? «{lua.describe(lua.COND_BY, b)}» в предпросмотре считается истинным")
            v = True
        return (not v) if b.get("neg") else v

    # --- actions ---
    def run(self, blocks: list[dict]):
        for b in blocks:
            k = b["k"]
            if k == "lua":
                code = b.get("code", "").strip()
                if "EndConversation" in code or "LeaveTown" in code:
                    self.ended = True
                self.log.append(f"Lua: {code}")
            elif k == "end":
                self.ended = True
                self.log.append("Разговор окончен")
            elif k == "leave":
                self.ended = True
                self.log.append("Игрок выезжает из города")
            elif k == "spoken":
                self.spoken[self.npc] = int(float(b["n"]))
                self.log.append(f"Счётчик разговоров = {b['n']}")
            elif k == "quest":
                q, do = b["quest"], b["do"]
                s = self.qstatus(q)
                if do == "take":
                    self.take(q)
                elif do in ("complete", "complete_if"):
                    if s == "Q_TAKEN":
                        self._set_status(q, "Q_COMPLETED", "Выполнен квест")
                    elif do == "complete":
                        self.log.append(f"! CompleteQuest: квест «{q}» не взят ({Q_NAMES[s]})")
                else:
                    if s == "Q_TAKEN":
                        self._set_status(q, "Q_FAILED", "Провален квест")
                    elif do == "fail":
                        self.log.append(f"! FailQuest: квест «{q}» не взят ({Q_NAMES[s]})")
            elif k == "money":
                n = int(float(b["n"]))
                self.money += n
                self.log.append(f"Деньги {n:+d} → {self.money}")
            elif k == "qitem":
                (self.qitems.add if b["do"] == "add" else self.qitems.discard)(b["item"])
                self.log.append(("Получен предмет " if b["do"] == "add" else "Отдан предмет ") + b["item"])
            elif k == "items":
                n = int(float(b["n"])) * (1 if b["do"] == "add" else -1)
                self.items[b["item"]] = self.items.get(b["item"], 0) + n
                self.log.append(f"Груз {b['item']} {n:+d}")
            elif k == "book":
                self.books.add(b["book"])
                self.log.append(f"Получена книга {b['book']}")
            elif k in ("var", "varstr"):
                self.vars[b["var"]] = b.get("n", b.get("value", ""))
                self.log.append(f"Переменная {b['var']} = {self.vars[b['var']]}")
            else:
                self.log.append(lua.describe(lua.ACT_BY, b))


class PreviewDialog(QDialog):
    def __init__(self, app, start: str, parent=None):
        super().__init__(parent)
        self.app = app
        self.d = app.dialogs
        self.start = start
        self.world = World(app.quests)
        self.setWindowTitle("Предпросмотр диалога")
        self.resize(900, 560)
        self.setObjectName("root")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        talk = Panel(margins=(8, 8, 8, 8), spacing=8)
        self.who = QLabel()
        self.who.setObjectName("head")
        talk.lay.addWidget(self.who)
        self.npc_text = QLabel()
        self.npc_text.setWordWrap(True)
        self.npc_text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.npc_text.setMinimumHeight(110)
        self.npc_text.setStyleSheet("font-size: 10.5pt;")
        talk.lay.addWidget(self.npc_text)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #7a7b7e;")
        talk.lay.addWidget(line)
        self.options = QVBoxLayout()
        self.options.setSpacing(4)
        box = QWidget()
        box.setLayout(self.options)
        talk.lay.addWidget(box)
        talk.lay.addStretch(1)
        row = QHBoxLayout()
        again = QPushButton("Заговорить снова")
        again.setToolTip("Мир остаётся как есть — видно, что NPC скажет во второй раз")
        again.clicked.connect(self.begin)
        fresh = QPushButton("Новая игра")
        fresh.clicked.connect(lambda: (self.world.reset(), self.begin()))
        row.addWidget(again)
        row.addWidget(fresh)
        row.addStretch(1)
        talk.lay.addLayout(row)
        lay.addWidget(talk, 3)

        logp = Panel(margins=(8, 8, 8, 8))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFrameShape(QFrame.NoFrame)
        self.log.setStyleSheet("border: none; border-image: none; background: transparent;")
        logp.lay.addWidget(self.log)
        lay.addWidget(logp, 2)
        self.begin()

    def _flush(self):
        if self.world.log:
            self.log.appendPlainText("\n".join("   " + l for l in self.world.log))
            self.world.log.clear()

    def begin(self):
        w = self.world
        w.ended = False
        users = self.app.index.hello_users().get(self.start, [])
        entries = [self.start]
        w.npc = "NPC"
        if users:
            mp, npc = users[0]
            w.npc = npc
            m = self.app.map(mp)
            el = m.objects.get(npc)
            if el is not None and el.get("helloReplyNames"):
                entries = el.get("helloReplyNames").split()
            self.who.setText(m.full_name(npc) or npc)
        else:
            self.who.setText("")
        self.log.appendPlainText("— разговор —")
        self._say_first(entries)

    def _say_first(self, names: list[str]):
        """The engine takes the first reply in the list whose condition is true."""
        w = self.world
        for n in names:
            el = self.d.items.get(n)
            if el is None:
                w.log.append(f"! реплики «{n}» нет")
                continue
            if el.get("role") != "NPC":
                continue
            if w.test(lua.parse_condition(el.get("scriptCondition"))):
                self._npc(n)
                return
        self._flush()
        self._end("NPC нечего сказать: ни у одной реплики не выполнено условие.")

    def _npc(self, name: str):
        w = self.world
        el = self.d.items[name]
        self.npc_text.setText(el.get("text"))
        self.log.appendPlainText(f"NPC: {el.get('text')}")
        w.run(lua.parse_actions(el.get("scriptResult")))
        self._flush()
        self._clear()
        if w.ended:
            self._end()
            return
        nxt = [self.d.items[n] for n in self.d.next_of(name) if n in self.d.items]
        shown = 0
        for o in nxt:
            if o.get("role") != "PLAYER":
                continue
            if not w.test(lua.parse_condition(o.get("scriptCondition"))):
                continue
            b = QPushButton(o.get("text") or o.get("name"))
            b.setStyleSheet("text-align: left; padding: 3px 8px;")
            b.clicked.connect(lambda _=False, n=o.get("name"): self._choose(n))
            self.options.addWidget(b)
            shown += 1
        self._flush()
        if not shown:
            npc_next = [o.get("name") for o in nxt if o.get("role") == "NPC"]
            if npc_next:
                self._say_first(npc_next)
            else:
                self._end("У игрока нет ни одного ответа — в игре разговор здесь зависнет." if not nxt
                          else "Все ответы игрока скрыты условиями.")

    def _choose(self, name: str):
        w = self.world
        el = self.d.items[name]
        self.log.appendPlainText(f"Игрок: {el.get('text')}")
        w.run(lua.parse_actions(el.get("scriptResult")))
        self._flush()
        if w.ended:
            self._clear()
            self._end()
            return
        nxt = self.d.next_of(name)
        if not nxt:
            self._clear()
            self._end("Реплика игрока без продолжения и без «Завершить разговор».")
            return
        self._say_first(nxt)

    def _clear(self):
        clear_layout(self.options)

    def _end(self, note: str = ""):
        self._clear()
        if note:
            self.log.appendPlainText("   ! " + note)
        l = QLabel("Разговор окончен." if not note else note)
        l.setWordWrap(True)
        l.setObjectName("dim")
        self.options.addWidget(l)

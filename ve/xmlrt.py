"""Lossless XML: what is not touched is written back byte for byte.

Game files were edited by hand and by several tools, so their formatting varies.
Every node keeps its source text; only the opening tag of an element whose
attributes changed is rebuilt.
"""
from __future__ import annotations

import re

_NAME = r"[A-Za-z_:][\w:.\-]*"
_ATTR_RE = re.compile(r"(" + _NAME + r")\s*=\s*(\"[^\"]*\"|'[^']*')")
_TAG_RE = re.compile(r"<(" + _NAME + r")")
_ENT = {"&quot;": '"', "&apos;": "'", "&lt;": "<", "&gt;": ">", "&amp;": "&"}
_ENT_RE = re.compile(r"&(?:quot|apos|lt|gt|amp|#\d+|#x[0-9a-fA-F]+);")


class XmlError(ValueError):
    pass


def unescape(s: str) -> str:
    def rep(m):
        t = m.group(0)
        if t in _ENT:
            return _ENT[t]
        return chr(int(t[3:-1], 16) if t[2] in "xX" else int(t[2:-1]))
    return _ENT_RE.sub(rep, s) if "&" in s else s


def escape_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
            .replace("\r\n", "&#10;").replace("\n", "&#10;"))


def escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;")


class Text:
    """Text between tags, whitespace included."""
    __slots__ = ("raw", "parent")

    def __init__(self, raw: str):
        self.raw = raw
        self.parent = None

    def dump(self) -> str:
        return self.raw


class Comment:
    __slots__ = ("raw", "parent")

    def __init__(self, text: str = "", raw: str | None = None):
        self.raw = raw if raw is not None else "<!--" + text + "-->"
        self.parent = None

    @property
    def text(self) -> str:
        return self.raw[4:-3]

    @text.setter
    def text(self, v: str):
        self.raw = "<!--" + v + "-->"

    def dump(self) -> str:
        return self.raw


class Raw:
    """<?xml?>, DOCTYPE, CDATA: kept as is."""
    __slots__ = ("raw", "parent")

    def __init__(self, raw: str):
        self.raw = raw
        self.parent = None

    def dump(self) -> str:
        return self.raw


class Element:
    __slots__ = ("tag", "attrs", "children", "parent", "_open", "_close", "_selfclose", "nl", "inline")

    def __init__(self, tag: str, attrs: dict | None = None):
        self.tag = tag
        self.attrs: dict[str, str] = dict(attrs or {})
        self.children: list = []
        self.parent: Element | None = None
        self._open: str | None = None      # source opening tag; None means rebuild
        self._close: str | None = None
        self._selfclose = True
        self.nl = "\r\n"
        self.inline: bool | None = None    # new tag on one line; None means decide by length

    # --- attributes ---
    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    def set(self, name: str, value: str | None):
        """Set an attribute; None removes it; unchanged values keep the source tag."""
        if value is None:
            if name in self.attrs:
                del self.attrs[name]
                self._open = None
            return
        if self.attrs.get(name) != value:
            self.attrs[name] = value
            self._open = None

    def set_opt(self, name: str, value: str):
        """Empty value means no attribute, as the originals write it."""
        self.set(name, value if value else None)

    def reorder(self, order: list[str]):
        new = {k: self.attrs[k] for k in order if k in self.attrs}
        new.update({k: v for k, v in self.attrs.items() if k not in new})
        if list(new) != list(self.attrs):
            self.attrs = new
            self._open = None

    # --- children ---
    def elements(self, tag: str | None = None) -> list["Element"]:
        return [c for c in self.children if isinstance(c, Element) and (tag is None or c.tag == tag)]

    def find(self, tag: str) -> "Element | None":
        for c in self.children:
            if isinstance(c, Element) and c.tag == tag:
                return c
        return None

    def iter(self, tag: str | None = None):
        for c in self.children:
            if isinstance(c, Element):
                if tag is None or c.tag == tag:
                    yield c
                yield from c.iter(tag)

    def depth(self) -> int:
        d, p = 0, self.parent
        while p is not None and p.tag != "#root":
            d, p = d + 1, p.parent
        return d

    @property
    def text(self) -> str:
        return unescape("".join(c.raw for c in self.children if isinstance(c, Text)))

    @text.setter
    def text(self, value: str):
        self.children = [c for c in self.children if not isinstance(c, Text)]
        t = Text(escape_text(value))
        t.parent = self
        self.children.insert(0, t)
        self._selfclose = False

    def _adopt(self, node):
        node.parent = self
        if isinstance(node, Element):
            node.nl = self.nl
        return node

    def _tail_ws(self) -> int:
        """Index of the trailing whitespace text before the closing tag (or len)."""
        if self.children and isinstance(self.children[-1], Text) and not self.children[-1].raw.strip():
            return len(self.children) - 1
        return len(self.children)

    def append(self, node, blank_line: bool = True):
        """Append an element last, indented like its siblings."""
        self.insert_at(self._tail_ws(), node, blank_line)

    def insert_at(self, index: int, node, blank_line: bool = True):
        """Insert a node before children[index] on its own indented line."""
        ind = "\t" * (self.depth() + 1)
        was_empty = not self.children
        self._selfclose = False
        lead = Text((self.nl if blank_line and not was_empty and index > 0 else "") + self.nl + ind)
        # insert before the trailing whitespace so the closing tag keeps its indent
        self.children[index:index] = [self._adopt(lead), self._adopt(node)]
        if was_empty:
            self.children.append(self._adopt(Text(self.nl + "\t" * self.depth())))

    def insert_after(self, ref, node, blank_line: bool = True):
        i = self.children.index(ref) + 1
        self.insert_at(i, node, blank_line)

    def remove(self, node):
        """Remove a node together with the whitespace before it."""
        i = self.children.index(node)
        if i > 0 and isinstance(self.children[i - 1], Text) and not self.children[i - 1].raw.strip():
            del self.children[i - 1:i + 1]
        else:
            del self.children[i]
        node.parent = None
        if not any(not (isinstance(c, Text) and not c.raw.strip()) for c in self.children):
            self.children = []
            self._selfclose = True
            self._close = None

    def detach(self, node) -> list:
        """Take a node out with its leading whitespace (for moving elsewhere)."""
        i = self.children.index(node)
        j = i - 1 if i > 0 and isinstance(self.children[i - 1], Text) and not self.children[i - 1].raw.strip() else i
        out = self.children[j:i + 1]
        del self.children[j:i + 1]
        return out

    # --- writing ---
    def _open_tag(self) -> str:
        closing = " />" if (self._selfclose and not self.children) else ">"
        if self._open is not None:
            body = self._open
            was_self = body.rstrip().endswith("/>")
            if was_self == (closing == " />"):
                return body
            body = body.rstrip()
            body = body[:-2].rstrip() if was_self else body[:-1].rstrip()
            return body + closing
        if not self.attrs:
            return "<" + self.tag + closing
        parts = [f'{k}="{escape_attr(v)}"' for k, v in self.attrs.items()]
        if self.inline or (self.inline is None and len(parts) <= 2 and sum(map(len, parts)) < 70):
            return "<" + self.tag + " " + " ".join(parts) + closing
        ind = self.nl + "\t" * (self.depth() + 1)
        return "<" + self.tag + ind + ind.join(parts) + closing

    def dump(self) -> str:
        if self.tag == "#root":
            return "".join(c.dump() for c in self.children)
        if self._selfclose and not self.children:
            return self._open_tag()
        return self._open_tag() + "".join(c.dump() for c in self.children) + (self._close or f"</{self.tag}>")


def clone(el: Element) -> Element:
    """Deep copy of an element with the same formatting."""
    c = parse(el.dump()).elements()[0]
    c.parent = None
    return c


def parse(src: str) -> Element:
    """Parse a document; returns the '#root' node whose dump() equals src."""
    nl = "\r\n" if "\r\n" in src else "\n"
    root = Element("#root")
    root.nl = nl
    root._selfclose = False
    cur = root
    i, n = 0, len(src)
    while i < n:
        lt = src.find("<", i)
        if lt < 0:
            cur.children.append(cur._adopt(Text(src[i:])))
            break
        if lt > i:
            cur.children.append(cur._adopt(Text(src[i:lt])))
        if src.startswith("<!--", lt):
            e = src.find("-->", lt + 4)
            if e < 0:
                raise XmlError("незакрытый комментарий")
            cur.children.append(cur._adopt(Comment(raw=src[lt:e + 3])))
            i = e + 3
        elif src.startswith("<![CDATA[", lt):
            e = src.find("]]>", lt)
            if e < 0:
                raise XmlError("незакрытый CDATA")
            cur.children.append(cur._adopt(Raw(src[lt:e + 3])))
            i = e + 3
        elif src.startswith("<?", lt) or src.startswith("<!", lt):
            e = src.find(">", lt)
            if e < 0:
                raise XmlError("незакрытое объявление")
            cur.children.append(cur._adopt(Raw(src[lt:e + 1])))
            i = e + 1
        elif src.startswith("</", lt):
            e = src.find(">", lt)
            name = src[lt + 2:e].strip()
            if cur.tag == "#root" or name != cur.tag:
                line = src.count("\n", 0, lt) + 1
                raise XmlError(f"строка {line}: закрыт </{name}>, а открыт <{cur.tag}>")
            cur._close = src[lt:e + 1]
            cur = cur.parent
            i = e + 1
        else:
            m = _TAG_RE.match(src, lt)
            if not m:                       # a lone '<' in text stays text
                cur.children.append(cur._adopt(Text("<")))
                i = lt + 1
                continue
            # tag end: '>' outside quotes
            j, q = m.end(), ""
            while j < n:
                ch = src[j]
                if q:
                    if ch == q:
                        q = ""
                elif ch in "\"'":
                    q = ch
                elif ch == ">":
                    break
                j += 1
            if j >= n:
                raise XmlError(f"строка {src.count(chr(10), 0, lt) + 1}: незакрытый тег <{m.group(1)}")
            raw = src[lt:j + 1]
            el = Element(m.group(1))
            el.nl = nl
            for am in _ATTR_RE.finditer(raw, len(m.group(0))):
                el.attrs[am.group(1)] = unescape(am.group(2)[1:-1])
            el._open = raw
            el._selfclose = raw.rstrip().endswith("/>")
            cur.children.append(cur._adopt(el))
            if not el._selfclose:
                cur = el
            i = j + 1
    if cur is not root:
        raise XmlError(f"не закрыт <{cur.tag}>")
    return root

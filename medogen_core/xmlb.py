"""Минимальный построитель XML с явными префиксами пространств имён.

ElementTree переименовывает префиксы, а СЭД нередко ждут привычные
xdms:/doc:/exc:, поэтому XML собирается вручную.
"""
from xml.sax.saxutils import escape, quoteattr


class E:
    """Элемент: E('xdms:title', 'Текст', {'xdms:id': '1'}, children...)."""

    def __init__(self, tag, text=None, attrs=None, *children):
        self.tag = tag
        self.text = None if text is None else str(text)
        self.attrs = {k: v for k, v in (attrs or {}).items() if v is not None}
        self.children = [c for c in children if c is not None]

    def add(self, *children):
        for c in children:
            if c is None:
                continue
            if isinstance(c, (list, tuple)):
                self.add(*c)
            else:
                self.children.append(c)
        return self

    def render(self, indent=0, step="  "):
        pad = step * indent
        attrs = "".join(f" {k}={quoteattr(str(v))}" for k, v in self.attrs.items())
        if not self.children and self.text is None:
            return f"{pad}<{self.tag}{attrs}/>"
        if not self.children:
            return f"{pad}<{self.tag}{attrs}>{escape(self.text)}</{self.tag}>"
        inner = "\n".join(c.render(indent + 1, step) for c in self.children)
        return f"{pad}<{self.tag}{attrs}>\n{inner}\n{pad}</{self.tag}>"


class Raw:
    """Готовый фрагмент XML, вставляется как есть."""

    def __init__(self, xml):
        self.xml = xml

    def render(self, indent=0, step="  "):
        pad = step * indent
        return "\n".join(pad + line for line in self.xml.strip("\n").split("\n"))


def document(root, encoding="UTF-8"):
    return f'<?xml version="1.0" encoding="{encoding}"?>\n{root.render()}\n'

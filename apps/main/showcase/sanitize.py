"""
Очистка пользовательской разметки витрины (ТЗ-BE-2026-05, п. 3.10, 6.9, 6.10).

- sanitize_body(): ограниченная разметка страниц — абзацы, жирный, списки, ссылки.
  Всё остальное экранируется и показывается как текст (<script> → &lt;script&gt;).
- sanitize_svg(): SVG-логотип — только безопасные элементы/атрибуты, без скриптов,
  обработчиков событий, внешних ссылок и DOCTYPE/ENTITY.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import List, Optional

from apps.main.showcase.design_schema import is_safe_url

ALLOWED_BODY_TAGS = {"p", "br", "strong", "b", "ul", "ol", "li", "a"}
_VOID = {"br"}


class _BodySanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.stack: List[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_BODY_TAGS:
            self.out.append(html.escape(self.get_starttag_text() or f"<{tag}>"))
            return
        if tag == "br":
            self.out.append("<br>")
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not is_safe_url(href):
                # Ссылка с недопустимой схемой (javascript: и т.п.) — оставляем только текст.
                self.stack.append("a!")
                return
            self.out.append(f'<a href="{html.escape(href.strip(), quote=True)}" rel="nofollow noopener" target="_blank">')
            self.stack.append("a")
            return
        self.out.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br":
            self.out.append("<br>")
        else:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag not in ALLOWED_BODY_TAGS:
            self.out.append(html.escape(f"</{tag}>"))
            return
        if tag in _VOID:
            return
        for marker in (tag, "a!" if tag == "a" else None):
            if marker and marker in self.stack:
                while self.stack:
                    top = self.stack.pop()
                    if top != "a!":
                        self.out.append(f"</{top}>")
                    if top == marker:
                        break
                return

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    def result(self) -> str:
        while self.stack:
            top = self.stack.pop()
            if top != "a!":
                self.out.append(f"</{top}>")
        return "".join(self.out)


_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z]")


def sanitize_body(text: Optional[str]) -> str:
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if not _TAG_RE.search(text):
        # Обычный текст: пустая строка — новый абзац, перенос — <br>.
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        return "".join("<p>" + html.escape(p, quote=False).replace("\n", "<br>") + "</p>" for p in paras)
    parser = _BodySanitizer()
    parser.feed(text)
    parser.close()
    return parser.result()


# ----------------------------------------------------------------------
# SVG
# ----------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ALLOWED_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "defs", "lineargradient", "radialgradient", "stop", "title", "desc", "clippath", "mask", "use", "symbol",
}
ALLOWED_SVG_ATTRS = {
    "id", "class", "viewbox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "d", "points", "fill", "fill-opacity", "fill-rule", "clip-rule", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "stroke-opacity", "opacity",
    "transform", "offset", "stop-color", "stop-opacity", "gradientunits", "gradienttransform", "spreadmethod",
    "fx", "fy", "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "letter-spacing",
    "clip-path", "mask", "preserveaspectratio", "version", "href", "style", "maskunits", "clippathunits",
    "patternunits", "xml:space",
}
_BAD_STYLE_RE = re.compile(r"(expression|javascript:|@import|url\s*\(\s*['\"]?\s*(?!#))", re.I)
_URL_REF_RE = re.compile(r"url\s*\(\s*['\"]?\s*(?!#)", re.I)


class SvgError(ValueError):
    pass


def _local(name: str) -> str:
    if name.startswith("{"):
        ns, _, local = name[1:].partition("}")
        if ns == XLINK_NS:
            return "xlink:" + local
        if ns == "http://www.w3.org/XML/1998/namespace":
            return "xml:" + local
        return local
    return name


def sanitize_svg(data: bytes, max_bytes: int = 1024 * 1024) -> bytes:
    if len(data) > max_bytes:
        raise SvgError("SVG слишком большой.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SvgError("SVG должен быть в кодировке UTF-8.")
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise SvgError("SVG с DOCTYPE/ENTITY не принимается.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise SvgError("Некорректный SVG.")
    if _local(root.tag).lower() != "svg":
        raise SvgError("Файл не является SVG.")

    def clean(el):
        for child in list(el):
            if not isinstance(child.tag, str) or _local(child.tag).lower() not in ALLOWED_SVG_TAGS:
                el.remove(child)
                continue
            clean(child)
        for attr in list(el.attrib):
            name = _local(attr)
            lname = name.lower()
            val = el.attrib[attr]
            if lname.startswith("on"):
                del el.attrib[attr]
                continue
            if lname in ("href", "xlink:href"):
                if not val.strip().startswith("#"):
                    del el.attrib[attr]
                continue
            if lname not in ALLOWED_SVG_ATTRS:
                del el.attrib[attr]
                continue
            if lname == "style" and _BAD_STYLE_RE.search(val):
                del el.attrib[attr]
                continue
            if _URL_REF_RE.search(val) or "javascript:" in val.lower():
                del el.attrib[attr]

    clean(root)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8")


def svg_size(data: bytes):
    """(ширина, высота) из width/height или viewBox; (0, 0) если не определить."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return 0, 0

    def num(v):
        m = re.match(r"^\s*([0-9.]+)", v or "")
        return int(float(m.group(1))) if m else 0

    w, h = num(root.attrib.get("width")), num(root.attrib.get("height"))
    if (not w or not h) and root.attrib.get("viewBox"):
        parts = re.split(r"[\s,]+", root.attrib["viewBox"].strip())
        if len(parts) == 4:
            try:
                w, h = int(float(parts[2])), int(float(parts[3]))
            except ValueError:
                pass
    return w, h

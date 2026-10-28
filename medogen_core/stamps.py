"""Картинки штампов (PNG) с реальными данными, по образцу эталонных пакетов.

Штамп ЭП (эталон 3.0 «ДЭВ без digital»): рамка, «ДОКУМЕНТ ПОДПИСАН ЭЛЕКТРОННОЙ ПОДПИСЬЮ»,
плашка «СВЕДЕНИЯ О СЕРТИФИКАТЕ ЭП», сертификат, владелец, срок действия.
Штамп регистрации (эталон 2.7.1 «Входящее письмо»): «ДД.ММ.ГГГГ № номер», «На № … от …».
Только стандартная библиотека: глифы шрифта лежат в font_data.py."""
import base64
import re
import struct
import zlib

from .font_data import FONTS

BODY, HEAD = 30, 40
_cache = {}


def _font(size):
    if size not in _cache:
        height, index, widths, *blob = FONTS[size]
        data = zlib.decompress(base64.b64decode("".join(blob)))
        glyphs, pos = {}, 0
        for ch, w in zip(index, widths):
            glyphs[ch] = (w, data[pos:pos + w * height])
            pos += w * height
        _cache[size] = (height, glyphs)
    return _cache[size]


def has_glyph(ch):
    return ch in _font(BODY)[1]


def _ru_date(value):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else str(value or "—")


def signature_lines(info, signer_name):
    """Строки сведений о сертификате. Для встроенного тестового сертификата и нечитаемой
    подписи владельцем пишется подписант из сценария."""
    if not info:
        return ["Сертификат: —", f"Владелец: {signer_name}", "Действителен с — до —"]
    who = signer_name if info.get("test") or not info.get("owner") else info["owner"]
    return [f"Сертификат: {info['serial']}", f"Владелец: {who}",
            f"Действителен с {info['not_before']} до {info['not_after']}"]


def registration_lines(reg_date, reg_number, link=None):
    lines = [f"{_ru_date(reg_date)} № {reg_number}"]
    if link:
        lines.append(f"На № {link[0]} от {_ru_date(link[1])}")
    return lines


class _Canvas:
    """Оттенок серого + альфа; текст рисуется чернилами (ink) поверх."""

    def __init__(self, w, h, gray=0, alpha=0):
        self.w, self.h = w, h
        self.g = bytearray([gray]) * (w * h)
        self.a = bytearray([alpha]) * (w * h)

    def fill(self, x0, y0, x1, y1, gray, alpha=255):
        for y in range(max(0, y0), min(self.h, y1)):
            row = y * self.w
            for x in range(max(0, x0), min(self.w, x1)):
                self.g[row + x], self.a[row + x] = gray, alpha

    def text(self, x, y, s, size, white=False):
        height, glyphs = _font(size)
        for ch in s:
            w, bmp = glyphs.get(ch) or glyphs["?"]
            for gy in range(height):
                py = y + gy
                if not 0 <= py < self.h:
                    continue
                for gx in range(w):
                    ink = bmp[gy * w + gx]
                    px = x + gx
                    if not ink or not 0 <= px < self.w:
                        continue
                    i = py * self.w + px
                    if white:
                        self.g[i] = max(self.g[i], ink)
                    else:
                        self.g[i] = self.g[i] * (255 - ink) // 255
                        self.a[i] = max(self.a[i], ink)
            x += w

    def png(self):
        w = self.w
        raw = bytearray()
        for y in range(self.h):
            row = bytearray(2 * w)
            row[0::2], row[1::2] = self.g[y * w:(y + 1) * w], self.a[y * w:(y + 1) * w]
            raw += b"\x00" + row

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 4, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def text_width(s, size):
    glyphs = _font(size)[1]
    return sum((glyphs.get(c) or glyphs["?"])[0] for c in s)


def _fit(w, h, w_mm, h_mm):
    """Расширяет (w, h) до пропорций штампа w_mm × h_mm из паспорта."""
    aspect = w_mm / h_mm if w_mm and h_mm else w / h
    if w / h < aspect:
        w = round(h * aspect)
    else:
        h = round(w / aspect)
    return w, h


def _rounded_frame(c, border, radius):
    """Белая плашка с чёрной рамкой и скруглёнными углами; снаружи прозрачно."""
    w, h = c.w, c.h
    inner = radius - border
    for y in range(h):
        dy = max(radius - y, y - (h - 1 - radius), 0)
        # отступ внешнего и внутреннего контура от края в этой строке
        out = radius - int((radius * radius - dy * dy) ** 0.5) if dy else 0
        if dy > inner:
            ins = w // 2
        elif dy:
            ins = radius - int((inner * inner - dy * dy) ** 0.5)
        else:
            ins = border
        if y < border or y >= h - border:
            ins = w // 2
        row = y * w
        c.a[row + out:row + w - out] = b"\xff" * (w - 2 * out)
        c.g[row + out:row + w - out] = b"\x00" * (w - 2 * out)
        if w - 2 * ins > 0:
            c.g[row + ins:row + w - ins] = b"\xff" * (w - 2 * ins)


def signature_stamp(info, signer_name, w_mm, h_mm):
    head = ["ДОКУМЕНТ ПОДПИСАН", "ЭЛЕКТРОННОЙ ПОДПИСЬЮ"]
    band = "СВЕДЕНИЯ О СЕРТИФИКАТЕ ЭП"
    body = signature_lines(info, signer_name)
    hh, bh = _font(HEAD)[0], _font(BODY)[0]
    pad, border, gap, band_h = 34, 6, 10, bh + 14
    content_w = max([text_width(s, HEAD) for s in head] + [text_width(s, BODY) + 40 for s in [band] + body])
    content_h = 2 * hh + 2 * gap + band_h + gap + 3 * (bh + gap)
    w, h = _fit(content_w + 2 * pad, content_h + 2 * pad, w_mm, h_mm)
    c = _Canvas(w, h)
    _rounded_frame(c, border, 30)
    y = (h - content_h) // 2
    for s in head:
        c.text((w - text_width(s, HEAD)) // 2, y, s, HEAD)
        y += hh
    y += gap
    left, right = pad + 10, w - pad - 10
    c.fill(left, y, right, y + band_h, 0)
    c.text((w - text_width(band, BODY)) // 2, y + 7, band, BODY, white=True)
    y += band_h + 2 * gap
    for s in body:
        c.text(left, y, s, BODY)
        y += bh + gap
    return c.png()


def registration_stamp(reg_date, reg_number, link, w_mm, h_mm):
    lines = registration_lines(reg_date, reg_number, link)
    hh, pad = _font(HEAD)[0], 8
    content_w = max(text_width(s, HEAD) for s in lines)
    content_h = hh * len(lines)
    w, h = _fit(content_w + 2 * pad, content_h + 2 * pad, w_mm, h_mm)
    c = _Canvas(w, h)
    y = (h - content_h) // 2
    for s in lines:
        c.text(pad, y, s, HEAD)
        y += hh
    return c.png()


def for_signer(sig, signer):
    """Штамп ЭП подписанта модели (model.Signer) по его подписи sig."""
    from .certinfo import from_signature
    return signature_stamp(from_signature(sig), signer.name, signer.stamp.w, signer.stamp.h)


def for_document(doc):
    """Штамп регистрации документа модели (model.DocSpec); связь — первая из links."""
    link = (doc.links[0].number, doc.links[0].date) if doc.links else None
    return registration_stamp(doc.reg_date, doc.reg_number, link, doc.reg_stamp.w, doc.reg_stamp.h)

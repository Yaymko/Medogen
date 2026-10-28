"""Генератор medogen_core/font_data.py: растровые глифы шрифта DejaVu Sans для штампов.

Нужен один раз (или при смене набора символов); сам генератор пакетов работает без Pillow.
Запуск: python tools/make_font.py [путь к DejaVuSans.ttf]
Шрифт DejaVu Sans — свободная лицензия (Bitstream Vera / public domain), встраивание разрешено."""
import base64
import os
import sys
import zlib

from PIL import Image, ImageDraw, ImageFont

SIZES = (30, 40)
CHARS = ("".join(chr(c) for c in range(32, 127))
         + "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
         + "№—–«»…")


def render(font_path, size):
    font = ImageFont.truetype(font_path, size)
    ascent, descent = font.getmetrics()
    height = ascent + descent
    glyphs = {}
    for ch in CHARS:
        adv = max(1, round(font.getlength(ch)))
        img = Image.new("L", (adv, height), 0)
        ImageDraw.Draw(img).text((0, 0), ch, fill=255, font=font)
        glyphs[ch] = (adv, img.tobytes())
    return height, glyphs


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    out = ['"""Растровые глифы DejaVu Sans для штампов (сгенерировано tools/make_font.py, не править)."""',
           "", "FONTS = {"]
    for size in SIZES:
        height, glyphs = render(path, size)
        index = "".join(glyphs)
        widths = [glyphs[c][0] for c in index]
        blob = base64.b64encode(zlib.compress(b"".join(glyphs[c][1] for c in index), 9)).decode()
        lines = [blob[i:i + 100] for i in range(0, len(blob), 100)]
        out.append(f"    {size}: ({height}, {index!r}, {widths!r},")
        out.append("        " + "\n        ".join(repr(x) for x in lines) + "),")
    out.append("}")
    dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "medogen_core", "font_data.py")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(dst, os.path.getsize(dst))


if __name__ == "__main__":
    main()

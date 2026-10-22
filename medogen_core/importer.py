"""Импорт справочника организаций из сохранённой страницы ГАС ГосЭДО
(addresseesActual.html) или из CSV в формат orgs/*.json.

Страница ГАС (2026) хранит участников в упакованном виде: JSON-заголовок
<script id="gosedo-datapack">, словарь строк в <textarea class="gosedo-dictionary-chunk">
(каждая строка — общий префикс с предыдущей + хвост) и записи в
<textarea class="gosedo-row-chunk"> (значения «длина:текст» или «@номер:» — ссылка
на словарь). Разбор повторяет скрипт страницы. Для старых/других страниц и CSV
остаётся нестрогий разбор таблицы: в строке ищется GUID, адрес МЭДО и название."""
import csv
import html
import io
import json
import re

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ADDR = re.compile(r"^[A-Za-z0-9_]+~[A-Za-z0-9_]+$|^[A-Z0-9]+(?:_[A-Z0-9]+)+$")


def _textareas(text, cls):
    out = []
    for m in re.finditer(r'<textarea[^>]*class="%s"[^>]*>(.*?)</textarea>' % cls, text, re.S):
        s = m.group(1)
        if s.startswith("\n"):  # HTML-парсер отбрасывает перевод строки сразу после <textarea>
            s = s[1:]
        out.append(html.unescape(s))
    return out


class _Packed:
    """Чтение последовательности «длина:значение» / «@ключ:» (длины — в символах)."""

    def __init__(self, s, dic=None):
        self.s, self.i, self.dic = s, 0, dic

    def more(self):
        return self.i < len(self.s)

    def key(self):
        j = self.s.index(":", self.i)
        k, self.i = self.s[self.i:j], j + 1
        return k

    def value(self):
        k = self.key()
        if k.startswith("@"):
            return self.dic[int(k[1:])]
        n = int(k or 0)
        v = self.s[self.i:self.i + n]
        if len(v) != n:
            raise ValueError("оборванное значение в упакованных данных ГАС")
        self.i += n
        return v


def parse_gosedo(text):
    """Участники ГАС из упакованной страницы: [{uid, iedmsId, title, organization, isActive, ...}]
    или None, если страница другого формата."""
    m = re.search(r'<script[^>]*id="gosedo-datapack"[^>]*>(.*?)</script>', text, re.S)
    if not m:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # как HTML-парсер браузера
    pack = json.loads(html.unescape(m.group(1)) if m.group(1).lstrip().startswith("&") else m.group(1))
    if (pack.get("dictionary") or {}).get("t") not in (None, "prefix-dictionary-chunks"):
        raise ValueError(f"неизвестный формат словаря ГАС: {pack['dictionary'].get('t')}")
    dic, n = {}, 1
    for chunk in _textareas(text, "gosedo-dictionary-chunk"):
        r, prev = _Packed(chunk), ""
        while r.more():
            g = int(r.key())
            prev = prev[:g] + r.value()
            dic[n] = prev
            n += 1
    part = pack.get("participants") or {}
    fields = part.get("fields") or []
    if (part.get("rows") or {}).get("t") != "packed-row-chunks" or not fields:
        raise ValueError("неизвестный формат записей ГАС")
    rows = []
    for chunk in _textareas(text, "gosedo-row-chunk"):
        r, vals = _Packed(chunk, dic), []
        while r.more():
            vals.append(r.value())
        rows += [dict(zip(fields, vals[i:i + len(fields)])) for i in range(0, len(vals), len(fields))]
    if part.get("count") and part["count"] != len(rows):
        raise ValueError(f"ГАС: разобрано {len(rows)} записей вместо {part['count']}")
    return rows


def gosedo_entries(rows):
    """{guid: {guid, name, address, org, active}} — для справочника ГАС и стендов."""
    out = {}
    for p in rows:
        guid = (p.get("uid") or "").lower()
        if not GUID.fullmatch(guid):
            continue
        out[guid] = {"guid": guid, "name": p.get("title") or p.get("organization") or guid,
                     "address": p.get("iedmsId") or None, "org": p.get("organization") or "",
                     "active": str(p.get("isActive")).lower() != "false"}
    return out


def _cells_from_html(text):
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)
        yield [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]


def _cells_from_csv(text):
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    yield from csv.reader(io.StringIO(text), dialect)


def parse(text, filename=""):
    packed = parse_gosedo(text) if not filename.lower().endswith(".csv") else None
    if packed is not None:
        return {g[:8]: {k: e[k] for k in ("guid", "name", "address")}
                for g, e in gosedo_entries(packed).items() if e["active"]}
    rows = _cells_from_csv(text) if filename.lower().endswith(".csv") else _cells_from_html(text)
    orgs = {}
    for cells in rows:
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        guid = next((GUID.search(c).group(0).lower() for c in cells if GUID.search(c)), None)
        if not guid:
            continue
        addr = None
        for c in cells:
            if ADDR.match(c):
                addr = c if "~" in c else f"{c}~MEDOGU"
                break
        names = [c for c in cells if c and not GUID.search(c) and c != addr and not ADDR.match(c)
                 and not re.fullmatch(r"[\d.\-: ]+", c)]
        name = max(names, key=len) if names else f"Организация {guid[:8]}"
        orgs[guid[:8]] = {"guid": guid, "name": name, "address": addr}
    return orgs


def read_text(src):
    with open(src, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def import_file(src, dst, stand=None):
    orgs = parse(read_text(src), src)
    data = {"stand": stand or "imported", "self": None, "orgs": orgs}
    try:  # повторный импорт в существующий стенд сохраняет папки обмена с СЭД
        with open(dst, encoding="utf-8-sig") as f:
            old = json.load(f)
        if old.get("exchange"):
            data["exchange"] = old["exchange"]
    except (OSError, ValueError):
        pass
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(orgs)

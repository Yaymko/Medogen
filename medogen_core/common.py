"""Общие части генератора: справочники, организации, даты, заглушки файлов,
подписи, конверт и запись пакета на диск."""
import datetime as dt
import json
import os
import re
import shlex
import struct
import subprocess
import tempfile
import uuid
import zipfile
import zlib

# --- Справочники НСИ (базовые значения из эталонов и регламентов) -----------

KINDS = {
    "DK00000000": "Другие виды документов",
    "DK00000073": "Резолюция Председателя Правительства Российской Федерации, "
                  "Заместителя Председателя Правительства Российской Федерации",
}
PLACES = {
    "DP00000077": "г. Москва",
    "DP00000018": "Удмуртская Республика",
}
CLASSES = {
    "DC00000000": "Обычная информация",
    "DC00000001": "Информация ограниченного распространения",
}
LINK_TYPES = {
    "LT00000000": "Связан с",
}
CONTAINER_TYPES = {
    "TC00000002": "Документ в электронном виде",
    "TC00000007": "Цифровой документ ГосЭДО",
}
# Причины отказа в приеме ЭС (квитанция 3.0), приказ 611/96, прил. 3, табл. 2
RECEIPT_REASONS = {
    "101": "Паспорт сообщения не соответствует формату",
    "102": "Паспорт контейнера не соответствует формату",
    "103": "Транспортный контейнер не соответствует формату",
    "201": "Некорректная адресация электронного сообщения",
    "202": "Повторное направление электронного сообщения",
    "203": "Повторное направление транспортного контейнера",
    "301": "Файл текста основного документа не соответствует формату PDF/A-1",
    "302": "Файл структурированных данных основного документа не соответствует формату",
    "303": "Структурированные данные не соответствуют требованиям к организационно-"
           "техническому взаимодействию",
}
# Причины отказа в регистрации, Cat01-004-00005 (Типовой регламент ГосЭДО, табл. 5)
REFUSAL_REASONS = {
    "101": "Не указан вид документа",
    "102": "Некорректно указан вид документа",
    "103": "Не указан корреспондент",
    "104": "Не указаны номер и дата исходящего документа",
    "105": "Не указаны номер и дата основания для ответа",
    "106": "Ошибка адресации",
    "107": "Автор документа не соответствует требованиям",
    "108": "Адресат документа не соответствует требованиям",
    "109": "Несоответствие требованиям к формату электронной подписи",
    "110": "Нет приложения или неполная комплектация",
    "111": "Несоответствие вложенных файлов",
    "112": "Не указаны ссылки на внешние документы",
    "113": "Отсутствуют обязательные структурированные данные",
    "201": "Документ ДСП",
    "202": "Документ зарегистрирован ранее, новый текст «взамен разосланного» загружен",
    "203": "Документ не соответствует регламенту",
    "204": "Документ не соответствует реквизитам паспорта",
    "205": "Документ неверно адресован",
    "206": "Документ подписан не уполномоченным лицом",
    "207": "Документ получен ранее в электронном виде",
    "208": "Документ получен ранее на бумажном носителе",
    "209": "Нет подписи или ЭП",
    "210": "Текст отсутствует",
    "211": "Текст неразборчив",
    "212": "Текст отсканирован с дефектами",
    "213": "Текст содержит дублирование страниц",
}


class MedoError(Exception):
    pass


WARNINGS = []


def warn(msg):
    if msg in WARNINGS:
        return
    WARNINGS.append(msg)
    print(f"  ! {msg}")


def ref(value, catalog, what):
    """'DK00000000' или {'id': ..., 'name': ...} -> (id, name)."""
    if isinstance(value, dict):
        rid, name = value.get("id"), value.get("name")
        if not name:
            name = catalog.get(rid)
        if not rid or not name:
            raise MedoError(f"{what}: нужно указать id и name, получено {value}")
        return rid, name
    if value in catalog:
        return value, catalog[value]
    raise MedoError(f"{what}: код {value!r} не найден в справочнике генератора, "
                    f"укажите объектом {{\"id\": \"...\", \"name\": \"...\"}}")


# --- Идентификаторы и время -------------------------------------------------

def new_uuid():
    return str(uuid.uuid4())


GUID_RE = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")


def check_guid(value, what):
    v = str(value).strip().lower()
    if not GUID_RE.match(v):
        raise MedoError(f"{what}: {value!r} не похож на GUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)")
    return v


class Clock:
    def __init__(self, tz="+03:00"):
        m = re.match(r"^([+-])(\d{2}):(\d{2})$", tz)
        if not m:
            raise MedoError(f"timezone: ожидается вид +03:00, получено {tz!r}")
        sign = 1 if m.group(1) == "+" else -1
        self.tz = dt.timezone(sign * dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))

    def now(self):
        return dt.datetime.now(self.tz).replace(microsecond=0)

    def stamp(self, value=None):
        """Дата-время с зоной: 2026-06-07T12:22:38+03:00."""
        if value:
            return str(value)
        return self.now().isoformat()

    def date(self, value=None):
        """None/'today'/'today+N'/'today-N'/'YYYY-MM-DD' -> 'YYYY-MM-DD'."""
        if value is None or value == "":
            value = "today"
        s = str(value)
        m = re.match(r"^today(?:([+-])(\d+))?$", s)
        if m:
            d = self.now().date()
            if m.group(1):
                delta = dt.timedelta(days=int(m.group(2)))
                d = d + delta if m.group(1) == "+" else d - delta
            return d.isoformat()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            raise MedoError(f"дата {s!r}: ожидается YYYY-MM-DD или today, today+N")
        return s


# --- Организации -------------------------------------------------------------

class Org:
    def __init__(self, guid, name, address=None, key=None):
        self.guid = guid
        self.name = name
        self.address = address
        self.key = key

    def __repr__(self):
        return f"Org({self.name}, {self.guid})"


class Directory:
    """Справочник организаций одного стенда (orgs/*.json)."""

    def __init__(self, data=None):
        self.orgs = {}
        self.gas = {}  # {guid: {guid, name, address}} — справочник ГАС, если загружен
        if data:
            self.load(data)

    def load(self, data):
        for key, o in (data.get("orgs") or {}).items():
            self.orgs[key] = self._make(o, key)

    @staticmethod
    def _make(o, key=None):
        if "guid" not in o or "name" not in o:
            raise MedoError(f"организация {key or o}: нужны поля guid и name")
        return Org(check_guid(o["guid"], f"организация {key}"), o["name"], o.get("address"), key)

    def get(self, value, what="организация"):
        if isinstance(value, Org):
            return value
        if isinstance(value, dict):
            return self._make(value)
        if value in self.orgs:
            return self.orgs[value]
        v = str(value).lower()
        for o in self.orgs.values():
            if o.guid == v:
                return o
        found = self.from_gas(v)
        if found:
            return found
        if GUID_RE.match(v):
            warn(f"{what}: GUID {v} нет в справочнике, название и адрес МЭДО будут условными")
            return Org(v, f"Организация {v[:8]}", None)
        known = ", ".join(sorted(self.orgs)) or "справочник пуст"
        raise MedoError(f"{what}: {value!r} не найдена в справочнике ({known})")

    def from_gas(self, guid):
        g = self.gas.get(str(guid).lower())
        if not g:
            return None
        if g.get("active") is False:
            warn(f"{g['name']} ({g['guid']}): в ГАС отмечен как неактивный участник")
        return Org(g["guid"], g["name"], g.get("address"), "gas")

    def many(self, values, what):
        if values is None:
            return []
        if not isinstance(values, list):
            values = [values]
        return [self.get(v, what) for v in values]


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


# --- Заглушки файлов --------------------------------------------------------

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
                "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e",
                "yu", "ya"]))


def translit(text):
    out = []
    for ch in str(text):
        low = ch.lower()
        if low in _TR:
            t = _TR[low]
            out.append(t.capitalize() if ch != low else t)
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def stub_pdf(lines, pages=1):
    """Простой PDF (A4) с текстом латиницей на каждой странице."""
    pages = max(1, int(pages))
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * pages
    kids = []
    for p in range(pages):
        text = [translit(x).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                for x in lines] + [f"Page {p + 1} of {pages}"]
        ops = ["BT", "/F1 12 Tf", "60 780 Td", "16 TL"]
        for t in text:
            ops.append(f"({t[:90]}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] "
                         "/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                         % (pages_id, font, content)).encode()))
    add(("<< /Type /Pages /Kids [%s] /Count %d >>"
         % (" ".join(f"{k} 0 R" for k in kids), pages)).encode())
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, catalog, xref)
    return bytes(out)


def pdf_page_count(data):
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", data))
    return max(1, n)


def stub_png(w=300, h=120, color=(30, 60, 160)):
    """PNG-штамп: белый прямоугольник с цветной рамкой."""
    border = 6
    rows = []
    for y in range(h):
        row = bytearray(b"\x00")
        for x in range(w):
            edge = x < border or y < border or x >= w - border or y >= h - border
            inner = border * 2 <= x < w - border * 2 and y in (h // 2 - 1, h // 2)
            row += bytes(color) if edge or inner else b"\xff\xff\xff"
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def read_file(path, base_dir):
    p = path if os.path.isabs(path) else os.path.join(base_dir, path)
    if not os.path.isfile(p):
        raise MedoError(f"файл не найден: {p}")
    with open(p, "rb") as f:
        return f.read()


# --- Подписи ----------------------------------------------------------------

class Signer:
    """Отсоединённая подпись. По умолчанию — тестовая подпись CMS (cms.py, RSA,
    самоподписанный сертификат); если в настройках задан sign_command, вызывается
    внешняя утилита (например, cryptcp КриптоПро). В команде подставляются {in} и {out}."""

    def __init__(self, command=None):
        self.command = command

    def sign(self, data, label):
        if not self.command:
            from . import cms
            return cms.sign(data)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "data.bin")
            dst = os.path.join(tmp, "data.bin.sig")
            with open(src, "wb") as f:
                f.write(data)
            cmd = self.command.replace("{in}", src).replace("{out}", dst)
            res = subprocess.run(cmd if os.name == "nt" else shlex.split(cmd),
                                 shell=(os.name == "nt"), capture_output=True)
            if res.returncode != 0 or not os.path.isfile(dst):
                raise MedoError(f"sign_command завершилась с ошибкой ({res.returncode}): "
                                f"{res.stderr.decode(errors='replace')[:500]}")
            with open(dst, "rb") as f:
                return f.read()


# --- Пакет (ЭС) -------------------------------------------------------------

def write_envelope(path, subject, addresses, files):
    lines = ["[ПИСЬМО КП ПС СЗИ]", f"ТЕМА={subject}", "[АДРЕСАТЫ]"]
    lines += [f"{i}={a}" for i, a in enumerate(addresses)]
    lines += ["[ФАЙЛЫ]"]
    lines += [f"{i}={f}" for i, f in enumerate(files)]
    with open(path, "wb") as f:
        f.write(("\r\n".join(lines) + "\r\n").encode("cp1251"))


def medo_addresses(orgs):
    out = []
    for o in orgs:
        if o.address:
            out.append(o.address)
        else:
            warn(f"у организации «{o.name}» нет адреса МЭДО (address), в envelope.ini "
                 f"записан GUID — поправьте справочник")
            out.append(f"{o.guid}~MEDOGU")
    return out


def make_zip(files):
    """files: list[(name, bytes)] -> bytes zip."""
    import io
    buf = io.BytesIO()
    stamp = dt.datetime.now().timetuple()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


def safe_dir_name(text):
    s = re.sub(r"[^\w\-.]+", "_", str(text), flags=re.UNICODE).strip("_")
    return s[:80] or "package"

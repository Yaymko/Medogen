"""Проверка пакета: XSD паспортов (если установлен lxml), обязательные реквизиты (required.py,
работает и без lxml), состав конверта и ТК, сверка с ГАС (предупреждения)."""
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET

from . import required

XSD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xsd")
SCHEMAS = {
    "urn:IEDMS:MESSAGE": ("2.7.1", "message.xsd"),
    "urn:IEDMS:CONTAINER": ("2.7.1", "container.xsd"),
    "urn:IEDMS:MESSAGE:3.0": ("3.0", "message.xsd"),
    "urn:IEDMS:CONTAINER:3.0": ("3.0", "container.xsd"),
}

try:
    from lxml import etree as LX
except ImportError:  # проверка по XSD недоступна, остаются структурные проверки
    LX = None

_cache = {}


def _schema(ns):
    if ns not in _cache:
        ver, name = SCHEMAS[ns]
        _cache[ns] = LX.XMLSchema(LX.parse(os.path.join(XSD_DIR, ver, name)))
    return _cache[ns]


def check_xml(data, label, errors):
    try:
        root = ET.fromstring(data[3:] if data.startswith(b"\xef\xbb\xbf") else data)
    except ET.ParseError as e:
        errors.append(f"{label}: XML не разбирается: {e}")
        return None
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if ns in SCHEMAS and LX is not None:
        doc = LX.fromstring(data)
        sch = _schema(ns)
        if not sch.validate(doc):
            for err in sch.error_log:
                errors.append(f"{label}: XSD строка {err.line}: {err.message}")
    return root


def read_envelope(path):
    with open(path, "rb") as f:
        text = f.read().decode("cp1251")
    sections, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = sections.setdefault(m.group(1), [])
        elif "=" in line and cur is not None:
            cur.append(line.split("=", 1)[1].strip())
    return sections


def validate_folder(folder, warnings=None, gas=None):
    """Ошибки пакета. Если передан список warnings, в него добавляется сверка с ГАС
    (gas — {guid: запись}; по умолчанию загружается gas/gas.json)."""
    errors = []
    env = os.path.join(folder, "envelope.ini")
    if not os.path.isfile(env):
        return [f"нет envelope.ini в {folder}"]
    sections = read_envelope(env)
    files = sections.get("ФАЙЛЫ", [])
    if not sections.get("АДРЕСАТЫ"):
        errors.append("envelope.ini: пустой раздел [АДРЕСАТЫ]")
    for f in files:
        if not os.path.isfile(os.path.join(folder, f)):
            errors.append(f"envelope.ini: файл {f} отсутствует")
    extra = sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))
                   and n.lower() != "envelope.ini" and n not in files)
    if extra:  # проверочный бот МЭДО требует, чтобы [ФАЙЛЫ] перечислял все файлы пакета
        errors.append(f"envelope.ini: в [ФАЙЛЫ] не перечислены файлы пакета: {', '.join(extra)}")
    msg = None
    for f in sorted(files, key=lambda n: not n.endswith(".xml")):  # сначала паспорт ЭС
        p = os.path.join(folder, f)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            data = fh.read()
        if f.endswith(".xml"):
            root = check_xml(data, f, errors)
            if root is not None and root.tag == "{urn:IEDMS:MESSAGE:3.0}message":
                if len(root.findall("{urn:IEDMS:MESSAGE:3.0}receivers")) != 1:
                    errors.append(f"{f}: должен быть ровно один элемент receivers")
                msg = required.message_30(root, f, errors)
            elif root is not None and root.tag == "{urn:IEDMS:MESSAGE}communication":
                msg = required.message_271(root, f, errors)
            if root is not None:
                required.uuid_attrs(root, f, errors)
        elif f.endswith(".edc.zip"):
            check_container(data, f, errors, msg)
    if msg and msg.get("container_file") and msg["container_file"] not in files:
        errors.append(f"паспорт ЭС ссылается на ТК {msg['container_file']}, его нет в [ФАЙЛЫ]")
    if warnings is not None:
        if gas is None:
            from . import gas as gasmod
            gas = gasmod.by_guid()
        required.check_gas(msg, sections.get("АДРЕСАТЫ", []), gas, warnings)
    return _unique(errors)


def _unique(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def check_container(data, label, errors, msg=None):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        for n in names:
            if n.endswith(".xml"):
                check_xml(z.read(n), f"{label}/{n}", errors)
        if "passport.xml" not in names:
            if "digital.xml" not in names:
                errors.append(f"{label}: нет ни passport.xml, ни digital.xml")
            else:
                for n in names:
                    if n != "digital.xml" and not re.match(r"^enclosure_[a-z0-9_\-.]{1,60}\.[a-z0-9]+$", n):
                        errors.append(f"{label}: в ТК ГосЭДО допустимы только digital.xml и enclosure_*, найден {n}")
                try:
                    droot = ET.fromstring(z.read("digital.xml"))
                except ET.ParseError:
                    return
                tops = [c.tag.rsplit("}", 1)[-1] for c in droot]
                if "РегистрацияДокумента" in tops and "Signature" not in tops:
                    errors.append(f"{label}/digital.xml: зарегистрированный документ в ТК ГосЭДО без подписи "
                                  "контейнера /Документ/Signature (показатель 10.7 бота МЭДО)")
            return
        root = ET.fromstring(z.read("passport.xml"))
        ns = root.tag[1:].split("}")[0]
        plabel = f"{label}/passport.xml"
        required.uuid_attrs(root, plabel, errors)
        if ns == "urn:IEDMS:CONTAINER:3.0":
            required.container_30(root, plabel, errors, msg)
        elif ns == "urn:IEDMS:CONTAINER":
            required.container_271(root, plabel, errors, msg)
        if ns == "urn:IEDMS:CONTAINER:3.0":
            q = "{urn:IEDMS:CONTAINER:3.0}"
            integ = root.find(f"{q}integrity")
            if integ is not None:
                listed = [e.text for e in integ.findall(f"{q}innerFile")]
                sign = integ.get(f"{q}signFile")
                actual = sorted(n for n in names if n != sign)
                if sorted(listed) != actual:
                    errors.append(f"{label}: integrity не совпадает с составом архива: "
                                  f"в списке {sorted(listed)}, в архиве {actual}")
                if listed and listed[0] != "passport.xml":
                    errors.append(f"{label}: integrity должен начинаться с passport.xml")
                if sign and sign not in names:
                    errors.append(f"{label}: нет файла подписи ТК {sign}")
            referenced = [e.text for e in root.iter() if e.tag in (f"{q}textFile", f"{q}dataFile", f"{q}mainFile",
                                                                   f"{q}signFile")]
            referenced += [v for e in root.iter() for k, v in e.attrib.items()
                           if k in (f"{q}stampFile", f"{q}signFile")]
        else:
            q = "{urn:IEDMS:CONTAINER}"
            referenced = [v for e in root.iter() for k, v in e.attrib.items() if k == f"{q}localName"]
        for r in referenced:
            if r not in names:
                errors.append(f"{label}: паспорт ссылается на {r}, но его нет в архиве")
        for n in names:
            if n.lower().endswith((".p7s", ".sig")) and not looks_like_cms(z.read(n)):
                errors.append(f"{label}/{n}: файл ЭП не похож на CMS/PKCS#7 (DER, Base64 или PEM)")


def looks_like_cms(data):
    """Как в проверочном боте МЭДО: DER-последовательность, PEM или Base64 от неё."""
    import base64
    if data[:1] == b"\x30":
        return True
    text = data.strip()
    if text.startswith(b"-----BEGIN"):
        return True
    try:
        return base64.b64decode(b"".join(text.split()), validate=True)[:1] == b"\x30"
    except Exception:
        return False


def has_xsd():
    return LX is not None

"""Сведения о сертификате из отсоединённой подписи CMS/PKCS#7 (для штампа ЭП).

Разбор DER без сторонних библиотек: серийный номер, владелец, срок действия.
Работает для тестовой подписи (cms.py), подписи КриптоПро (sign_command) и своего .p7s."""
import base64
import datetime as dt

from . import cms

OID_CN, OID_SN, OID_G = "2.5.4.3", "2.5.4.4", "2.5.4.42"


def _read(data, pos):
    """(tag, начало содержимого, конец содержимого) элемента DER по смещению pos."""
    tag = data[pos]
    ln = data[pos + 1]
    pos += 2
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    if pos + ln > len(data):
        raise ValueError("обрезанный DER")
    return tag, pos, pos + ln


def _children(data, start, end):
    out = []
    while start < end:
        tag, s, e = _read(data, start)
        out.append((tag, s, e))
        start = e
    return out


def _oid(body):
    parts = [body[0] // 40, body[0] % 40]
    v = 0
    for b in body[1:]:
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(v)
            v = 0
    return ".".join(map(str, parts))


def _text(tag, body):
    if tag == 0x1E:
        return body.decode("utf-16-be", "replace")
    if tag == 0x14:
        return body.decode("latin-1")
    return body.decode("utf-8", "replace")


def _time(tag, body):
    s = body.decode("ascii").rstrip("Z")
    fmt = "%y%m%d%H%M%S" if tag == 0x17 else "%Y%m%d%H%M%S"
    return dt.datetime.strptime(s[:12 if tag == 0x17 else 14], fmt).strftime("%d.%m.%Y")


def serial_hex(body):
    """Серийный номер побайтно в hex (как в свойствах сертификата), без знакового 00."""
    if len(body) > 1 and body[0] == 0:
        body = body[1:]
    return body.hex().upper()


def build_name(pairs):
    """DER Name из [(oid, значение)] — для тестов."""
    rdns = [cms._set(cms._seq(cms._oid(oid), cms._tlv(0x0C, value.encode()))) for oid, value in pairs]
    return cms._seq(*rdns)


def parse_name(der):
    """[(oid, значение)] из DER Name."""
    _, s, e = _read(der, 0)
    return _rdns(der, s, e)


def _rdns(der, s, e):
    out = []
    for _, rs, re_ in _children(der, s, e):
        for _, as_, ae in _children(der, rs, re_):
            (_, os_, oe), (vt, vs, ve) = _children(der, as_, ae)[:2]
            out.append((_oid(der[os_:oe]), _text(vt, der[vs:ve])))
    return out


def owner(name):
    d = dict(name)
    if d.get(OID_SN):
        return " ".join(x for x in (d[OID_SN], d.get(OID_G)) if x)
    return d.get(OID_CN, "")


def _cert_info(der, s, e):
    tbs = _children(der, *_children(der, s, e)[0][1:])
    if tbs[0][0] == 0xA0:  # [0] version
        tbs = tbs[1:]
    serial = serial_hex(der[tbs[0][1]:tbs[0][2]])
    (t1, s1, e1), (t2, s2, e2) = _children(der, tbs[3][1], tbs[3][2])
    name = _rdns(der, tbs[4][1], tbs[4][2])
    return {"serial": serial, "owner": owner(name),
            "not_before": _time(t1, der[s1:e1]), "not_after": _time(t2, der[s2:e2]),
            "test": serial == format(cms.SERIAL, "X")}


def from_signature(sig):
    """dict(serial, owner, not_before, not_after, test) или None, если разобрать не удалось."""
    try:
        if sig[:1] != b"\x30":  # PEM / base64
            text = sig.decode("ascii").replace("-----BEGIN PKCS7-----", "").replace("-----END PKCS7-----", "")
            text = text.replace("-----BEGIN CMS-----", "").replace("-----END CMS-----", "")
            sig = base64.b64decode("".join(text.split()))
        _, s, e = _read(sig, 0)
        content = _children(sig, s, e)[1]                    # [0] EXPLICIT SignedData
        _, ss, se = _read(sig, content[1])
        parts = _children(sig, ss, se)
        certs = [p for p in parts if p[0] == 0xA0]
        if not certs:
            return None
        cert_list = _children(sig, certs[0][1], certs[0][2])
        infos = [_cert_info(sig, cs, ce) for tag, cs, ce in cert_list if tag == 0x30]
        wanted = None
        signer_infos = [p for p in parts if p[0] == 0x31][-1]
        for _, ps, pe in _children(sig, signer_infos[1], signer_infos[2])[:1]:
            sid = _children(sig, ps, pe)[1]
            if sid[0] == 0x30:  # issuerAndSerialNumber
                ser = _children(sig, sid[1], sid[2])[1]
                wanted = serial_hex(sig[ser[1]:ser[2]])
        for info in infos:
            if info["serial"] == wanted:
                return info
        return infos[0] if infos else None
    except (ValueError, IndexError, UnicodeDecodeError, base64.binascii.Error):
        return None

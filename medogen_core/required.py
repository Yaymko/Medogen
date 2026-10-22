"""Проверка обязательных реквизитов пакета без XSD — по критериям проверочного бота МЭДО
(разделы 1–3 отчёта): заполненность обязательных элементов и атрибутов, формат UUID,
дат и имени ТК, согласованность паспорта ЭС, паспорта ТК и envelope.ini.

Сверка с ГАС (адреса из [АДРЕСАТЫ], GUID и наименования отправителя и получателей)
выдаётся предупреждениями: на тестовых стендах GUID могут отличаться от ГАС."""
import re

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UUID_ANY = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DT_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TK_NAME = re.compile(r"^[a-zA-Z0-9_]{1,247}\.edc\.zip$")

M30, C30 = "{urn:IEDMS:MESSAGE:3.0}", "{urn:IEDMS:CONTAINER:3.0}"
M27, C27 = "{urn:IEDMS:MESSAGE}", "{urn:IEDMS:CONTAINER}"
# 2.7 и 2.6 (и ниже): одна схема сообщения, паспорт ТК только в 2.7
M_OLD, C_OLD = "{http://www.infpres.com/IEDMS}", "{http://minsvyaz.ru/container}"
DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")
TYPES_271 = {"Документ": "document", "Уведомление": "notification", "Квитанция": "acknowledgment",
             "Транспортный контейнер": "container"}
NOTIFICATIONS_271 = {"Зарегистрирован", "Отказано в регистрации", "Назначен исполнитель",
                     "Доклад подготовлен", "Исполнение", "Поставлен на контроль", "Доклад направлен",
                     "Опубликование"}
# минимальный размер справочника ГАС, при котором сверка имеет смысл (в поставке — 7 записей)
GAS_FULL = 1000


def _text(el):
    return (el.text or "").strip() if el is not None else ""


class Check:
    def __init__(self, label, q, errors):
        self.label, self.q, self.errors = label, q, errors

    def err(self, msg):
        self.errors.append(f"{self.label}: {msg}")

    def path(self, p):
        return "/".join(self.q + x for x in p.split("/"))

    def el(self, parent, p, what=None, pattern=None, fmt=None, required=True):
        """Элемент с непустым текстом. Возвращает элемент или None."""
        e = parent.find(self.path(p)) if parent is not None else None
        if e is None:
            if required:
                self.err(f"нет обязательного элемента {what or p}")
            return None
        v = _text(e)
        if not v and len(e) == 0:
            self.err(f"не заполнено {what or p}")
        elif pattern and v and not pattern.match(v):
            self.err(f"{what or p} = «{v}»: ожидается {fmt}")
        return e

    def attr(self, e, name, what, pattern=None, fmt=None, required=True):
        if e is None:
            return None
        v = e.get(self.q + name)
        if v is None:
            v = e.get(name)
        if v is None or not v.strip():
            if required:
                self.err(f"не заполнен атрибут {what}")
            return None
        if pattern and not pattern.match(v):
            self.err(f"{what} = «{v}»: ожидается {fmt}")
        return v


def uuid_attrs(root, label, errors):
    """Все атрибуты *uid / *Uid — UUID в нижнем регистре (п. 1.6, 2.4, 3.4 бота)."""
    for e in root.iter():
        for k, v in e.attrib.items():
            name = k.split("}")[-1]
            if name.lower().endswith("uid") and v and not UUID.match(v):
                tag = e.tag.split("}")[-1]
                hint = "UUID в нижнем регистре" if UUID_ANY.match(v) else "UUID вида xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                errors.append(f"{label}: {tag}/@{name} = «{v}»: ожидается {hint}")


def message_30(root, label, errors):
    c = Check(label, M30, errors)
    info = {"format": "3.0", "receivers": []}
    h = c.el(root, "header", "header")
    info["msg_uid"] = c.attr(h, "msgUid", "header/@msgUid", UUID, "UUID в нижнем регистре")
    src = c.el(h, "source", "наименование отправителя (header/source)")
    info["source"] = {"guid": c.attr(src, "uid", "header/source/@uid", UUID, "UUID"), "name": _text(src)}
    c.el(h, "created", "header/created", DT_TZ, "дата-время с часовым поясом, например 2026-10-21T10:00:00+03:00")
    tl = c.el(h, "timeLimit", "header/timeLimit", required=False)
    if tl is not None and not re.fullmatch(r"[1-9]\d*", _text(tl)):
        c.err(f"header/timeLimit = «{_text(tl)}»: ожидается положительное целое")
    payload = c.el(root, "payload", "payload")
    if payload is not None:
        kinds = [x for x in ("container", "receipt") if payload.find(M30 + x) is not None]
        if len(kinds) != 1:
            c.err("в payload должен быть ровно один из элементов container или receipt")
        cont = payload.find(M30 + "container")
        if cont is not None:
            info["kind"] = "container"
            c.attr(cont, "secure", "payload/container/@secure")
            t = c.el(cont, "type", "наименование типа ТК (payload/container/type)")
            info["container_type"] = c.attr(t, "id", "payload/container/type/@id")
            info["secure"] = (cont.get(M30 + "secure") or "").lower() in ("true", "1")
            f = c.el(cont, "file", "имя файла ТК (payload/container/file)", TK_NAME,
                     "имя вида [a-zA-Z0-9_].edc.zip")
            info["container_file"] = _text(f)
        rec = payload.find(M30 + "receipt")
        if rec is not None:
            info["kind"] = "receipt"
            c.attr(rec, "onMsgUid", "payload/receipt/@onMsgUid", UUID, "UUID")
    rs = c.el(root, "receivers", "receivers")
    seen = set()
    for r in (rs.findall(M30 + "receiver") if rs is not None else []):
        g = c.attr(r, "uid", "receivers/receiver/@uid", UUID, "UUID")
        if not _text(r):
            c.err(f"не заполнено наименование получателя {g or ''}".rstrip())
        if g in seen:
            c.err(f"получатель {g} указан дважды")
        seen.add(g)
        info["receivers"].append({"guid": g, "name": _text(r)})
    if rs is not None and not info["receivers"]:
        c.err("в receivers нет ни одного receiver")
    return info


def message_271(root, label, errors):
    c = Check(label, M27, errors)
    info = {"format": "2.7.1", "receivers": []}
    ver = c.attr(root, "version", "communication/@version")
    if ver and ver != "2.7.1":
        c.err(f"communication/@version = «{ver}», ожидается 2.7.1")
    h = c.el(root, "header", "header")
    typ = c.attr(h, "type", "header/@type")
    info["msg_uid"] = c.attr(h, "uid", "header/@uid", UUID, "UUID в нижнем регистре")
    c.attr(h, "created", "header/@created", DT_TZ, "дата-время с часовым поясом")
    src = c.el(h, "source", "header/source")
    org = c.el(src, "organization", "наименование отправителя (header/source/organization)")
    info["source"] = {"guid": c.attr(src, "uid", "header/source/@uid", UUID, "UUID"), "name": _text(org)}
    if typ:
        branch = TYPES_271.get(typ)
        if branch is None:
            c.err(f"header/@type = «{typ}»: допустимо {', '.join(TYPES_271)}")
        else:
            info["kind"] = branch
            b = c.el(root, branch, f"ветка {branch} для типа «{typ}»")
            if branch == "document":
                c.err("тип «Документ»: по приказу 611/96 документ передаётся только в транспортном контейнере")
            if branch == "container" and b is not None:
                f = c.el(b, "body", "имя файла ТК (container/body)", TK_NAME, "имя вида [a-zA-Z0-9_].edc.zip")
                info["container_file"] = _text(f)
            if branch == "notification" and b is not None:
                nt = c.attr(b, "type", "notification/@type")
                if nt and nt not in NOTIFICATIONS_271:
                    c.err(f"notification/@type = «{nt}»: допустимо {', '.join(sorted(NOTIFICATIONS_271))}")
    di = c.el(root, "deliveryIndex", "deliveryIndex")
    for d in (di.findall(f"{M27}destination/{M27}destination") if di is not None else []):
        g = c.attr(d, "uid", "deliveryIndex/destination/destination/@uid", UUID, "UUID")
        o = d.find(M27 + "organization")
        if not _text(o):
            c.err(f"не заполнено наименование получателя {g or ''}".rstrip())
        info["receivers"].append({"guid": g, "name": _text(o)})
    if di is not None and not info["receivers"]:
        c.err("в deliveryIndex нет ни одного получателя")
    return info


def message_27(root, label, errors):
    """Сообщение 2.7 или 2.6 (и ниже): формат — атрибут version; тип «Документ» допустим (2.6),
    deliveryIndex и часовой пояс в датах необязательны (схема 2.7)."""
    c = Check(label, M_OLD, errors)
    ver = c.attr(root, "version", "communication/@version")
    info = {"format": ver or "2.7", "receivers": []}
    h = c.el(root, "header", "header")
    typ = c.attr(h, "type", "header/@type")
    info["msg_uid"] = c.attr(h, "uid", "header/@uid", UUID, "UUID в нижнем регистре")
    c.attr(h, "created", "header/@created", DT, "дата-время")
    src = c.el(h, "source", "header/source")
    org = c.el(src, "organization", "наименование отправителя (header/source/organization)")
    info["source"] = {"guid": c.attr(src, "uid", "header/source/@uid", UUID, "UUID"), "name": _text(org)}
    if typ:
        branch = TYPES_271.get(typ)
        if branch is None:
            c.err(f"header/@type = «{typ}»: допустимо {', '.join(TYPES_271)}")
        else:
            info["kind"] = branch
            b = c.el(root, branch, f"ветка {branch} для типа «{typ}»")
            if branch == "container" and b is not None:
                f = c.el(b, "body", "имя файла ТК (container/body)")
                info["container_file"] = _text(f)
            if branch == "document" and b is not None:
                info["files"] = [c.attr(f, "localName", "files/file/@localName")
                                 for f in root.findall(f"{M_OLD}files/{M_OLD}file")]
    di = root.find(M_OLD + "deliveryIndex")
    for d in (di.findall(f"{M_OLD}destination/{M_OLD}destination") if di is not None else []):
        g = c.attr(d, "uid", "deliveryIndex/destination/destination/@uid", UUID, "UUID")
        info["receivers"].append({"guid": g, "name": _text(d.find(M_OLD + "organization"))})
    return info


def _org(c, parent, what):
    o = c.el(parent, "organization", f"{what}/organization")
    c.attr(o, "id", f"{what}/organization/@id", UUID, "UUID")
    c.el(o, "title", f"наименование организации ({what}/organization/title)")


def container_30(root, label, errors, msg):
    c = Check(label, C30, errors)
    d = c.el(root, "document", "document")
    uid = c.attr(d, "docUid", "document/@docUid", UUID, "UUID в нижнем регистре")
    if uid and msg and uid == msg.get("msg_uid"):
        c.err("document/@docUid совпадает с идентификатором сообщения msgUid")
    if d is not None and d.find(C30 + "textFile") is None:
        c.err("нет обязательного элемента document/textFile")
    req = c.el(root, "requisites", "requisites")
    for name, what in (("documentKind", "вид документа"), ("documentPlace", "место составления"),
                       ("documentClass", "гриф ограничения доступа")):
        e = c.el(req, name, f"{what} (requisites/{name})")
        c.attr(e, "id", f"requisites/{name}/@id")
    cls = req.find(C30 + "documentClass") if req is not None else None
    if cls is not None and cls.get(C30 + "id") == "DC00000001" and msg and not msg.get("secure"):
        c.err("гриф DC00000001 (ДСП), а в message.xml payload/container/@secure не true")
    authors = root.findall(f"{C30}authors/{C30}author")
    if not authors:
        c.err("нет ни одного автора (authors/author)")
    for a in authors:
        _org(c, a, "authors/author")
        reg = c.el(a, "registration", "регистрационные данные (authors/author/registration)")
        c.el(reg, "number", "регистрационный номер (registration/number)")
        c.el(reg, "date", "дата регистрации (registration/date)", DATE, "дата ГГГГ-ММ-ДД")
        for s in a.findall(f"{C30}signs/{C30}sign"):
            c.attr(s, "signFile", "authors/author/signs/sign/@signFile")
            signer = s.find(C30 + "signer")
            if signer is not None:
                c.el(signer, "name", "ФИО подписанта (signer/name)")
    for ad in root.findall(f"{C30}addressees/{C30}addressee"):
        if ad.find(C30 + "organization") is not None:
            _org(c, ad, "addressees/addressee")


def container_271(root, label, errors, msg, q=C27, version="2.7.1"):
    c = Check(label, q, errors)
    uid = c.attr(root, "uid", "container/@uid", UUID, "UUID в нижнем регистре")
    if uid and msg and uid == msg.get("msg_uid"):
        c.err("container/@uid совпадает с идентификатором сообщения header/@uid")
    ver = c.attr(root, "version", "container/@version")
    if ver and ver != version:
        c.err(f"container/@version = «{ver}», ожидается {version}")
    req = c.el(root, "requisites", "requisites")
    for name, what in (("documentKind", "вид документа"), ("classification", "гриф (classification)"),
                       ("documentPlace", "место составления")):
        # в 2.7 гриф и место составления необязательны
        c.el(req, name, f"{what} (requisites/{name})", required=version == "2.7.1" or name == "documentKind")
    authors = root.findall(f"{q}authors/{q}author")
    if not authors:
        c.err("нет ни одного автора (authors/author)")
    for a in authors:
        _org(c, a, "authors/author")
        reg = c.el(a, "registration", "регистрационные данные (authors/author/registration)")
        c.el(reg, "number", "регистрационный номер (registration/number)")
        c.el(reg, "date", "дата регистрации (registration/date)", DATE, "дата ГГГГ-ММ-ДД")
        for s in a.findall(q + "sign"):
            p = s.find(q + "person")
            if p is not None:
                c.el(p, "name", "ФИО подписанта (sign/person/name)")
    d = c.el(root, "document", "document")
    c.attr(d, "localName", "document/@localName")
    for ad in root.findall(f"{q}addressees/{q}addressee"):
        if ad.find(q + "organization") is not None:
            _org(c, ad, "addressees/addressee")


def container_27(root, label, errors, msg):
    container_271(root, label, errors, msg, q=C_OLD, version="1.0")


def check_gas(msg, addresses, gas, warnings):
    """Сверка с ГАС, как пп. 1.3, 1.7, 1.8 бота. gas — {guid: {name, address, ...}}."""
    if not gas or len(gas) < GAS_FULL or not msg:
        return
    by_addr = {}
    for o in gas.values():
        if o.get("address"):
            by_addr.setdefault(o["address"].lower(), []).append(o)
    for a in addresses:
        found = by_addr.get(a.lower())
        if not found:
            warnings.append(f"ГАС: адрес «{a}» из [АДРЕСАТЫ] не найден в справочнике ГАС")
        elif all(o.get("active") is False for o in found):
            warnings.append(f"ГАС: адрес «{a}» в ГАС принадлежит неактивному участнику")
    src = msg.get("source") or {}
    g = (src.get("guid") or "").lower()
    if g:
        o = gas.get(g)
        if o is None:
            warnings.append(f"ГАС: GUID отправителя {g} не найден в справочнике ГАС")
        elif msg.get("format") == "3.0" and src.get("name") and src["name"] != o["name"]:
            warnings.append(f"ГАС: наименование отправителя «{src['name']}» не совпадает с ГАС «{o['name']}»")
    addr_set = {a.lower() for a in addresses}
    for r in msg.get("receivers") or []:
        g = (r.get("guid") or "").lower()
        if not g:
            continue
        o = gas.get(g)
        if o is None:
            warnings.append(f"ГАС: GUID получателя {g} не найден в справочнике ГАС")
            continue
        if o.get("address") and o["address"].lower() not in addr_set:
            warnings.append(f"ГАС: получатель {o['name']} ({g}) имеет адрес {o['address']}, "
                            f"его нет в [АДРЕСАТЫ] ({', '.join(addresses) or 'пусто'})")
        if msg.get("format") == "3.0" and r.get("name") and r["name"] != o["name"]:
            warnings.append(f"ГАС: наименование получателя «{r['name']}» не совпадает с ГАС «{o['name']}»")

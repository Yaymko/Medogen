"""Сборка пакетов по сценарию JSON."""
import json
import re
import os
import shutil

from . import digital, gas, v271, v30
from .common import (RECEIPT_REASONS, Clock, Directory, MedoError, Org, Signer, check_guid,
                     load_json, medo_addresses, new_uuid, safe_dir_name, write_envelope, warn)
from .model import DEFAULT_SIGNER, DocSpec
from .reply import read_package

TYPES = ("document", "gosedo", "citizen_appeal", "receipt", "notification", "assignment")
FORMATS = ("2.7.1", "3.0")


class Ctx:
    """Окружение одного пакета."""

    def __init__(self, run, spec, seq):
        self.run = run
        self.spec = spec
        self.seq = seq
        self.clock = run.clock
        self.orgs = run.orgs
        self.base_dir = run.base_dir
        self.crypto = run.crypto
        self.started = run.clock.now()
        self.format = str(spec.get("format", "3.0"))
        if self.format not in FORMATS:
            raise MedoError(f"format: {self.format!r}, допустимо {', '.join(FORMATS)}")
        self.type = spec.get("type", "document")
        if self.type not in TYPES:
            raise MedoError(f"type: {self.type!r}, допустимо {', '.join(TYPES)}")

        self.reply = None
        src = spec.get("reply_to") or spec.get("based_on")
        if src:
            self.reply = run.resolve_package(src)

        sender = spec.get("from")
        if sender is None and self.reply and spec.get("reply_to"):
            rec = self.reply["receivers"]
            sender = rec[0]["guid"] if rec else None
        if sender is None and self.reply and spec.get("based_on"):
            sender = self.reply["source"]["guid"]
        if sender is None:
            sender = run.default_sender
        if sender is None:
            raise MedoError("не указан отправитель (from) и нет self в справочнике организаций")
        self.sender = self._org(sender, "from")

        to = spec.get("to")
        if to is None and self.reply and spec.get("reply_to"):
            to = [self.reply["source"]["guid"]]
        if to is None and self.reply and spec.get("based_on"):
            to = [r["guid"] for r in self.reply["receivers"]]
        if to is None:
            to = []
        self.to_raw = to if isinstance(to, list) else [to]
        self.to = [self._org(x["org"] if isinstance(x, dict) and "org" in x else x, "to")
                   for x in self.to_raw]


    def auto_number(self, prefix):
        """Номер по умолчанию: новый при каждой сборке (время сборки и порядковый номер пакета)."""
        return f"{prefix}-{self.started:%d%m-%H%M%S}-{self.seq}"
    def _org(self, value, what):
        """Организация из справочника; GUID из входящего пакета подставляется с его названием."""
        if isinstance(value, str) and self.reply:
            v = value.lower()
            if v not in self.orgs.orgs and all(o.guid != v for o in self.orgs.orgs.values()) \
                    and v not in self.orgs.gas:
                for cand in [self.reply["source"]] + self.reply["receivers"]:
                    if cand["guid"] == v:
                        if what != "from":  # адрес МЭДО нужен только получателю
                            warn(f"{what}: {cand['name']} ({v}) нет в справочнике, адрес МЭДО неизвестен")
                        return Org(v, cand["name"], None)
        return self.orgs.get(value, what)


class Run:
    """Один запуск: справочник, настройки, выходная папка."""

    def __init__(self, scenario_path, orgs_paths, out_dir, gas_path=None):
        self.base_dir = os.path.dirname(os.path.abspath(scenario_path))
        self.scenario = load_json(scenario_path)
        self.scenario_name = os.path.splitext(os.path.basename(scenario_path))[0]
        settings = self.scenario.get("settings", {})
        self.settings = settings
        self.clock = Clock(settings.get("timezone", "+03:00"))
        self.crypto = Signer(settings.get("sign_command"))
        self.orgs = Directory()
        self.orgs.gas = gas.by_guid(gas_path)
        self.default_sender = None
        for p in orgs_paths:
            data = load_json(p)
            self.orgs.load(data)
            self.default_sender = data.get("self", self.default_sender)
        if self.scenario.get("orgs"):
            self.orgs.load(self.scenario)
        self.default_sender = self.scenario.get("self", self.default_sender)
        self.out_dir = out_dir
        self.built = {}

    def resolve_package(self, ref):
        if ref.startswith("@"):
            name = ref[1:]
            if name not in self.built:
                raise MedoError(f"reply_to {ref}: пакет с name={name} ещё не собран в этом сценарии")
            return read_package(self.built[name])
        path = ref if os.path.isabs(ref) else os.path.join(self.base_dir, ref)
        return read_package(path)

    def packages(self):
        defaults = self.scenario.get("defaults", {})
        items = self.scenario.get("packages")
        if items is None:
            items = [{k: v for k, v in self.scenario.items()
                      if k not in ("settings", "orgs", "self", "defaults", "description")}]
        return [{**defaults, **p} for p in items]

    def build_all(self):
        results = []
        for i, spec in enumerate(self.packages(), 1):
            name = spec.get("name") or f"{spec.get('type', 'document')}"
            print(f"[{i}] {spec.get('format', '3.0')} {spec.get('type', 'document')}: {name}")
            ctx = Ctx(self, spec, i)
            folder = os.path.join(self.out_dir, safe_dir_name(
                f"{self.scenario_name}_{i:02d}_{ctx.format}_{ctx.type}_{name}"))
            if os.path.exists(folder):
                shutil.rmtree(folder)
            os.makedirs(folder)
            manifest = build_package(ctx, folder)
            manifest["name"] = name
            # manifest лежит вне папки пакета: в пакете должны быть только файлы из envelope.ini
            with open(manifest_path(folder), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            self.built[name] = folder
            results.append((folder, manifest))
            print(f"    -> {folder}")
        return results


def manifest_path(folder):
    """out/_manifests/<папка пакета>.json — сведения о собранном пакете (GUID и пр.)."""
    d = os.path.join(os.path.dirname(folder), "_manifests")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, os.path.basename(folder) + ".json")


# --- Сборка по типам -----------------------------------------------------------

def write_message(ctx, folder, msg_bytes, files, receivers):
    """files: [(имя, bytes)] — ТК; пишет message.xml, ТК и envelope.ini."""
    msg_name = ctx.run.settings.get("message_file", "message.xml")
    with open(os.path.join(folder, msg_name), "wb") as f:
        f.write(msg_bytes)
    for name, data in files:
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
    subject = ctx.spec.get("subject") or ctx.run.settings.get("subject") or "Сообщение МЭДО"
    write_envelope(os.path.join(folder, "envelope.ini"), subject, medo_addresses(receivers),
                   [msg_name] + [n for n, _ in files])
    return [msg_name] + [n for n, _ in files] + ["envelope.ini"]


def org_json(o):
    return {"guid": o.guid, "name": o.name, "address": o.address}


def build_package(ctx, folder):
    if not ctx.to and ctx.type != "assignment":  # у поручения получатели — исполнители
        raise MedoError("не указаны получатели (to)")
    builder = {
        "document": build_document, "gosedo": build_document, "citizen_appeal": build_document,
        "receipt": build_receipt, "notification": build_notification, "assignment": build_assignment,
    }[ctx.type]
    msg_uid = new_uuid()
    manifest = builder(ctx, folder, msg_uid)
    manifest.update({"format": ctx.format, "type": ctx.type, "msg_uid": msg_uid,
                     "from": org_json(ctx.sender), "to": [org_json(o) for o in ctx.to]})
    if ctx.reply:  # основной пакет, на основании которого собран этот (для связей в интерфейсе)
        manifest["parent"] = {"kind": "reply_to" if ctx.spec.get("reply_to") else "based_on",
                              "folder": os.path.abspath(ctx.reply["folder"]),
                              "msg_uid": ctx.reply.get("msg_uid")}
    return manifest


def _time_limit(ctx):
    return ctx.spec.get("time_limit", ctx.run.settings.get("time_limit", 240))


def _send_30_dev(ctx, folder, msg_uid, doc, digital_xml=None, extra=()):
    zbytes, _ = v30.build_dev_container(ctx, doc, digital_xml, extra)
    msg = v30.message(ctx, msg_uid, ctx.to, ("TC00000002", "document.edc.zip", doc.secure),
                      time_limit=_time_limit(ctx))
    return write_message(ctx, folder, msg, [("document.edc.zip", zbytes)], ctx.to)


def _send_30_gosedo(ctx, folder, msg_uid, digital_xml, enclosures, secure=False):
    if "<doc:РегистрацияДокумента>".encode() in digital_xml:
        # ТК ГосЭДО с зарегистрированным документом: целостность обеспечивает подпись
        # /Документ/Signature (показатель 10.7 проверочного бота)
        digital_xml = digital.sign_enveloped(digital_xml)
    zbytes = v30.build_gosedo_container(ctx, digital_xml, enclosures)
    msg = v30.message(ctx, msg_uid, ctx.to, ("TC00000007", "digital.edc.zip", secure),
                      time_limit=_time_limit(ctx))
    return write_message(ctx, folder, msg, [("digital.edc.zip", zbytes)], ctx.to)


def _send_271(ctx, folder, msg_uid, doc, extra=(), notification=None):
    zbytes, _ = v271.build_container(ctx, doc, extra)
    msg = v271.message(ctx, "Транспортный контейнер", msg_uid, ctx.to, body="document.edc.zip",
                       notification=notification)
    return write_message(ctx, folder, msg, [("document.edc.zip", zbytes)], ctx.to)


def _gosedo_enclosures(ctx, doc, include_pdf, extra=()):
    """Приложения для ТК ГосЭДО: только enclosure_* без отдельных файлов подписей."""
    enc, _ = v30.attachment_files(ctx, doc)
    if any(n.endswith(".p7s") for n, _ in enc):
        warn("ТК ГосЭДО: отдельные подписи приложений (.p7s) не входят в допустимый состав "
             "контейнера и пропущены")
        enc = [(n, b) for n, b in enc if not n.endswith(".p7s")]
    enc += list(extra)
    if include_pdf:
        enc.insert(0, ("enclosure_document.pdf", doc.pdf))
    return enc


def _registration_digital(ctx, doc, reg_process_uid, gosedo=False):
    """digital.xml документа, требующего регистрации. В ТК ДЭВ (как эталон «ДЭВ с digital»
    и реальные пакеты) без doc:ПодписиДокумента: подпись документа — в document_sign_*.
    В ТК ГосЭДО файлов подписи нет, поэтому подпись документа — в doc:ПодписиДокумента."""
    block = digital.registration_block(doc, reg_process_uid, ctx.to, doc.created)
    return digital.build(digital.DOC_NOTICE, doc.uid, ctx.sender, doc.created, [], True, True,
                         registration=block, signers=doc.signers if gosedo else None,
                         fixed_template=digital.NOTICE_TEMPLATE, dsp=doc.secure)


class SentNotice:
    """Уведомление 2.7.1 «Поставлен на контроль» (documentSent) в паспорте ЭС с ТК."""

    def __init__(self, d, doc):
        self.kind = "sent"
        self.document_uid = doc.uid
        self.foundation_org = None
        self.foundation_number = doc.reg_number
        self.foundation_date = doc.reg_date
        self.time = d.get("time")
        self.comment = d.get("comment")
        self.need_registration = d.get("need_registration", True)
        self.need_execution = d.get("need_execution", False)
        self.need_publication = d.get("need_publication", False)


CARDINFO_FIELDS = ("Country", "AutoRepublic", "Land", "Area", "AutoArea", "AutoDistrict", "Region", "Town")
CARDINFO_EXT = ("TownShip", "StreetType", "StreetName", "House", "Building", "Flat", "Comment")


def cardinfo(c):
    """CardInfo.txt — XML с данными автора обращения (как в эталоне 2.7.1)."""
    from xml.sax.saxutils import escape

    def el(tag, val, ind):
        return f"{ind}<{tag}>{escape(str(val))}</{tag}>" if val else f"{ind}<{tag} />"

    addr = c.get("address", {})
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<XMLCardInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xmlns:xsd="http://www.w3.org/2001/XMLSchema">',
             el("FirstName", c.get("first_name"), "  "),
             el("MiddleName", c.get("middle_name"), "  "),
             el("LastName", c.get("last_name"), "  "),
             el("FullName", " ".join(x for x in (c.get("last_name"), c.get("first_name"),
                                                 c.get("middle_name")) if x), "  "),
             "  <Address>"]
    lines += [el(k, addr.get(k, "Российская Федерация" if k == "Country" else ""), "    ")
              for k in CARDINFO_FIELDS]
    lines += ["  </Address>", "  <AddressExt>"]
    lines += [el(k, addr.get(k), "    ") for k in CARDINFO_EXT]
    lines += ["  </AddressExt>", el("Email", c.get("email"), "  ")]
    if c.get("phone"):
        lines.append(el("Phone", c["phone"], "  "))
    lines.append("  <RubricList>")
    for r in c.get("rubrics", []):
        lines += ["    <Rubric>", el("RubricCode", r, "      "), "    </Rubric>"]
    lines += ["  </RubricList>", "</XMLCardInfo>"]
    return b"\xef\xbb\xbf" + "\r\n".join(lines).encode("utf-8")


def _link_to_incoming(ctx, d):
    """Ответный документ: связь (links) с документом входящего пакета и аннотация «Ответ на …»."""
    src = ctx.reply
    number = src.get("doc_number") or "б/н"
    date = (src.get("doc_date") or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = None
    d.setdefault("annotation", f"Ответ на № {number}" + (f" от {date}" if date else ""))
    if not src.get("doc_uid") or ctx.spec.get("link_reply") is False:
        if not src.get("doc_uid"):
            warn("ответный документ: во входящем пакете не найден УУИД документа, связь не добавлена")
        return
    org = ctx._org(src["source"]["guid"], "links")
    d["links"] = list(d.get("links") or []) + [
        {"uid": src["doc_uid"], "org": org, "number": number, "date": date}]


def build_document(ctx, folder, msg_uid):
    d = dict(ctx.spec.get("document", {}))
    container = ctx.spec.get("container", "gosedo" if ctx.type == "gosedo" else "dev")
    if ctx.type == "gosedo" and ctx.format != "3.0":
        raise MedoError("ТК ГосЭДО (type=gosedo) существует только в формате 3.0")
    extra = []
    if ctx.type == "document" and ctx.spec.get("reply_to"):
        _link_to_incoming(ctx, d)
    if ctx.type == "citizen_appeal":
        c = ctx.spec.get("citizen") or {}
        fio = " ".join(x for x in (c.get("last_name"), c.get("first_name"), c.get("middle_name")) if x)
        d.setdefault("annotation", "О направлении обращения гражданина по компетенции")
        appeal = {"file": c["appeal_file"]} if c.get("appeal_file") else {"pages": 1}
        appeal["description"] = c.get("appeal_description") or \
            f"Обращение от {fio or 'гражданина'} №{c.get('number', '1-ОГ')} от " \
            f"{ctx.clock.date(c.get('date'))}"
        d["attachments"] = [appeal] + list(d.get("attachments") or [])
    doc = DocSpec(d, ctx)
    manifest = {"doc_uid": doc.uid, "reg_number": doc.reg_number, "reg_date": doc.reg_date}

    if ctx.type == "citizen_appeal":
        card = cardinfo(ctx.spec.get("citizen") or {})
        if ctx.format == "2.7.1":
            extra = [("CardInfo.txt", card, len(doc.attachments), "Данные об авторе обращения")]
        else:
            warn("3.0: CardInfo.txt не предусмотрен форматом, данные автора вложены как "
                 "enclosure_cardinfo.txt — уточните требования получателя")
            extra = [("enclosure_cardinfo.txt", card, "Данные об авторе обращения")]

    if ctx.format == "2.7.1":
        notice = None
        if ctx.spec.get("notify_sent"):
            notice = v271.notification(SentNotice(ctx.spec["notify_sent"], doc), ctx.clock)
        manifest["files"] = _send_271(ctx, folder, msg_uid, doc, extra, notice)
        return manifest

    doc.check_30_extensions()
    reg_process = new_uuid()
    if container == "gosedo":
        dig = _registration_digital(ctx, doc, reg_process, gosedo=True)
        enc = _gosedo_enclosures(ctx, doc, d.get("include_pdf"), [(n, b) for n, b, _ in extra])
        manifest["files"] = _send_30_gosedo(ctx, folder, msg_uid, dig, enc, doc.secure)
        manifest["reg_process_uid"] = reg_process
        return manifest
    dig = None
    if ctx.spec.get("with_digital", False):
        dig = _registration_digital(ctx, doc, reg_process)
        manifest["reg_process_uid"] = reg_process
    manifest["files"] = _send_30_dev(ctx, folder, msg_uid, doc, dig, extra)
    return manifest


# --- Квитанции -------------------------------------------------------------------

class ReceiptSpec:
    def __init__(self, ctx):
        r = ctx.spec.get("receipt", {})
        self.on_msg_uid = r.get("on_msg_uid") or (ctx.reply or {}).get("msg_uid")
        if not self.on_msg_uid:
            raise MedoError("receipt: укажите on_msg_uid или reply_to")
        self.on_msg_uid = check_guid(self.on_msg_uid, "receipt.on_msg_uid")
        self.accepted = r.get("accepted", True)
        self.time = r.get("time")
        self.comment = r.get("comment")
        self.error_code = r.get("error_code", None if self.accepted else 0)
        self.accepted_on = ctx.orgs.many(r.get("accepted_on"), "receipt.accepted_on")
        self.rejected_on = ctx.orgs.many(r.get("rejected_on"), "receipt.rejected_on")
        reasons = r.get("reasons") or ([] if self.accepted else [{"id": "101"}])
        self.reasons = []
        for x in reasons:
            if not isinstance(x, dict):
                x = {"id": str(x)}
            rid = str(x["id"])
            name = x.get("name") or RECEIPT_REASONS.get(rid)
            if not name:
                raise MedoError(f"receipt.reasons: код {rid} не найден, укажите name")
            self.reasons.append((rid, name, x.get("comment")))


def build_receipt(ctx, folder, msg_uid):
    r = ReceiptSpec(ctx)
    if ctx.format == "2.7.1":
        msg = v271.message(ctx, "Квитанция", msg_uid, ctx.to, acknowledgment=v271.acknowledgment(r, ctx.clock))
    else:
        msg = v30.message(ctx, msg_uid, ctx.to, receipt=v30.receipt(r), time_limit=_time_limit(ctx))
    return {"on_msg_uid": r.on_msg_uid, "files": write_message(ctx, folder, msg, [], ctx.to)}


# --- Уведомления -----------------------------------------------------------------

class NotificationSpec:
    def __init__(self, ctx):
        n = ctx.spec.get("notification", {})
        rp = ctx.reply or {}
        self.kind = n.get("kind", "registered")
        self.time = n.get("time")
        self.comment = n.get("comment")
        self.document_uid = n.get("document_uid") or rp.get("doc_uid")
        if not self.document_uid:
            raise MedoError("notification: укажите document_uid или reply_to")
        self.document_uid = check_guid(self.document_uid, "notification.document_uid")
        f = n.get("foundation", {})
        self.foundation_org = f.get("organization", rp.get("doc_org"))
        self.foundation_number = str(f.get("number", rp.get("doc_number") or "б/н"))
        self.foundation_date = f.get("date", rp.get("doc_date")) or None
        self.number = str(n.get("number") or ctx.auto_number("ВХ"))
        self.date = ctx.clock.date(n.get("date"))
        self.reasons = [digital.refusal_reason(x) for x in n.get("reasons", ["203"])]
        self.secretary = n.get("secretary", {"person": "Секретарь"})
        self.manager = n.get("manager", {"person": "Руководитель"})
        self.executor = n.get("executor", {"person": "Исполнитель"})
        self.signatory = n.get("signatory", {"person": "Подписант", "post": "Руководитель"})
        rep = n.get("report", {})
        self.report_org = rep.get("organization", ctx.sender.name)
        self.report_number = str(rep.get("number") or ctx.auto_number("ИСХ"))
        self.report_date = ctx.clock.date(rep.get("date"))
        self.text = n.get("text", "Документ находится на исполнении")
        self.published_at = n.get("published_at")
        self.publication_point = n.get("publication_point", "Официальный интернет-портал правовой информации")
        # 3.0
        self.process_uid = n.get("process_uid") or rp.get("reg_process_uid") or self.document_uid
        self.process_title = rp.get("reg_process_title") or \
            f"Регистрация от {self.foundation_date or ''} #{self.foundation_number}"
        self.process_created = rp.get("reg_process_created")
        self.event_code = n.get("event_code")
        if self.kind not in v271.NOTIFICATION_TYPES:
            raise MedoError(f"notification.kind: {self.kind!r}, допустимо {', '.join(v271.NOTIFICATION_TYPES)}")


def build_notification(ctx, folder, msg_uid):
    n = NotificationSpec(ctx)
    if ctx.format == "2.7.1":
        msg = v271.message(ctx, "Уведомление", msg_uid, ctx.to, notification=v271.notification(n, ctx.clock))
        return {"doc_uid": n.document_uid, "files": write_message(ctx, folder, msg, [], ctx.to)}
    if n.kind not in ("registered", "refused"):
        raise MedoError("в 3.0 уведомления передаются событиями digital.xml: поддерживаются "
                        "kind=registered и refused; события поручений — через type=assignment")
    original_sender = ctx.to[0]
    when = ctx.clock.stamp(n.time)
    block = digital.registration_answer(n.kind, n, ctx.sender, original_sender, when)
    doc_uid = new_uuid()
    dig = digital.build(digital.DOC_NOTICE, doc_uid, ctx.sender, when, [block], False, False,
                        fixed_template=digital.NOTICE_TEMPLATE)
    files = _send_30_gosedo(ctx, folder, msg_uid, dig, [])
    return {"doc_uid": doc_uid, "on_doc_uid": n.document_uid, "process_uid": n.process_uid, "files": files}


# --- Поручения ---------------------------------------------------------------------

class Item:
    pass


class AssignmentSpec:
    def __init__(self, ctx, default_author):
        a = ctx.spec.get("assignment", {})
        rp = ctx.reply or {}
        self.event = a.get("event", "issue")
        self.process_uid = a.get("process_uid") or rp.get("process_uid") or new_uuid()
        self.process_created = a.get("process_created") or rp.get("process_created") or ctx.clock.stamp()
        author = a.get("author") or {}
        self.author_name = author.get("name") or default_author
        self.author_id = str(author.get("id", "1"))
        raw_items = a.get("items") or rp.get("items") or [{"number": "1"}]
        default_exec = ctx.to[0] if ctx.to else None
        self.items = []
        for i, x in enumerate(raw_items, 1):
            it = Item()
            it.number = str(x.get("number", i))
            it.text = x.get("text", "Проработать вопрос и представить предложения.")
            it.deadline = ctx.clock.date(x.get("deadline", "today+30"))
            it.executor = ctx._org(x["executor"], "assignment.executor") if x.get("executor") else default_exec
            if it.executor is None:
                raise MedoError("assignment: не указан исполнитель")
            it.coexecutors = [ctx._org(c, "assignment.coexecutors") for c in x.get("coexecutors", [])]
            it.new_deadline = ctx.clock.date(x.get("new_deadline", a.get("new_deadline", "today+60")))
            it.remarks = x.get("remarks", a.get("remarks", "Доклад требует доработки."))
            it.fix_deadline = ctx.clock.date(x.get("fix_deadline", a.get("fix_deadline", "today+14")))
            self.items.append(it)


ASSIGNMENT_TITLES = {
    "issue": "О выдаче цифрового поручения",
    "change_deadline": "Изменение срока поручения",
    "return": "Возвращение доклада на доработку",
    "accept": "Принятие информации об исполнении",
    "stop": "Прекращение контроля поручения",
}


def build_assignment(ctx, folder, msg_uid):
    a_raw = ctx.spec.get("assignment", {})
    event = a_raw.get("event", "issue")
    if event not in digital.EVENTS:
        raise MedoError(f"assignment.event: {event!r}, допустимо {', '.join(digital.EVENTS)}")
    d = dict(ctx.spec.get("document", {}))
    d.setdefault("kind", "DK00000073" if event == "issue" else "DK00000000")
    d.setdefault("annotation", ASSIGNMENT_TITLES[event])
    first_signer = (d.get("signers") or [d.get("signer") or {}])[0]
    a = AssignmentSpec(ctx, first_signer.get("name") or DEFAULT_SIGNER["name"])
    if not ctx.spec.get("to"):
        recips = []
        for it in a.items:
            for o in [it.executor] + it.coexecutors:
                if o.guid not in [r.guid for r in recips]:
                    recips.append(o)
        ctx.to = recips
        ctx.to_raw = recips
    if not ctx.to:
        raise MedoError("не указаны получатели (to)")
    doc = DocSpec(d, ctx)
    when = doc.created
    messages = digital.assignment_messages(event, a, ctx.sender, when)
    manifest = {"doc_uid": doc.uid, "process_uid": a.process_uid, "event": event,
                "items": [{"number": it.number, "executor": it.executor.guid} for it in a.items]}
    if event == "issue":
        reg_process = new_uuid()
        block = digital.registration_block(doc, reg_process, ctx.to, when)
        dig = digital.build(digital.DOC_RESOLUTION, doc.uid, ctx.sender, when, messages, True, True,
                            registration=block, signers=doc.signers)
        manifest["reg_process_uid"] = reg_process
    else:
        dig = digital.build(digital.DOC_NOTICE, doc.uid, ctx.sender, when, messages, True, False,
                            signers=doc.signers, fixed_template=digital.NOTICE_TEMPLATE)

    if ctx.format == "2.7.1":
        notice = None
        if event == "issue" and ctx.spec.get("notify_sent", {}) is not None:
            notice = v271.notification(SentNotice(ctx.spec.get("notify_sent") or {}, doc), ctx.clock)
        manifest["files"] = _send_271(ctx, folder, msg_uid, doc, [("digital.xml", dig, 101, None)], notice)
        return manifest
    if ctx.spec.get("container", "dev") == "gosedo":
        enc = _gosedo_enclosures(ctx, doc, d.get("include_pdf"))
        manifest["files"] = _send_30_gosedo(ctx, folder, msg_uid, dig, enc, doc.secure)
    else:
        manifest["files"] = _send_30_dev(ctx, folder, msg_uid, doc, dig)
    return manifest

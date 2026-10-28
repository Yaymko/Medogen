"""Формат 2.7.1: паспорт ТК (urn:IEDMS:CONTAINER) и паспорт ЭС (urn:IEDMS:MESSAGE)."""
from . import stamps
from .common import make_zip, read_file, warn
from .xmlb import E, document

NS_C = "urn:IEDMS:CONTAINER"
NS_M = "urn:IEDMS:MESSAGE"
VERSION = "2.7.1"


def _stamp(tag, name, s):
    return E(tag, None, {"xdms:localName": name},
             E("xdms:position", None, None,
               E("xdms:page", s.page),
               E("xdms:topLeft", None, None, E("xdms:x", s.x), E("xdms:y", s.y)),
               E("xdms:dimension", None, None, E("xdms:w", s.w), E("xdms:h", s.h))))


def _org(o, tag="xdms:organization"):
    return E(tag, None, {"xdms:id": o.guid}, E("xdms:title", o.name))


def _person(p, tag="xdms:person"):
    if not p:
        return None
    return E(tag, None, {"xdms:id": p.get("id")},
             E("xdms:post", p.get("post")) if p.get("post") else None,
             E("xdms:name", p.get("name")) if p.get("name") else None,
             E("xdms:phone", p.get("phone")) if p.get("phone") else None,
             E("xdms:email", p.get("email")) if p.get("email") else None)


TK_SIGN = "container_sign.p7s"


class Dialect:
    """Отличия паспорта ТК 2.7 от 2.7.1: пространство имён, версия, имена файлов, linkType."""
    ns = NS_C
    version = VERSION
    doc_name = "document.pdf"
    reg_stamp = "reg_stamp.png"
    link_type = True
    sign_types = ("Утверждающая", "Заверяющая", "Визирующая")
    container_signature = True

    def sign(self, i):
        return f"document_sign_{i}.p7s", f"sign_stamp_{i}.png"

    def attachment(self, idx, ext):
        return f"attachment_{idx}.{ext}", f"attachment_{idx}_sign.p7s"


def build_container(ctx, doc, extra=(), dialect=None):
    """Собирает document.edc.zip. extra: [(localName, bytes, order, description)] —
    служебные вложения (digital.xml, CardInfo.txt)."""
    dl = dialect or Dialect()
    files = [(dl.doc_name, doc.pdf)]
    signs = []
    for i, s in enumerate(doc.signers):
        sig_name, stamp_name = dl.sign(i)
        stype = s.type
        if stype not in dl.sign_types:
            warn(f"тип подписи «{stype}» нет в схеме {dl.version}, указан «Утверждающая»")
            stype = "Утверждающая"
        sig = read_file(s.file, ctx.base_dir) if s.file else ctx.crypto.sign(doc.pdf, dl.doc_name)
        st = read_file(s.stamp_file, ctx.base_dir) if s.stamp_file else stamps.for_signer(sig, s)
        files += [(sig_name, sig), (stamp_name, st)]
        signs.append(E("xdms:sign", None, None,
                       E("xdms:person", None, {"xdms:id": s.id},
                         E("xdms:post", s.post), E("xdms:name", s.name),
                         E("xdms:phone", s.phone) if s.phone else None,
                         E("xdms:email", s.email) if s.email else None),
                       E("xdms:documentSignature", None, {"xdms:localName": sig_name, "xdms:type": stype},
                         _stamp("xdms:signatureStamp", stamp_name, s.stamp))))
    reg_png = read_file(doc.reg_stamp_file, ctx.base_dir) if doc.reg_stamp_file else stamps.for_document(doc)
    files.append((dl.reg_stamp, reg_png))

    ex = doc.executor
    author = E("xdms:author", None, None,
               E("xdms:organization", None, {"xdms:id": doc.author.guid}, E("xdms:title", doc.author.name)),
               E("xdms:department", doc.department) if doc.department else None,
               E("xdms:registration", None, None,
                 E("xdms:number", doc.reg_number), E("xdms:date", doc.reg_date),
                 _stamp("xdms:registrationStamp", dl.reg_stamp, doc.reg_stamp)),
               *signs,
               E("xdms:executor", None, {"xdms:id": ex.get("id")},
                 E("xdms:post", ex.get("post")) if ex.get("post") else None,
                 E("xdms:name", ex["name"]), E("xdms:phone", ex["phone"]),
                 E("xdms:email", ex.get("email")) if ex.get("email") else None))

    links = None
    if doc.links:
        links = E("xdms:links", None, None, *[
            E("xdms:link", None, {"xdms:uid": ln.uid}, _org(ln.org),
              E("xdms:registration", None, None, E("xdms:number", ln.number), E("xdms:date", ln.date)),
              E("xdms:linkType", ln.type[1], {"xdms:id": ln.type[0]}) if dl.link_type else None)
            for ln in doc.links])

    attachments = []
    for a in doc.attachments:
        name, sig_file = dl.attachment(a.idx, a.ext)
        files.append((name, a.data))
        sig = None
        if a.signed:
            sig = sig_file
            files.append((sig, ctx.crypto.sign(a.data, name)))
        attachments.append(E("xdms:attachment", None, {"xdms:localName": name},
                             E("xdms:order", a.idx - 1),
                             E("xdms:description", a.description) if a.description else None,
                             E("xdms:signature", None, {"xdms:localName": sig}) if sig else None))
    for name, data, order, descr in extra:
        files.append((name, data))
        attachments.append(E("xdms:attachment", None, {"xdms:localName": name},
                             E("xdms:order", order),
                             E("xdms:description", descr) if descr else None))

    enclosure_pages = sum(a.pages for a in doc.attachments)
    tk_sign = ctx.run.settings.get("container_signature", dl.container_signature)
    root = E("xdms:container", None, {"xmlns:xdms": dl.ns, "xdms:uid": doc.uid, "xdms:version": dl.version},
             E("xdms:requisites", None, None,
               E("xdms:documentKind", doc.kind[1], {"xdms:id": doc.kind[0]}),
               E("xdms:documentPlace", doc.place[1], {"xdms:id": doc.place[0]}),
               E("xdms:classification", doc.classification[1], {"xdms:id": doc.classification[0]}),
               E("xdms:annotation", doc.annotation),
               links),
             E("xdms:authors", None, None, author),
             E("xdms:addressees", None, None, *[
                 E("xdms:addressee", None, None, _org(a.org),
                   E("xdms:department", a.department) if a.department else None,
                   _person(a.person))
                 for a in doc.addressees]),
             E("xdms:document", None, {"xdms:localName": dl.doc_name},
               E("xdms:pagesQuantity", doc.pages),
               E("xdms:enclosurePagesQuantity", enclosure_pages) if enclosure_pages else None),
             E("xdms:attachments", None, None, *attachments) if attachments else None,
             E("xdms:containerSignature", None, {"xdms:localName": TK_SIGN}) if tk_sign else None)
    passport = document(root).encode("utf-8")
    if tk_sign:
        # Подпись ТК: passport.xml и остальные файлы по ASCII, как integrity в 3.0 (допущение, см. README)
        data = passport + b"".join(d for _, d in sorted(files, key=lambda f: f[0].encode()))
        files.append((TK_SIGN, ctx.crypto.sign(data, "container")))
    return make_zip([("passport.xml", passport)] + files), passport


def _header(ctx, mtype, uid):
    return E("xdms:header", None, {"xdms:type": mtype, "xdms:uid": uid, "xdms:created": ctx.clock.stamp()},
             E("xdms:source", None, {"xdms:uid": ctx.sender.guid},
               E("xdms:organization", ctx.sender.name)))


def _delivery(receivers):
    return E("xdms:deliveryIndex", None, None, *[
        E("xdms:destination", None, None,
          E("xdms:destination", None, {"xdms:uid": o.guid}, E("xdms:organization", o.name)))
        for o in receivers])


def message(ctx, mtype, uid, receivers, body=None, notification=None, acknowledgment=None,
            ns=NS_M, version=VERSION):
    root = E("xdms:communication", None, {"xmlns:xdms": ns, "xdms:version": version},
             _header(ctx, mtype, uid))
    if acknowledgment is not None:
        root.add(acknowledgment)
    else:
        if body:
            root.add(E("xdms:container", None, None, E("xdms:body", body)))
        root.add(notification)
    root.add(_delivery(receivers))
    return document(root).encode("utf-8")


# --- Уведомления 2.7.1 -------------------------------------------------------

NOTIFICATION_TYPES = {
    "sent": ("Поставлен на контроль", "documentSent"),
    "registered": ("Зарегистрирован", "documentAccepted"),
    "refused": ("Отказано в регистрации", "documentRefused"),
    "executor_assigned": ("Назначен исполнитель", "executorAssigned"),
    "report_prepared": ("Доклад подготовлен", "reportPrepared"),
    "report_sent": ("Доклад направлен", "reportSent"),
    "course_changed": ("Исполнение", "courseChanged"),
    "published": ("Опубликование", "documentPublished"),
}


def _addressee(tag, d):
    """d: {org, person, post, department, contact} -> тип addressee."""
    d = d or {}
    return E(tag, None, {"xdms:uid": d.get("uid")},
             E("xdms:organization", d["organization"]) if d.get("organization") else None,
             E("xdms:person", d["person"]) if d.get("person") else None,
             E("xdms:department", d["department"]) if d.get("department") else None,
             E("xdms:post", d["post"]) if d.get("post") else None,
             E("xdms:contactInfo", d["contact"]) if d.get("contact") else None)


def _docref(tag, org_name, number, date, comment=None):
    return E(tag, None, None,
             E("xdms:organization", org_name) if org_name else None,
             E("xdms:num", None, None, E("xdms:number", number), E("xdms:date", date) if date else None),
             E("xdms:comment", comment) if comment else None)


def notification(n, clock, reason=None):
    """n: NotificationSpec (kind, document_uid, foundation_*...). reason(rid, rname) — текст
    причины отказа (в 2.7/2.6 — значение перечисления схемы)."""
    reason = reason or (lambda rid, rname: f"{rid} {rname}".strip())
    ntype, tag = NOTIFICATION_TYPES[n.kind]
    body = [E("xdms:time", clock.stamp(n.time)),
            _docref("xdms:foundation", n.foundation_org, n.foundation_number, n.foundation_date)]
    if n.kind == "sent":
        body += [E("xdms:needRegistration", str(n.need_registration).lower()),
                 E("xdms:needExecution", str(n.need_execution).lower()),
                 E("xdms:needPublication", str(n.need_publication).lower())]
    elif n.kind == "registered":
        body.append(E("xdms:num", None, None, E("xdms:number", n.number), E("xdms:date", n.date)))
    elif n.kind == "refused":
        body += [E("xdms:reason", reason(rid, rname)) for rid, rname in n.reasons]
    elif n.kind == "executor_assigned":
        body += [_addressee("xdms:secretary", n.secretary), _addressee("xdms:manager", n.manager),
                 _addressee("xdms:executor", n.executor)]
    elif n.kind == "report_prepared":
        s = n.signatory
        body.append(E("xdms:signatory", None, None,
                      E("xdms:organization", s.get("organization")) if s.get("organization") else None,
                      E("xdms:person", s.get("person")) if s.get("person") else None,
                      E("xdms:post", s.get("post")) if s.get("post") else None,
                      E("xdms:signed", clock.date(s.get("signed")))))
    elif n.kind == "report_sent":
        body.append(_docref("xdms:report", n.report_org, n.report_number, n.report_date))
    elif n.kind == "course_changed":
        body.append(E("xdms:courseText", n.text))
    elif n.kind == "published":
        body += [E("xdms:num", None, None, E("xdms:number", n.number),
                   E("xdms:date", clock.stamp(n.published_at))),
                 E("xdms:publicationPoint", n.publication_point)]
    return E("xdms:notification", None, {"xdms:type": ntype, "xdms:uid": n.document_uid},
             E(f"xdms:{tag}", None, None, *body),
             E("xdms:comment", n.comment) if n.comment else None)


def acknowledgment(r, clock, with_error_code=True):
    return E("xdms:acknowledgment", None, {"xdms:uid": r.on_msg_uid},
             E("xdms:time", clock.stamp(r.time)),
             E("xdms:accepted", "true" if r.accepted else "false"),
             E("xdms:errorCode", r.error_code) if with_error_code and r.error_code is not None else None,
             E("xdms:comment", r.comment) if r.comment else None)

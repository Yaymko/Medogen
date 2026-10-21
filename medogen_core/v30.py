"""Формат 3.0: паспорт ТК (urn:IEDMS:CONTAINER:3.0) и паспорт ЭС (urn:IEDMS:MESSAGE:3.0)."""
from .common import CONTAINER_TYPES, make_zip, read_file, stub_png
from .xmlb import E, document

NS_C = "urn:IEDMS:CONTAINER:3.0"
NS_M = "urn:IEDMS:MESSAGE:3.0"
TK_SIGN = "_tk_signature.p7s"


def _stamp(tag, name, s):
    return E(tag, None, {"xdms:stampFile": name},
             E("xdms:position", None, {"xdms:page": s.page},
               E("xdms:coordinate", None, {"xdms:x": s.x, "xdms:y": s.y}),
               E("xdms:dimension", None, {"xdms:w": s.w, "xdms:h": s.h})))


def _org(o):
    return E("xdms:organization", None, {"xdms:id": o.guid}, E("xdms:title", o.name))


def integrity_order(names):
    """passport.xml первым, остальные файлы по возрастанию кодов ASCII."""
    rest = sorted((n for n in names if n != "passport.xml"), key=lambda s: s.encode("ascii"))
    return ["passport.xml"] + rest


def attachment_files(ctx, doc):
    """Приложения в именах enclosure_*; возвращает (files, xml-элементы attachment)."""
    files, items = [], []
    for a in doc.attachments:
        name = f"enclosure_{a.idx}.{a.ext}"
        files.append((name, a.data))
        sig = None
        if a.signed:
            sig = f"enclosure_{a.idx}_sign.p7s"
            files.append((sig, ctx.crypto.sign(a.data, name)))
        items.append(E("xdms:attachment", None, {"xdms:order": a.idx},
                       E("xdms:mainFile", name),
                       E("xdms:signFile", sig) if sig else None,
                       E("xdms:description", a.description) if a.description else None))
    return files, items


def build_dev_container(ctx, doc, digital=None, extra=()):
    """ТК «Документ в электронном виде» (TC00000002): document.edc.zip.
    extra: [(enclosure-имя, bytes, description)] — дополнительные приложения."""
    files = [("document.pdf", doc.pdf)]
    if digital is not None:
        files.append(("digital.xml", digital))
    stamps_files = []
    reg_png = read_file(doc.reg_stamp_file, ctx.base_dir) if doc.reg_stamp_file else stub_png(300, 80)
    stamps_files.append(("reg_stamp.png", reg_png))
    signs = []
    for i, s in enumerate(doc.signers):
        sig_name, stamp_name = f"document_sign_{i}.p7s", f"sign_stamp_{i}.png"
        sig = read_file(s.file, ctx.base_dir) if s.file else ctx.crypto.sign(doc.pdf, "document.pdf")
        st = read_file(s.stamp_file, ctx.base_dir) if s.stamp_file else stub_png(300, 130)
        files.append((sig_name, sig))
        stamps_files.append((stamp_name, st))
        signs.append(E("xdms:sign", None, {"xdms:signFile": sig_name},
                       E("xdms:type", s.type),
                       _stamp("xdms:stamp", stamp_name, s.stamp),
                       E("xdms:signer", None, {"xdms:id": s.id},
                         E("xdms:post", s.post), E("xdms:name", s.name),
                         E("xdms:phone", s.phone) if s.phone else None,
                         E("xdms:email", s.email) if s.email else None)))
    files += stamps_files
    att_files, att_items = attachment_files(ctx, doc)
    files += att_files
    order = len(doc.attachments)
    for name, data, descr in extra:
        order += 1
        files.append((name, data))
        att_items.append(E("xdms:attachment", None, {"xdms:order": order},
                           E("xdms:mainFile", name),
                           E("xdms:description", descr) if descr else None))

    ex = doc.executor
    links = None
    if doc.links:
        links = E("xdms:links", None, None, *[
            E("xdms:link", None, {"xdms:docUid": ln.uid},
              E("xdms:linkType", ln.type[1], {"xdms:id": ln.type[0]}),
              _org(ln.org),
              E("xdms:registration", None, None, E("xdms:number", ln.number), E("xdms:date", ln.date)))
            for ln in doc.links])

    def addressee(a):
        p = a.person
        return E("xdms:addressee", None, None, _org(a.org),
                 E("xdms:department", a.department) if a.department else None,
                 E("xdms:authority", None, {"xdms:id": p.get("id")},
                   E("xdms:post", p.get("post") or "Руководитель"),
                   E("xdms:name", p["name"]) if p.get("name") else None) if p else None)

    root = E("xdms:container", None, {"xmlns:xdms": NS_C},
             E("xdms:document", None, {"xdms:docUid": doc.uid},
               E("xdms:textFile", "document.pdf"),
               E("xdms:dataFile", "digital.xml") if digital is not None else None,
               E("xdms:annotation", doc.annotation[:4000]) if doc.annotation else None),
             E("xdms:requisites", None, None,
               E("xdms:documentKind", doc.kind[1], {"xdms:id": doc.kind[0]}),
               E("xdms:documentPlace", doc.place[1], {"xdms:id": doc.place[0]}),
               E("xdms:documentClass", doc.classification[1], {"xdms:id": doc.classification[0]}),
               E("xdms:description", doc.annotation)),
             links,
             E("xdms:authors", None, None,
               E("xdms:author", None, None, _org(doc.author),
                 E("xdms:registration", None, None, E("xdms:number", doc.reg_number),
                   E("xdms:date", doc.reg_date)),
                 E("xdms:stamps", None, None, _stamp("xdms:stamp", "reg_stamp.png", doc.reg_stamp)),
                 E("xdms:signs", None, None, *signs),
                 E("xdms:executor", None, {"xdms:id": ex.get("id")},
                   E("xdms:post", ex.get("post")) if ex.get("post") else None,
                   E("xdms:name", ex["name"]), E("xdms:phone", ex["phone"]),
                   E("xdms:email", ex.get("email")) if ex.get("email") else None))),
             E("xdms:addressees", None, None, *[addressee(a) for a in doc.addressees]),
             E("xdms:attachments", None, None, *att_items) if att_items else None)

    names = integrity_order(["passport.xml"] + [n for n, _ in files])
    root.add(E("xdms:integrity", None, {"xdms:signFile": TK_SIGN},
               *[E("xdms:innerFile", n) for n in names]))
    passport = document(root).encode("utf-8")
    all_files = dict(files)
    all_files["passport.xml"] = passport
    # Подпись ТК: последовательно passport.xml и файлы из integrity
    tk_sig = ctx.crypto.sign(b"".join(all_files[n] for n in names), "integrity")
    return make_zip([("passport.xml", passport)] + files + [(TK_SIGN, tk_sig)]), passport


def build_gosedo_container(ctx, digital, enclosures=()):
    """ТК «Цифровой документ ГосЭДО» (TC00000007): digital.edc.zip = digital.xml + enclosure_*."""
    return make_zip([("digital.xml", digital)] + list(enclosures))


def message(ctx, msg_uid, receivers, container=None, receipt=None, time_limit=None):
    """container: (type_id, file_name, secure)."""
    header = E("xdms:header", None, {"xdms:msgUid": msg_uid},
               E("xdms:source", ctx.sender.name, {"xdms:uid": ctx.sender.guid}),
               E("xdms:created", ctx.clock.stamp()),
               E("xdms:timeLimit", time_limit) if time_limit else None)
    if container is not None:
        tid, fname, secure = container
        payload = E("xdms:container", None, {"xdms:secure": "true" if secure else "false"},
                    E("xdms:type", CONTAINER_TYPES[tid], {"xdms:id": tid}),
                    E("xdms:file", fname))
    else:
        payload = receipt
    root = E("xdms:message", None, {"xmlns:xdms": NS_M}, header,
             E("xdms:payload", None, None, payload),
             E("xdms:receivers", None, None, *[
                 E("xdms:receiver", o.name, {"xdms:uid": o.guid}) for o in receivers]))
    return document(root).encode("utf-8")


def receipt(r):
    def receivers(lst):
        if not lst:
            return None
        return E("xdms:onReceivers", None, None,
                 *[E("xdms:receiver", o.name, {"xdms:uid": o.guid}) for o in lst])

    parts = []
    if r.accepted or r.accepted_on:
        parts.append(E("xdms:resultAccept", None, None, receivers(r.accepted_on)))
    if r.reasons:
        parts.append(E("xdms:resultReject", None, None, receivers(r.rejected_on), *[
            E("xdms:error", None, None,
              E("xdms:reason", name, {"xdms:id": rid}),
              E("xdms:comment", comment) if comment else None)
            for rid, name, comment in r.reasons]))
    return E("xdms:receipt", None, {"xdms:onMsgUid": r.on_msg_uid}, *parts)

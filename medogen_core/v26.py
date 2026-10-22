"""Формат 2.6 (и ниже): без транспортного контейнера. document.xml (communication в
http://www.infpres.com/IEDMS) содержит реквизиты документа и список files/file, сами файлы
лежат рядом. Структура по эталону «2.6. Входящее письмо» и схеме «message 27 и 26»."""
from .v27 import NS_M
from .xmlb import E, document

GROUP_TEXT, GROUP_ATTACHMENT = "Текст документа", "Текст приложения"


def document_files(ctx, doc, extra=(), sign=False):
    """[(имя, данные, группа, описание, листы, имя файла подписи)]; extra: [(имя, данные, описание)]."""
    out = [("document.pdf", doc.pdf, GROUP_TEXT, None, doc.pages)]
    out += [(f"document_att{a.idx}.{a.ext}", a.data, GROUP_ATTACHMENT, a.description, a.pages)
            for a in doc.attachments]
    out += [(name, data, GROUP_ATTACHMENT, descr, 0) for name, data, descr in extra]
    return [(n, d, g, ds, p, f"{n}.sig" if sign else None) for n, d, g, ds, p in out]


def _num(number, date):
    return E("xdms:num", None, None, E("xdms:number", number), E("xdms:date", date) if date else None)


def _link(ctx, ln):
    reply_uid = (ctx.reply or {}).get("doc_uid") if ctx.spec.get("reply_to") else None
    return E("xdms:link", None, {"xdms:uid": ln.uid},
             E("xdms:linkType", "В ответ на" if ln.uid == reply_uid else "Связан с"),
             E("xdms:document", None, None,
               _num(ln.number, ln.date),
               E("xdms:signatories", None, None,
                 E("xdms:signatory", None, None, E("xdms:organization", ln.org.name),
                   E("xdms:signed", ln.date or ctx.clock.date(None)))),
               E("xdms:addressees", None, None,
                 E("xdms:addressee", None, {"xdms:uid": ctx.sender.guid},
                   E("xdms:organization", ctx.sender.name))),
               E("xdms:pages", 1),
               E("xdms:annotation", "Документ-основание")))


def _signed_data(ctx, sig_name, signer):
    return E("xdms:signedData", None, None,
             E("xdms:signerInfo", None, {"xdms:signature": sig_name},
               E("xdms:digestTime", ctx.clock.stamp()),
               E("xdms:signer", None, None, E("xdms:organization", ctx.sender.name),
                 E("xdms:person", signer.name), E("xdms:post", signer.post))))


def document_message(ctx, uid, doc, files, version):
    ex = doc.executor
    enclosure_pages = sum(a.pages for a in doc.attachments)
    body = E("xdms:document", None, {"xdms:uid": doc.uid},
             E("xdms:kind", doc.kind[1], {"xdms:id": doc.kind[0]}),
             _num(doc.reg_number, doc.reg_date),
             E("xdms:classification", doc.classification[1], {"xdms:id": doc.classification[0]}),
             E("xdms:signatories", None, None, *[
                 E("xdms:signatory", None, None,
                   E("xdms:organization", ctx.sender.name), E("xdms:person", s.name),
                   E("xdms:department", doc.department) if doc.department else None,
                   E("xdms:post", s.post), E("xdms:signed", doc.reg_date))
                 for s in doc.signers]),
             E("xdms:addressees", None, None, *[
                 E("xdms:addressee", None, {"xdms:uid": a.org.guid},
                   E("xdms:organization", a.org.name),
                   E("xdms:person", a.person) if a.person else None,
                   E("xdms:department", a.department) if a.department else None)
                 for a in doc.addressees]),
             E("xdms:pages", doc.pages),
             E("xdms:enclosuresPages", enclosure_pages) if enclosure_pages else None,
             E("xdms:annotation", doc.annotation),
             E("xdms:links", None, None, *[_link(ctx, ln) for ln in doc.links]) if doc.links else None,
             E("xdms:executor", None, None, E("xdms:organization", ctx.sender.name),
               E("xdms:person", ex["name"]), E("xdms:contactInfo", ex["phone"]) if ex.get("phone") else None))
    file_list = E("xdms:files", None, None, *[
        E("xdms:file", None, {"xdms:localName": name, "xdms:localId": i},
          E("xdms:group", group),
          E("xdms:description", descr) if descr else None,
          E("xdms:pages", pages) if pages else None,
          _signed_data(ctx, sig, doc.signers[0]) if sig else None)
        for i, (name, _data, group, descr, pages, sig) in enumerate(files, 1)])
    root = E("xdms:communication", None, {"xmlns:xdms": NS_M, "xdms:version": version},
             E("xdms:header", None, {"xdms:type": "Документ", "xdms:uid": uid, "xdms:created": ctx.clock.stamp()},
               E("xdms:source", None, {"xdms:uid": ctx.sender.guid}, E("xdms:organization", ctx.sender.name))),
             body, file_list,
             E("xdms:deliveryIndex", None, None, *[
                 E("xdms:destination", None, None,
                   E("xdms:destination", None, {"xdms:uid": o.guid}, E("xdms:organization", o.name)))
                 for o in ctx.to]))
    return document(root).encode("utf-8")

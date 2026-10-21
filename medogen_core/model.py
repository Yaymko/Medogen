"""Разбор раздела "document" сценария в объект, общий для форматов 2.7.1 и 3.0."""
import os

from .common import (CLASSES, KINDS, LINK_TYPES, PLACES, MedoError, new_uuid, read_file,
                     ref, check_guid, stub_pdf, pdf_page_count, warn)

DEFAULT_SIGNER = {"post": "Руководитель", "name": "Иванов Иван Иванович"}
DEFAULT_EXECUTOR = {"name": "Петров Петр Петрович", "phone": "+7 (495) 000-00-00"}

ALLOWED_30_EXT = {"pdf", "zip", "xml", "gosx", "odt", "doc", "docx", "ods", "xls", "xlsx", "odp",
                  "ppt", "pptx", "png", "tiff", "txt", "csv", "rtf", "html"}


class Stamp:
    def __init__(self, d, default):
        d = {**default, **(d or {})}
        self.page, self.x, self.y, self.w, self.h = (int(d[k]) for k in ("page", "x", "y", "w", "h"))


class Signer:
    def __init__(self, d, idx):
        d = {**DEFAULT_SIGNER, **(d or {})}
        self.post = d["post"]
        self.name = d["name"]
        self.id = d.get("id")
        self.phone = d.get("phone")
        self.email = d.get("email")
        self.snils = d.get("snils")  # для СертификатУКЭП в digital.xml; без них — тестовые
        self.inn = d.get("inn")
        self.type = d.get("type", "Утверждающая")
        if self.type not in ("Утверждающая", "Заверяющая", "Визирующая"):
            raise MedoError(f"подписант {self.name}: type должен быть Утверждающая/Заверяющая/Визирующая")
        self.stamp = Stamp(d.get("stamp"), {"page": 1, "x": 74 + idx * 50, "y": 255, "w": 41, "h": 18})
        self.file = d.get("signature_file")  # своя .p7s вместо заглушки
        self.stamp_file = d.get("stamp_file")  # свой .png вместо заглушки


class Attachment:
    def __init__(self, d, idx, base_dir):
        if isinstance(d, str):
            d = {"file": d}
        self.description = d.get("description")
        self.signed = d.get("signed", False)
        if d.get("file"):
            self.data = read_file(d["file"], base_dir)
            self.ext = (d.get("ext") or os.path.splitext(d["file"])[1].lstrip(".") or "bin").lower()
            if not self.description:
                self.description = os.path.basename(d["file"])
        else:
            self.ext = (d.get("ext") or "pdf").lower()
            if self.ext != "pdf":
                raise MedoError(f"приложение {idx}: без file генерируется только pdf-заглушка")
            self.data = stub_pdf([f"Prilozhenie {idx}", self.description or ""], d.get("pages", 1))
        self.pages = pdf_page_count(self.data) if self.ext == "pdf" else 0
        self.idx = idx
        self.sign_data = None  # заполняется при сборке


class Link:
    def __init__(self, d, orgs, clock):
        self.uid = check_guid(d["uid"], "links.uid") if d.get("uid") else new_uuid()
        self.org = orgs.get(d["org"], "links.org")
        self.number = str(d.get("number", "1"))
        self.date = clock.date(d.get("date"))
        self.type = ref(d.get("type", "LT00000000"), LINK_TYPES, "links.type")


class Addressee:
    def __init__(self, item, resolve):
        if isinstance(item, dict) and "org" in item:
            self.org = resolve(item["org"], "to")
            self.department = item.get("department")
            self.person = item.get("person")
        else:
            self.org = resolve(item, "to")
            self.department = None
            self.person = None


class DocSpec:
    def __init__(self, d, ctx, default_kind="DK00000000", default_annotation="Тестовый документ"):
        clock, orgs = ctx.clock, ctx.orgs
        self.uid = check_guid(d["uid"], "document.uid") if d.get("uid") else new_uuid()
        self.kind = ref(d.get("kind", default_kind), KINDS, "document.kind")
        self.place = ref(d.get("place", "DP00000077"), PLACES, "document.place")
        self.classification = ref(d.get("classification", "DC00000000"), CLASSES,
                                  "document.classification")
        self.annotation = d.get("annotation", default_annotation)
        self.reg_number = str(d.get("reg_number") or ctx.auto_number("MG"))
        self.reg_date = clock.date(d.get("reg_date"))
        self.department = d.get("department")
        self.author = ctx.sender
        self.addressees = [Addressee(x, ctx._org) for x in ctx.to_raw]
        signers = d.get("signers") or [d.get("signer") or {}]
        self.signers = [Signer(s, i) for i, s in enumerate(signers)]
        self.executor = {**DEFAULT_EXECUTOR, **(d.get("executor") or {})}
        self.reg_stamp = Stamp(d.get("reg_stamp"), {"page": 1, "x": 34, "y": 74, "w": 38, "h": 10})
        self.reg_stamp_file = d.get("reg_stamp_file")
        self.links = [Link(x, orgs, clock) for x in d.get("links") or []]
        if d.get("pdf"):
            self.pdf = read_file(d["pdf"], ctx.base_dir)
        else:
            self.pdf = stub_pdf([f"MEDO test document No {self.reg_number} of {self.reg_date}",
                                 f"From: {self.author.name}",
                                 f"Subject: {self.annotation}",
                                 f"Document UID: {self.uid}"], d.get("pages", 1))
        self.pages = pdf_page_count(self.pdf)
        self.attachments = [Attachment(a, i + 1, ctx.base_dir)
                            for i, a in enumerate(d.get("attachments") or [])]
        self.created = clock.stamp(d.get("created"))

    @property
    def secure(self):
        return self.classification[0] != "DC00000000"

    def check_30_extensions(self):
        for a in self.attachments:
            if a.ext not in ALLOWED_30_EXT:
                warn(f"приложение {a.idx}: расширение .{a.ext} не входит в список допустимых "
                     f"для 3.0 (enclosure_*.{{{','.join(sorted(ALLOWED_30_EXT))}}})")

"""Форматы 2.7 и 2.6 (и ниже): проверка, чтение входящих, сборка и ответы."""
import contextlib
import glob
import io
import json
import os
import zipfile
import xml.etree.ElementTree as ET
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import builder, common, v271, validate  # noqa: E402
from medogen_core.common import MedoError  # noqa: E402
from medogen_core.reply import read_package  # noqa: E402

DEFAULT_ORGS = os.path.join(ROOT, "orgs", "default.json")
NS_M, NS_C = "{http://www.infpres.com/IEDMS}", "{http://minsvyaz.ru/container}"

REF_DIR = os.environ.get("MEDO_REF_DIR")

DOC_26 = """﻿<?xml version="1.0" encoding="UTF-8"?>
<communication xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" p3:version="2.6" xmlns:p3="http://www.infpres.com/IEDMS" xmlns="http://www.infpres.com/IEDMS">
  <p3:header p3:type="Документ" p3:uid="4cae074d-37aa-4591-bdcd-9d2f52c24342" p3:created="2025-01-30T14:55:24">
    <p3:source p3:uid="9dbfdb2f-4df6-4d52-987c-21cf64d14831">
      <p3:organization>Тестовая организация</p3:organization>
    </p3:source>
  </p3:header>
  <p3:document p3:uid="2d571788-df55-4b31-bff6-47fabfbc7fbb" p3:id="7409">
    <p3:kind p3:id="DK00000000">Другие виды документов</p3:kind>
    <p3:num>
      <p3:number>021</p3:number>
      <p3:date>2025-01-30</p3:date>
    </p3:num>
    <p3:signatories>
      <p3:signatory>
        {region}<p3:organization>Тестовая организация</p3:organization>
        <p3:person>Иванов Иван Иванович</p3:person>
        <p3:post>Руководитель</p3:post>
        <p3:signed>2025-01-30</p3:signed>
      </p3:signatory>
    </p3:signatories>
    <p3:addressees>
      <p3:addressee p3:uid="d9d3138f-a665-44e5-801e-3e9732cf62f1">
        <p3:organization>Правительство Тюменской области</p3:organization>
      </p3:addressee>
    </p3:addressees>
    <p3:pages>1</p3:pages>
    <p3:annotation>Предоставление информации</p3:annotation>
  </p3:document>
  <p3:files>
    <p3:file p3:localName="{name}" p3:localId="1">
      <p3:group>Текст документа</p3:group>
      <p3:pages>1</p3:pages>
    </p3:file>
  </p3:files>
{delivery}</communication>
"""

DELIVERY = """  <p3:deliveryIndex>
    <p3:destination>
      <p3:destination p3:uid="d9d3138f-a665-44e5-801e-3e9732cf62f1">
        <p3:organization>Правительство Тюменской области</p3:organization>
      </p3:destination>
    </p3:destination>
  </p3:deliveryIndex>
"""


REGION = '<p3:region p3:id="DP00000077">Московская область</p3:region>\n        '


def make_26(folder, name="Письмо 1.docx", delivery=True, create_file=True, region=True):
    os.makedirs(folder)
    with open(os.path.join(folder, "document.xml"), "w", encoding="utf-8") as f:
        f.write(DOC_26.replace("{name}", name).replace("{delivery}", DELIVERY if delivery else "")
                .replace("{region}", REGION if region else ""))
    files = ["document.xml"]
    if create_file:
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"PK\x03\x04 test")
        files.append(name)
    env = ("[ПИСЬМО КП ПС СЗИ]\r\nТЕМА=Тест\r\nЭЦП=1\r\n[АДРЕСАТЫ]\r\n0=TEST~MEDOGU\r\n[ФАЙЛЫ]\r\n"
           + "".join(f"{i}={n}\r\n" for i, n in enumerate(files)))
    with open(os.path.join(folder, "envelope.ini"), "wb") as f:
        f.write(env.encode("cp1251"))
    return folder


class ReadOld(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.box.cleanup()

    @unittest.skipUnless(REF_DIR, "MEDO_REF_DIR не задана: эталонные пакеты 2.6/2.7 не проверяются")
    def test_reference_packages(self):
        folders = sorted(glob.glob(os.path.join(REF_DIR, "2.7", "*"))) + \
            sorted(glob.glob(os.path.join(REF_DIR, "2.6", "*")))
        self.assertTrue(folders)
        for folder in folders:
            ver = os.path.basename(os.path.dirname(folder))
            with self.subTest(folder=os.path.basename(folder)):
                self.assertEqual(validate.validate_folder(folder), [])
                info = read_package(folder)
                self.assertEqual(info["format"], ver)
                for key in ("msg_uid", "doc_number", "doc_date", "doc_uid"):
                    self.assertTrue(info.get(key), key)
                self.assertTrue(info["source"]["guid"])

    def test_26_cyrillic_file_name(self):
        folder = make_26(os.path.join(self.box.name, "p"))
        self.assertEqual(validate.validate_folder(folder), [])
        info = read_package(folder)
        self.assertEqual(info["format"], "2.6")
        self.assertEqual(info["doc_uid"], "2d571788-df55-4b31-bff6-47fabfbc7fbb")
        self.assertEqual((info["doc_number"], info["doc_date"]), ("021", "2025-01-30"))
        self.assertEqual(info["receivers"][0]["guid"], "d9d3138f-a665-44e5-801e-3e9732cf62f1")

    def test_read_without_delivery_index(self):
        folder = make_26(os.path.join(self.box.name, "p"), delivery=False)
        self.assertEqual(read_package(folder)["receivers"], [])

    def test_3_7_26_signatory_without_region(self):
        """Бот, показатель 3.7: у signatory[1] документа 2.6 нет region — 0 баллов."""
        folder = make_26(os.path.join(self.box.name, "noreg"), region=False)
        errors = validate.validate_folder(folder)
        self.assertTrue(any("region" in e for e in errors), errors)

    def test_26_missing_listed_file(self):
        folder = make_26(os.path.join(self.box.name, "p"), name="nope.docx", create_file=False)
        errors = validate.validate_folder(folder)
        self.assertTrue(any("nope.docx" in e for e in errors), errors)



def build(tmp, packages):
    """Собирает сценарий; возвращает {name: (папка, манифест)}."""
    scen = os.path.join(tmp, f"s{len(os.listdir(tmp))}.json")
    with open(scen, "w", encoding="utf-8") as f:
        json.dump({"packages": packages}, f, ensure_ascii=False)
    with contextlib.redirect_stdout(io.StringIO()):
        built = builder.Run(scen, [DEFAULT_ORGS], os.path.join(tmp, "out")).build_all()
    return {m["name"]: (f, m) for f, m in built}


def xml_root(path):
    with open(path, "rb") as f:
        return ET.fromstring(f.read().lstrip(b"\xef\xbb\xbf"))


class Build27(unittest.TestCase):
    BASE = {"format": "2.7", "from": "pravit_mo", "to": ["mo_duma"]}

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        common.WARNINGS.clear()

    def tearDown(self):
        self.box.cleanup()

    def one(self, **spec):
        return build(self.box.name, [{**self.BASE, "name": "p", **spec}])["p"][0]

    def assertValid(self, folder):
        self.assertEqual(validate.validate_folder(folder), [])

    def test_document_valid(self):
        folder = self.one(type="document", document={"attachments": [{"pages": 1}, {"pages": 2, "signed": True}]})
        self.assertValid(folder)
        self.assertEqual(sorted(os.listdir(folder)), ["container.edc.zip", "envelope.ini", "message.xml"])
        m = xml_root(os.path.join(folder, "message.xml"))
        self.assertEqual(m.tag, NS_M + "communication")
        self.assertEqual(m.get(NS_M + "version"), "2.7")
        self.assertEqual(m.find(NS_M + "header").get(NS_M + "type"), "Транспортный контейнер")
        with zipfile.ZipFile(os.path.join(folder, "container.edc.zip")) as z:
            names = set(z.namelist())
            p = ET.fromstring(z.read("passport.xml").lstrip(b"\xef\xbb\xbf"))
        self.assertEqual(p.tag, NS_C + "container")
        self.assertEqual(p.get(NS_C + "version"), "1.0")
        self.assertTrue({"passport.xml", "document.pdf", "document_1_SigStamp.sig", "document_1_SigStamp.png",
                         "document_RegStamp.png", "document_att1.pdf", "document_att2.pdf",
                         "document_att2.sig"} <= names, names)

    def test_citizen_appeal_valid(self):
        folder = self.one(type="citizen_appeal", citizen={"last_name": "Петров", "first_name": "Пётр"})
        self.assertValid(folder)
        with zipfile.ZipFile(os.path.join(folder, "container.edc.zip")) as z:
            self.assertIn("CardInfo.txt", z.namelist())

    def test_receipt_accept_and_refuse_valid(self):
        doc = self.one(type="document")
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                folder = build(self.box.name, [{"format": "2.7", "name": "r", "type": "receipt", "reply_to": doc,
                                                "receipt": {"accepted": accepted}}])["r"][0]
                self.assertValid(folder)
                m = xml_root(os.path.join(folder, "message.xml"))
                self.assertEqual(m.find(NS_M + "header").get(NS_M + "type"), "Квитанция")
                self.assertIsNone(m.find(f"{NS_M}acknowledgment/{NS_M}errorCode"))

    def test_notifications_valid(self):
        doc = self.one(type="document")
        for kind in v271.NOTIFICATION_TYPES:
            if kind == "sent":
                continue
            with self.subTest(kind=kind):
                folder = build(self.box.name, [{"format": "2.7", "name": "n", "type": "notification",
                                                "reply_to": doc, "notification": {"kind": kind}}])["n"][0]
                self.assertValid(folder)

    def test_refusal_reason_text_passthrough(self):
        doc = self.one(type="document")
        folder = build(self.box.name, [{"format": "2.7", "name": "n", "type": "notification", "reply_to": doc,
                                        "notification": {"kind": "refused",
                                                         "reasons": [{"id": "x", "name": "Нет подписи"}, "203"]}}])["n"][0]
        self.assertValid(folder)
        self.assertIn("Нет подписи", open(os.path.join(folder, "message.xml"), encoding="utf-8-sig").read())
        self.assertTrue(any("одна причина" in w for w in common.WARNINGS), common.WARNINGS)
        with self.assertRaises(MedoError):
            build(self.box.name, [{"format": "2.7", "name": "n", "type": "notification", "reply_to": doc,
                                   "notification": {"kind": "refused", "reasons": ["101"]}}])

    def test_refusal_reason_plain_text(self):
        doc = self.one(type="document")
        for text in ("Нет подписи", "Перевернуты страницы"):
            with self.subTest(text=text):
                folder = build(self.box.name, [{"format": "2.7", "name": "n", "type": "notification", "reply_to": doc,
                                                "notification": {"kind": "refused", "reasons": [text]}}])["n"][0]
                self.assertValid(folder)
                self.assertIn(text, open(os.path.join(folder, "message.xml"), encoding="utf-8-sig").read())

    def test_errors(self):
        for t in ("assignment", "gosedo"):
            with self.subTest(type=t), self.assertRaisesRegex(MedoError, "только в 2.7.1 и 3.0"):
                self.one(type=t)
        doc = self.one(type="document")
        with self.assertRaisesRegex(MedoError, "Поставлен на контроль"):
            build(self.box.name, [{"format": "2.7", "name": "n", "type": "notification", "reply_to": doc,
                                   "notification": {"kind": "sent"}}])
        folder = self.one(type="document", notify_sent={})
        self.assertValid(folder)
        self.assertTrue(any("notify_sent" in w for w in common.WARNINGS), common.WARNINGS)



class Build26(unittest.TestCase):
    BASE = {"format": "2.6", "from": "pravit_mo", "to": ["mo_duma"]}

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        common.WARNINGS.clear()

    def tearDown(self):
        self.box.cleanup()

    def one(self, **spec):
        return build(self.box.name, [{**self.BASE, "name": "p", **spec}])["p"][0]

    def assertValid(self, folder):
        self.assertEqual(validate.validate_folder(folder), [])

    def test_document_valid(self):
        folder = self.one(type="document", document={"attachments": [{"pages": 1}]})
        self.assertValid(folder)
        self.assertEqual(sorted(os.listdir(folder)),
                         ["document.pdf", "document.xml", "document_att1.pdf", "envelope.ini"])
        m = xml_root(os.path.join(folder, "document.xml"))
        self.assertEqual(m.get(NS_M + "version"), "2.6")
        self.assertEqual(m.find(NS_M + "header").get(NS_M + "type"), "Документ")
        files = m.findall(f"{NS_M}files/{NS_M}file")
        self.assertEqual([(f.get(NS_M + "localName"), f.get(NS_M + "localId")) for f in files],
                         [("document.pdf", "1"), ("document_att1.pdf", "2")])
        self.assertEqual(read_package(folder)["format"], "2.6")

    def test_3_7_signatory_region_from_place(self):
        """Бот, показатель 3.7: регион подписанта с кодом из справочника мест (как documentPlace)."""
        for version in ("2.6", "2.5"):
            with self.subTest(version=version):
                folder = self.one(type="document", format=version, document={"place": "DP00000077"})
                self.assertValid(folder)
                sig = xml_root(os.path.join(folder, "document.xml")).find(
                    f"{NS_M}document/{NS_M}signatories/{NS_M}signatory")
                reg = sig[0]
                self.assertEqual(reg.tag, NS_M + "region")
                self.assertEqual(reg.get(NS_M + "id"), "DP00000077")
                self.assertTrue(reg.text)

    def test_citizen_appeal_valid(self):
        folder = self.one(type="citizen_appeal", citizen={"last_name": "Петров", "first_name": "Пётр"})
        self.assertValid(folder)
        self.assertIn("CardInfo.txt", os.listdir(folder))
        names = [f.get(NS_M + "localName") for f in
                 xml_root(os.path.join(folder, "document.xml")).findall(f"{NS_M}files/{NS_M}file")]
        self.assertIn("CardInfo.txt", names)

    def test_receipt_and_notifications_valid(self):
        doc = self.one(type="document")
        specs = [{"type": "receipt", "receipt": {"accepted": a}} for a in (True, False)]
        specs += [{"type": "notification", "notification": {"kind": k}}
                  for k in v271.NOTIFICATION_TYPES if k != "sent"]
        for sp in specs:
            with self.subTest(spec=sp):
                folder = build(self.box.name, [{"format": "2.6", "name": "r", "reply_to": doc, **sp}])["r"][0]
                self.assertValid(folder)
                self.assertEqual(sorted(os.listdir(folder)), ["document.xml", "envelope.ini"])

    def test_version_25(self):
        folder = build(self.box.name, [{**self.BASE, "format": "2.5", "name": "p", "type": "document"}])["p"][0]
        self.assertValid(folder)
        self.assertEqual(xml_root(os.path.join(folder, "document.xml")).get(NS_M + "version"), "2.5")
        with self.assertRaises(MedoError):
            build(self.box.name, [{**self.BASE, "format": "2.8", "name": "p", "type": "document"}])

    def test_sign_files(self):
        folder = self.one(type="document", sign_files=True, document={"attachments": [{"pages": 1}]})
        self.assertValid(folder)
        m = xml_root(os.path.join(folder, "document.xml"))
        for f in m.findall(f"{NS_M}files/{NS_M}file"):
            sd = f.find(NS_M + "signedData")
            self.assertIsNotNone(sd, f.get(NS_M + "localName"))
            sig = sd.find(NS_M + "signerInfo").get(NS_M + "signature")
            self.assertTrue(os.path.isfile(os.path.join(folder, sig)), sig)



class Replies(unittest.TestCase):
    def setUp(self):
        from medogen_core import webui
        self.box = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.box.name, "orgs"))
        shutil.copy(DEFAULT_ORGS, os.path.join(self.box.name, "orgs", "default.json"))
        self.app = webui.App(self.box.name)

    def tearDown(self):
        self.box.cleanup()

    def build(self, scenario):
        r = self.app.build({"stand": "default.json", "scenario": scenario})
        self.assertIsNone(r["error"], r.get("log"))
        return r["packages"]

    def test_meta_formats(self):
        self.assertEqual(self.app.meta()["formats"], ["3.0", "2.7.1", "2.7", "2.6"])

    def test_replies_keep_format(self):
        for fmt in ("2.7", "2.5"):
            with self.subTest(fmt=fmt):
                doc = self.build({"format": fmt, "type": "document", "from": "pravit_mo", "to": ["mo_duma"]})[0]
                self.assertEqual(doc["format"], fmt)
                card = self.app.package_card(doc["folder"])
                replies = self.build({"packages": [
                    {"format": card["format"], "type": "receipt", "reply_to": "ui:" + doc["id"]},
                    {"format": card["format"], "type": "notification", "reply_to": "ui:" + doc["id"],
                     "notification": {"kind": "registered"}}]})
                for r in replies:
                    self.assertEqual(r["format"], fmt)
                    self.assertEqual(r["errors"], [])

    def test_incoming_26_card(self):
        folder = make_26(os.path.join(self.app.incoming, "in1"))
        cards = {c["id"]: c for c in self.app.packages()}
        card = cards[self.app.pkg_id(folder)]
        self.assertEqual(card["format"], "2.6")
        self.assertEqual(card["reg_number"], "021")

    def test_incoming_26_zip_cp866_names(self):
        """Zip из «Сжатой папки» Windows: имена в cp866 без флага UTF-8."""
        folder = make_26(os.path.join(self.box.name, "src"), name="Письмо.docx")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for n in os.listdir(folder):
                arc = "XXXXXX.docx" if n == "Письмо.docx" else n
                z.write(os.path.join(folder, n), arc)
        data = buf.getvalue().replace(b"XXXXXX.docx", "Письмо.docx".encode("cp866"))
        up = self.app.upload("пакет.zip", data)
        card = self.app.incoming_package({"upload": up["path"]})
        self.assertEqual(card["format"], "2.6")
        self.assertEqual(card["errors"], [])

    def test_reply_to_26_without_delivery_index(self):
        folder = make_26(os.path.join(self.app.incoming, "in2"), delivery=False)
        pid = self.app.pkg_id(folder)
        for p in [{"type": "receipt", "reply_to": "ui:" + pid},
                  {"type": "notification", "reply_to": "ui:" + pid, "notification": {"kind": "refused", "reasons": ["Нет подписи"]}},
                  {"type": "document", "reply_to": "ui:" + pid, "document": {}}]:
            with self.subTest(type=p["type"]):
                r = self.build({"format": "2.6", **p})[0]
                self.assertEqual(r["errors"], [])

    def test_reply_document_to_26_links(self):
        doc = self.build({"format": "2.6", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]})[0]
        ans = self.build({"format": "2.6", "type": "document", "reply_to": "ui:" + doc["id"], "document": {}})[0]
        self.assertEqual(ans["errors"], [])
        m = xml_root(os.path.join(ans["folder"], "document.xml"))
        link = m.find(f"{NS_M}document/{NS_M}links/{NS_M}link")
        self.assertEqual(link.get(NS_M + "uid"), doc["doc_uid"])
        self.assertEqual(link.findtext(NS_M + "linkType"), "В ответ на")
        self.assertTrue(m.findtext(f"{NS_M}document/{NS_M}annotation").startswith("Ответ на №"))


if __name__ == "__main__":
    unittest.main()

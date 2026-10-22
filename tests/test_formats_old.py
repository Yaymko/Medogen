"""Форматы 2.7 и 2.6 (и ниже): проверка, чтение входящих, сборка и ответы."""
import glob
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import validate  # noqa: E402
from medogen_core.reply import read_package  # noqa: E402

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
        <p3:organization>Тестовая организация</p3:organization>
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


def make_26(folder, name="Письмо 1.docx", delivery=True, create_file=True):
    os.makedirs(folder)
    with open(os.path.join(folder, "document.xml"), "w", encoding="utf-8") as f:
        f.write(DOC_26.replace("{name}", name).replace("{delivery}", DELIVERY if delivery else ""))
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

    def test_26_missing_listed_file(self):
        folder = make_26(os.path.join(self.box.name, "p"), name="nope.docx", create_file=False)
        errors = validate.validate_folder(folder)
        self.assertTrue(any("nope.docx" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()

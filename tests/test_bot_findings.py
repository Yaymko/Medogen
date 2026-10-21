"""Регрессионные тесты по замечаниям проверочного бота МЭДО.

Каждый тест закрепляет одно найденное ботом расхождение (номер показателя в имени теста).
Запуск из папки medogen:  python -m unittest discover -s tests -v
Только стандартная библиотека; пакеты собираются через medogen.py build во временную папку.
MEDOGEN_ROOT=<папка> позволяет прогнать тесты на другой копии генератора."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NS = {
    "doc": "urn:Doc01-002-00001:DocumentMeta:v1.0.0",
    "mdm": "urn:Adm01-003:MasterDomainMeta:v1.0.0",
    "exc401": "urn:Exc01-004-00001:ExchangeMeta:v1.0.0",
    "sdm004": "urn:Adm01-004:SubjectDomainMeta:v1.0.0",
    "c30": "urn:IEDMS:CONTAINER:3.0",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}

SCENARIO = {
    "defaults": {"format": "3.0"},
    "packages": [
        {"name": "dev", "type": "document", "with_digital": True, "from": "pravit_mo", "to": ["mo_duma"],
         "document": {"annotation": "О согласовании проекта"}},
        {"name": "gosedo", "type": "gosedo", "from": "pravit_mo", "to": ["mo_duma"],
         "document": {"annotation": "О направлении информации", "reg_number": "ЦД-1"}},
        {"name": "registered", "type": "notification", "reply_to": "@dev",
         "notification": {"kind": "registered", "number": "ВХ-777", "date": "today"}},
    ],
}


def run_cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "medogen.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def snils_ok(s):
    if not re.fullmatch(r"\d{11}", s):
        return False
    total = sum(int(c) * (9 - i) for i, c in enumerate(s[:9]))
    check = total if total < 100 else 0 if total in (100, 101) else total % 101
    return (0 if check == 100 else check) == int(s[9:])


def inn_fl_ok(s):
    if not re.fullmatch(r"\d{12}", s):
        return False
    d = [int(c) for c in s]
    k11 = sum(x * k for x, k in zip(d, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10
    k12 = sum(x * k for x, k in zip(d, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10
    return d[10] == k11 and d[11] == k12


class BotFindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        scen = os.path.join(cls.tmp.name, "bot.json")
        with open(scen, "w", encoding="utf-8") as f:
            json.dump(SCENARIO, f, ensure_ascii=False)
        cls.out = os.path.join(cls.tmp.name, "out")
        r = run_cli("build", scen, "--out", cls.out)
        if r.returncode != 0:
            raise AssertionError(f"сборка не удалась:\n{r.stdout}\n{r.stderr}")
        cls.pkg = {}
        for name in os.listdir(cls.out):
            for key in ("dev", "gosedo", "registered"):
                if f"_{key}" in name:
                    cls.pkg[key] = os.path.join(cls.out, name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tk(self, key, inner):
        folder = self.pkg[key]
        zname = next(n for n in os.listdir(folder) if n.endswith(".edc.zip"))
        with zipfile.ZipFile(os.path.join(folder, zname)) as z:
            return ET.fromstring(z.read(inner))

    def test_dev_digital_has_no_document_signatures(self):
        # бот не обрабатывал ТК ДЭВ с XMLDSig-заглушкой; в эталоне «ДЭВ с digital» блока нет
        root = self.tk("dev", "digital.xml")
        self.assertIsNone(root.find("doc:ПодписиДокумента", NS))

    def test_dev_passport_has_annotation(self):
        root = self.tk("dev", "passport.xml")
        self.assertEqual(root.findtext("c30:document/c30:annotation", namespaces=NS), "О согласовании проекта")

    def test_10_7_gosedo_registered_document_has_container_signature(self):
        root = self.tk("gosedo", "digital.xml")
        self.assertIsNotNone(root.find("doc:РегистрацияДокумента", NS))
        self.assertIsNotNone(root.find("ds:Signature", NS), "нет /Документ/Signature")

    def test_gosedo_keeps_document_signature(self):
        # в ТК ГосЭДО нет .p7s, подпись документа только в digital.xml
        root = self.tk("gosedo", "digital.xml")
        self.assertIsNotNone(root.find("doc:ПодписиДокумента", NS))

    def test_10_2_certificate_holder_has_snils_and_inn(self):
        holder = self.tk("gosedo", "digital.xml").find(".//mdm:ДанныеДержателя", NS)
        tags = [c.tag.split("}")[1] for c in holder]
        self.assertEqual(tags, ["Фамилия", "ИмяОтчество", "СНИЛС", "ИНН"])
        self.assertTrue(snils_ok(holder.findtext("mdm:СНИЛС", namespaces=NS)))
        self.assertTrue(inn_fl_ok(holder.findtext("mdm:ИНН", namespaces=NS)))

    def test_10_2_registered_notice_uses_exc401_registration(self):
        coord = self.tk("registered", "digital.xml").find(".//exc401:Экземпляр/exc401:ДанныеДляКоординации", NS)
        self.assertEqual([c.tag for c in coord], [f"{{{NS['exc401']}}}РегистрацияДокумента"])
        self.assertEqual(coord.findtext("exc401:РегистрацияДокумента/sdm004:НомерРегистрации", namespaces=NS),
                         "ВХ-777")

    def test_generated_packages_pass_validate(self):
        for key, folder in self.pkg.items():
            r = run_cli("validate", folder)
            self.assertEqual(r.returncode, 0, f"{key}: {r.stdout}{r.stderr}")


class ValidateCatchesBotErrors(unittest.TestCase):
    def test_10_7_validate_reports_missing_container_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            scen = os.path.join(tmp, "g.json")
            with open(scen, "w", encoding="utf-8") as f:
                json.dump(SCENARIO["packages"][1] | {"format": "3.0"}, f, ensure_ascii=False)
            out = os.path.join(tmp, "out")
            self.assertEqual(run_cli("build", scen, "--out", out).returncode, 0)
            folder = os.path.join(out, next(n for n in os.listdir(out) if not n.startswith("_")))
            zpath = os.path.join(folder, "digital.edc.zip")
            with zipfile.ZipFile(zpath) as z:
                files = {n: z.read(n) for n in z.namelist()}
            text = files["digital.xml"].decode("utf-8")
            start = text.rindex('<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">')
            end = text.rindex("</Signature>") + len("</Signature>")
            files["digital.xml"] = (text[:start] + text[end:]).encode("utf-8")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for n, b in files.items():
                    z.writestr(n, b)
            r = run_cli("validate", folder)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("10.7", r.stdout + r.stderr)


class AssignmentFromGas(unittest.TestCase):
    """Исполнитель и соисполнитель пункта поручения, выбранные в форме из ГАС (по GUID, не из стенда)."""

    def test_gas_executor_and_coexecutor_become_receivers(self):
        sys.path.insert(0, ROOT)
        from medogen_core import builder
        ex = {"guid": "ade6065f-f3e4-43bf-93cd-cc45e751efef", "name": "Минспорт УР", "address": "IJEVSK_O~MEDOGU"}
        co = {"guid": "44185778-5259-4db7-b20a-78034cdaa4a5", "name": "Минспорт ЯО", "address": "YAROSLAVL_O~MEDOGU"}
        with tempfile.TemporaryDirectory() as tmp:
            gas_path = os.path.join(tmp, "gas.json")
            with open(gas_path, "w", encoding="utf-8") as f:
                json.dump({"orgs": {ex["guid"]: ex, co["guid"]: co}}, f, ensure_ascii=False)
            scen = os.path.join(tmp, "a.json")
            with open(scen, "w", encoding="utf-8") as f:
                json.dump({"format": "3.0", "type": "assignment", "from": "pravit_mo", "to": [],
                           "assignment": {"event": "issue", "items": [
                               {"number": "1", "text": "Проработать", "deadline": "today+30",
                                "executor": ex["guid"], "coexecutors": [co["guid"]]}]}}, f, ensure_ascii=False)
            out = os.path.join(tmp, "out")
            run = builder.Run(scen, [os.path.join(ROOT, "orgs", "default.json")], out, gas_path=gas_path)
            run.build_all()
            folder = os.path.join(out, next(n for n in os.listdir(out) if not n.startswith("_")))
            with open(os.path.join(folder, "envelope.ini"), encoding="cp1251") as f:
                env = f.read()
            self.assertIn("IJEVSK_O~MEDOGU", env)
            self.assertIn("YAROSLAVL_O~MEDOGU", env)


if __name__ == "__main__":
    unittest.main()

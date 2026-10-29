"""Обмен с СЭД через папки OUT, OUTArchiveGen и IN (docs/superpowers/specs/2026-10-22-sed-exchange-folders-design.md).

Временные папки играют роль общих папок сервера СЭД, пакеты собирает сам генератор."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import builder, exchange  # noqa: E402
from medogen_core.common import MedoError  # noqa: E402

DEFAULT_ORGS = os.path.join(ROOT, "orgs", "default.json")
PRAVIT_MO = "b72c0f24-d3dc-4cbf-8bfe-5da94daa18bf"

SCENARIO = {"packages": [
    {"name": "d30", "format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]},
    {"name": "d271", "format": "2.7.1", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]},
]}


def build_packages(tmp):
    scen = os.path.join(tmp, "s.json")
    with open(scen, "w", encoding="utf-8") as f:
        json.dump(SCENARIO, f, ensure_ascii=False)
    out = os.path.join(tmp, "built")
    with contextlib.redirect_stdout(io.StringIO()):
        built = builder.Run(scen, [DEFAULT_ORGS], out).build_all()
    return {m["name"] if "name" in m else os.path.basename(f): (f, m) for f, m in built}


def files_of(folder):
    return sorted(os.path.relpath(os.path.join(c, n), folder) for c, _d, fs in os.walk(folder) for n in fs)


class ExchangeModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        built = build_packages(cls.tmp.name)
        cls.p30, cls.m30 = next(v for v in built.values() if "3.0" in os.path.basename(v[0]))
        cls.p271, _ = next(v for v in built.values() if "2.7.1" in os.path.basename(v[0]))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        b = self.box.name
        self.out, self.arch, self.inn, self.dest = (os.path.join(b, n) for n in ("OUT", "OUTArchiveGen", "IN", "dest"))
        for d in (self.out, self.inn):
            os.makedirs(d)

    def tearDown(self):
        self.box.cleanup()

    def put_out(self, src, name=None):
        target = os.path.join(self.out, name or os.path.basename(src))
        shutil.copytree(src, target)
        return os.path.basename(target)

    def test_list_out_finds_packages_only(self):
        n30, n271 = self.put_out(self.p30), self.put_out(self.p271)
        open(os.path.join(self.out, "x.txt"), "w").close()
        os.makedirs(os.path.join(self.out, "empty"))
        self.put_out(self.p30, ".tmp1")
        self.put_out(self.p30, "_svc")
        items = exchange.list_out(self.out)
        self.assertEqual(sorted(i["name"] for i in items), sorted([n30, n271]))
        i30 = next(i for i in items if i["name"] == n30)
        self.assertEqual(i30["from"]["guid"], PRAVIT_MO)
        self.assertEqual(i30["msg_uid"], self.m30["msg_uid"])
        self.assertEqual(i30["format"], "3.0")

    def test_list_out_unreadable_package_has_error(self):
        os.makedirs(os.path.join(self.out, "broken"))
        open(os.path.join(self.out, "broken", "envelope.ini"), "w").close()
        items = exchange.list_out(self.out)
        self.assertEqual([i["name"] for i in items], ["broken"])
        self.assertTrue(items[0].get("error"))

    def test_list_out_unreachable_folder(self):
        with self.assertRaises(MedoError) as e:
            exchange.list_out(os.path.join(self.box.name, "nope"))
        self.assertIn("нет доступа к", str(e.exception))

    def test_take_copies_and_archives(self):
        name = self.put_out(self.p30)
        copy, archived, err = exchange.take(self.out, self.arch, name, os.path.join(self.dest, "c"))
        self.assertIsNone(err)
        self.assertFalse(os.path.exists(os.path.join(self.out, name)))
        self.assertEqual(archived, os.path.join(self.arch, name))
        self.assertEqual(files_of(copy), files_of(self.p30))
        self.assertEqual(files_of(archived), files_of(self.p30))

    def test_take_name_collision_in_archive(self):
        name = self.put_out(self.p30)
        os.makedirs(os.path.join(self.arch, name))
        _copy, archived, err = exchange.take(self.out, self.arch, name, os.path.join(self.dest, "c"))
        self.assertIsNone(err)
        self.assertEqual(archived, os.path.join(self.arch, name + "_1"))

    def test_take_rejects_missing_and_traversal(self):
        self.put_out(self.p30)
        for bad in ("nope", "..", "a/../..", "", "."):
            with self.subTest(name=bad), self.assertRaises(MedoError):
                exchange.take(self.out, self.arch, bad, os.path.join(self.dest, "c"))

    def test_take_archive_failure_keeps_copy(self):
        name = self.put_out(self.p30)
        open(os.path.join(self.box.name, "OUTArchiveGen"), "w").close()  # архив — файл, перенос невозможен
        copy, archived, err = exchange.take(self.out, self.arch, name, os.path.join(self.dest, "c"))
        self.assertTrue(err)
        self.assertIsNone(archived)
        self.assertEqual(files_of(copy), files_of(self.p30))
        self.assertTrue(os.path.isdir(os.path.join(self.out, name)))

    def test_put_in_writes_whole_folder(self):
        path = exchange.put_in(self.p30, self.inn)
        self.assertEqual(path, os.path.join(self.inn, os.path.basename(self.p30)))
        self.assertEqual(files_of(path), files_of(self.p30))
        self.assertEqual([n for n in os.listdir(self.inn) if n.startswith(".")], [])

    def test_put_in_refuses_existing_then_allows_after_pickup(self):
        path = exchange.put_in(self.p30, self.inn)
        with self.assertRaises(MedoError) as e:
            exchange.put_in(self.p30, self.inn)
        self.assertIn("в IN уже есть пакет", str(e.exception))
        shutil.rmtree(path)  # СЭД забрала пакет
        self.assertEqual(exchange.put_in(self.p30, self.inn), path)

    def test_take_rejects_drive_relative_and_non_packages(self):
        # по ревью: на Windows os.path.join(OUT, "C:x") уходит за пределы OUT
        self.put_out(self.p30, "_svc")
        os.makedirs(os.path.join(self.out, "notpkg"))
        for bad in ("C:", "C:notpkg", "_svc", "notpkg"):
            with self.subTest(name=bad), self.assertRaises(MedoError):
                exchange.take(self.out, self.arch, bad, os.path.join(self.dest, "c"))
        self.assertTrue(os.path.isdir(os.path.join(self.out, "_svc")))

    def test_put_in_copies_envelope_last(self):
        # по ревью: СЭД ищет папки с envelope.ini, он должен появиться последним
        order = []
        real = shutil.copy2
        def spy(src, dst, *a, **k):
            order.append(os.path.basename(src))
            return real(src, dst, *a, **k)
        shutil.copy2 = spy
        try:
            exchange.put_in(self.p30, self.inn)
        finally:
            shutil.copy2 = real
        self.assertEqual(order[-1], "envelope.ini")
        self.assertGreater(len(order), 1)

    def test_default_archive_for_share_root(self):
        import ntpath
        self.assertEqual(exchange.sibling_archive(r"\\srv\MEDO\OUT", ntpath), r"\\srv\MEDO\OUTArchiveGen")
        self.assertEqual(exchange.sibling_archive(r"\\srv\OUT", ntpath), r"\\srv\OUTArchiveGen")
        self.assertEqual(exchange.sibling_archive("D:\\MEDO\\OUT\\", ntpath), "D:\\MEDO\\OUTArchiveGen")

    def test_paths_default_archive(self):
        p = exchange.paths({"exchange": {"out": "/x/OUT", "in": "/x/IN"}})
        self.assertEqual(p["archive"], os.path.join("/x", "OUTArchiveGen"))
        self.assertEqual(p["in"], "/x/IN")
        self.assertIsNone(exchange.paths({}))
        self.assertIsNone(exchange.paths({"exchange": {"out": "", "in": ""}}))


class AppExchange(unittest.TestCase):
    """Методы интерфейса: пути из стенда, забор в _incoming, запись в IN с отметкой в манифесте."""

    def setUp(self):
        from medogen_core import webui
        self.box = tempfile.TemporaryDirectory()
        b = self.box.name
        self.root = os.path.join(b, "root")
        os.makedirs(os.path.join(self.root, "orgs"))
        shutil.copy(DEFAULT_ORGS, os.path.join(self.root, "orgs", "default.json"))
        self.out, self.inn = os.path.join(b, "OUT"), os.path.join(b, "IN")
        for d in (self.out, self.inn):
            os.makedirs(d)
        self.app = webui.App(self.root)
        self.app.exchange_save({"stand": "default.json", "exchange": {"out": self.out, "in": self.inn}})

    def tearDown(self):
        self.box.cleanup()

    def build(self):
        r = self.app.build({"stand": "default.json", "scenario": {
            "format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]}})
        self.assertIsNone(r["error"], r.get("log"))
        return r["packages"][0]

    def test_save_org_keeps_exchange(self):
        self.app.save_org({"stand": "default.json", "org": {
            "key": "new_org", "guid": "11111111-2222-3333-4444-555555555555", "name": "Новая", "address": "NEW~MEDOGU"}})
        with open(os.path.join(self.root, "orgs", "default.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["exchange"]["out"], self.out)
        self.assertEqual(self.app.orgs("default.json")["exchange"]["in"], self.inn)

    def test_take_puts_card_in_incoming(self):
        card = self.build()
        name = os.path.basename(card["folder"])
        shutil.copytree(card["folder"], os.path.join(self.out, name))
        items = self.app.exchange_out("default.json")
        self.assertIsNone(items["error"])
        self.assertEqual([i["name"] for i in items["items"]], [name])
        r = self.app.exchange_take({"stand": "default.json", "name": name})
        self.assertTrue(r["package"]["id"].startswith("_incoming/"))
        self.assertTrue(os.path.isdir(r["archived"]))
        self.assertEqual(self.app.exchange_out("default.json")["items"], [])

    def test_history_shows_stand_of_taken_package(self):
        # Alex: в истории пакетов видно, с какого стенда забран пакет; у собранных — пусто
        built = self.build()
        name = os.path.basename(built["folder"])
        shutil.copytree(built["folder"], os.path.join(self.out, name))
        taken = self.app.exchange_take({"stand": "default.json", "name": name})["package"]
        self.assertEqual(taken["taken_from"]["stand_file"], "default.json")
        self.assertTrue(taken["taken_from"]["stand"])
        self.assertEqual(taken["taken_from"]["out"], self.out)
        cards = {c["id"]: c for c in self.app.packages()}
        self.assertEqual(cards[taken["id"]]["taken_from"]["stand_file"], "default.json")
        self.assertIsNone(cards[built["id"]].get("taken_from"))

    def test_put_refuses_incoming_and_invalid(self):
        card = self.build()
        name = os.path.basename(card["folder"])
        shutil.copytree(card["folder"], os.path.join(self.out, name))
        incoming = self.app.exchange_take({"stand": "default.json", "name": name})["package"]
        bad = self.build()
        env = os.path.join(bad["folder"], "envelope.ini")
        with open(env, encoding="cp1251") as f:
            text = f.read()
        with open(env, "w", encoding="cp1251") as f:
            f.write(text.replace("[ФАЙЛЫ]", "[ПРОЧЕЕ]"))
        res = self.app.exchange_put({"stand": "default.json", "ids": [incoming["id"], bad["id"]]})["results"]
        self.assertEqual([x["ok"] for x in res], [False, False])
        self.assertIn("ошибки проверки", res[1]["error"])
        self.assertEqual(os.listdir(self.inn), [])

    def test_put_records_sent_in(self):
        card = self.build()
        res = self.app.exchange_put({"stand": "default.json", "ids": [card["id"]]})["results"]
        self.assertTrue(res[0]["ok"], res)
        target = os.path.join(self.inn, os.path.basename(card["folder"]))
        self.assertEqual(res[0]["path"], target)
        again = self.app.package_card(card["folder"], with_errors=False)
        self.assertEqual(again["sent_in"][-1]["path"], target)

    def test_put_to_out_lands_in_out_list(self):
        # Alex: сгенерированный пакет можно положить не только в IN, но и в OUT стенда
        card = self.build()
        res = self.app.exchange_put({"stand": "default.json", "ids": [card["id"]], "to": "out"})["results"]
        self.assertTrue(res[0]["ok"], res)
        target = os.path.join(self.out, os.path.basename(card["folder"]))
        self.assertEqual(res[0]["path"], target)
        self.assertEqual(files_of(target), files_of(card["folder"]))
        self.assertEqual(os.listdir(self.inn), [])
        names = [i["name"] for i in self.app.exchange_out("default.json")["items"]]
        self.assertEqual(names, [os.path.basename(card["folder"])])
        again = self.app.package_card(card["folder"], with_errors=False)
        self.assertEqual(again["sent_out"][-1]["path"], target)
        self.assertFalse(again.get("sent_in"))

    def test_put_to_out_refuses_existing(self):
        card = self.build()
        self.app.exchange_put({"stand": "default.json", "ids": [card["id"]], "to": "out"})
        res = self.app.exchange_put({"stand": "default.json", "ids": [card["id"]], "to": "out"})["results"]
        self.assertFalse(res[0]["ok"])
        self.assertIn("в OUT уже есть пакет", res[0]["error"])

    def test_put_to_out_needs_out_folder(self):
        self.app.exchange_save({"stand": "default.json", "exchange": {"in": self.inn}})
        card = self.build()
        with self.assertRaises(MedoError) as e:
            self.app.exchange_put({"stand": "default.json", "ids": [card["id"]], "to": "out"})
        self.assertIn("папка OUT", str(e.exception))

    def test_put_rejects_unknown_target(self):
        card = self.build()
        with self.assertRaises(MedoError):
            self.app.exchange_put({"stand": "default.json", "ids": [card["id"]], "to": "archive"})
        self.assertEqual(os.listdir(self.inn), [])
        self.assertEqual(os.listdir(self.out), [])

    def test_put_batch_survives_bad_item(self):
        card = self.build()
        res = self.app.exchange_put({"stand": "default.json", "ids": [123, card["id"]]})["results"]
        self.assertEqual([x["ok"] for x in res], [False, True])

    def test_put_refuses_disguised_incoming_id(self):
        card = self.build()
        name = os.path.basename(card["folder"])
        shutil.copytree(card["folder"], os.path.join(self.out, name))
        inc = self.app.exchange_take({"stand": "default.json", "name": name})["package"]
        for pid in ("./" + inc["id"], "x/../" + inc["id"]):
            res = self.app.exchange_put({"stand": "default.json", "ids": [pid]})["results"]
            self.assertFalse(res[0]["ok"], pid)
        self.assertEqual(os.listdir(self.inn), [])

    def test_import_orgs_keeps_exchange(self):
        from medogen_core import importer
        src = os.path.join(self.box.name, "orgs.csv")
        with open(src, "w", encoding="utf-8") as f:
            f.write("Минфин;11111111-2222-3333-4444-555555555555;M_MINFIN~MEDOGU\n")
        importer.import_file(src, os.path.join(self.root, "orgs", "default.json"))
        self.assertEqual(self.app.orgs("default.json")["exchange"]["out"], self.out)

    def test_check_and_out_report_unreachable(self):
        missing = os.path.join(self.box.name, "no-such-share", "OUT")
        self.app.exchange_save({"stand": "default.json", "exchange": {"out": missing, "in": self.inn}})
        chk = self.app.exchange_check({"stand": "default.json"})
        self.assertFalse(chk["out"]["ok"])
        self.assertTrue(chk["in"]["ok"], chk)
        self.assertEqual([n for n in os.listdir(self.inn)], [])  # пробный файл удалён
        self.assertIn("нет доступа", self.app.exchange_out("default.json")["error"])


if __name__ == "__main__":
    unittest.main()


def run_cli(*args):
    import subprocess
    return subprocess.run([sys.executable, os.path.join(ROOT, "medogen.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


class CliPut(unittest.TestCase):
    """Командная строка: build --put in|out и put кладут собранные пакеты в папки стенда."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        b = self.box.name
        self.out, self.inn = os.path.join(b, "OUT"), os.path.join(b, "IN")
        for d in (self.out, self.inn):
            os.makedirs(d)
        with open(DEFAULT_ORGS, encoding="utf-8") as f:
            stand = json.load(f)
        stand["exchange"] = {"out": self.out, "in": self.inn}
        self.stand = os.path.join(b, "stand.json")
        with open(self.stand, "w", encoding="utf-8") as f:
            json.dump(stand, f, ensure_ascii=False)
        self.scen = os.path.join(b, "s.json")
        with open(self.scen, "w", encoding="utf-8") as f:
            json.dump({"format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]}, f)
        self.built = os.path.join(b, "built")

    def tearDown(self):
        self.box.cleanup()

    def packages(self, folder):
        return sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n, "envelope.ini")))

    def test_build_put_out(self):
        r = run_cli("build", self.scen, "--orgs", self.stand, "--out", self.built, "--put", "out")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.packages(self.out), self.packages(self.built))
        self.assertEqual(len(self.packages(self.out)), 1)
        self.assertEqual(os.listdir(self.inn), [])
        self.assertIn("OUT", r.stdout)

    def test_build_put_in(self):
        r = run_cli("build", self.scen, "--orgs", self.stand, "--out", self.built, "--put", "in")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.packages(self.inn), self.packages(self.built))
        self.assertEqual(os.listdir(self.out), [])

    def test_put_command_existing_folder(self):
        r = run_cli("build", self.scen, "--orgs", self.stand, "--out", self.built)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.listdir(self.out), [])
        folder = os.path.join(self.built, self.packages(self.built)[0])
        r = run_cli("put", folder, "--orgs", self.stand, "--to", "out")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.packages(self.out), [os.path.basename(folder)])
        r = run_cli("put", folder, "--orgs", self.stand, "--to", "out")  # второй раз — уже лежит
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("в OUT уже есть пакет", r.stdout)

    def test_put_refuses_invalid_package(self):
        r = run_cli("build", self.scen, "--orgs", self.stand, "--out", self.built)
        folder = os.path.join(self.built, self.packages(self.built)[0])
        env = os.path.join(folder, "envelope.ini")
        with open(env, encoding="cp1251") as f:
            text = f.read()
        with open(env, "w", encoding="cp1251") as f:
            f.write(text.replace("[ФАЙЛЫ]", "[ПРОЧЕЕ]"))
        r = run_cli("put", folder, "--orgs", self.stand, "--to", "in")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(self.inn), [])

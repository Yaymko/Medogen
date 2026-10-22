"""Обмен с СЭД через папки OUT, OUTArchive и IN (docs/superpowers/specs/2026-10-22-sed-exchange-folders-design.md).

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
        self.out, self.arch, self.inn, self.dest = (os.path.join(b, n) for n in ("OUT", "OUTArchive", "IN", "dest"))
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
        open(os.path.join(self.box.name, "OUTArchive"), "w").close()  # архив — файл, перенос невозможен
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

    def test_paths_default_archive(self):
        p = exchange.paths({"exchange": {"out": "/x/OUT", "in": "/x/IN"}})
        self.assertEqual(p["archive"], os.path.join("/x", "OUTArchive"))
        self.assertEqual(p["in"], "/x/IN")
        self.assertIsNone(exchange.paths({}))
        self.assertIsNone(exchange.paths({"exchange": {"out": "", "in": ""}}))


if __name__ == "__main__":
    unittest.main()

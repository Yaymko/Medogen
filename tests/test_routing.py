"""Маршрутизация пакетов между стендами (docs/superpowers/specs/2026-10-28-stand-routing-design.md).

Временные папки играют роль OUT, OUTArchiveGen и IN двух стендов, пакеты собирает сам генератор."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import builder, exchange  # noqa: E402
from medogen_core.common import MedoError  # noqa: E402

DEFAULT_ORGS = os.path.join(ROOT, "orgs", "default.json")


def build(tmp, packages):
    scen = os.path.join(tmp, "s.json")
    with open(scen, "w", encoding="utf-8") as f:
        json.dump({"packages": packages}, f, ensure_ascii=False)
    out = os.path.join(tmp, "built")
    with contextlib.redirect_stdout(io.StringIO()):
        built = builder.Run(scen, [DEFAULT_ORGS], out).build_all()
    return [f for f, _m in built]


def age(folder, seconds):
    """Сдвигает mtime папки и всех файлов в ней в прошлое."""
    t = time.time() - seconds
    for cur, _dirs, files in os.walk(folder):
        for n in files:
            os.utime(os.path.join(cur, n), (t, t))
        os.utime(cur, (t, t))


def put(src, out_dir, ago=60):
    target = os.path.join(out_dir, os.path.basename(src))
    shutil.copytree(src, target)
    age(target, ago)
    return os.path.basename(target)


def break_package(folder):
    """Удаляет файл, перечисленный в [ФАЙЛЫ] (не envelope.ini): проверка находит ошибку."""
    victim = next(n for n in sorted(os.listdir(folder)) if n.lower() != "envelope.ini"
                  and os.path.isfile(os.path.join(folder, n)) and not n.endswith(".xml"))
    os.remove(os.path.join(folder, victim))


class RouteSettings(unittest.TestCase):
    def setUp(self):
        from medogen_core import webui
        self.box = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.box.name, "root")
        os.makedirs(os.path.join(self.root, "orgs"))
        shutil.copy(DEFAULT_ORGS, os.path.join(self.root, "orgs", "default.json"))
        self.app = webui.App(self.root)

    def tearDown(self):
        self.box.cleanup()

    def test_paths_has_route_fields(self):
        p = exchange.paths({"exchange": {"out": "O", "route_to": "s2.json", "route_auto": True}})
        self.assertEqual(p["route_to"], "s2.json")
        self.assertIs(p["route_auto"], True)
        p = exchange.paths({"exchange": {"out": "O"}})
        self.assertEqual(p["route_to"], "")
        self.assertIs(p["route_auto"], False)

    def test_save_keeps_route(self):
        self.app.exchange_save({"stand": "default.json", "exchange": {
            "out": "O", "in": "I", "route_to": " s2.json ", "route_auto": True}})
        ex = self.app.orgs("default.json")["exchange"]
        self.assertEqual(ex["route_to"], "s2.json")
        self.assertIs(ex["route_auto"], True)

    def test_import_orgs_keeps_route(self):
        from medogen_core import importer
        self.app.exchange_save({"stand": "default.json", "exchange": {
            "out": "O", "in": "I", "route_to": "s2.json", "route_auto": True}})
        src = os.path.join(self.box.name, "orgs.csv")
        with open(src, "w", encoding="utf-8") as f:
            f.write("Минфин;11111111-2222-3333-4444-555555555555;M_MINFIN~MEDOGU\n")
        importer.import_file(src, os.path.join(self.root, "orgs", "default.json"))
        self.assertEqual(self.app.orgs("default.json")["exchange"]["route_to"], "s2.json")


if __name__ == "__main__":
    unittest.main()

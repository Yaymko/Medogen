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


class RouterModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p30, cls.p271 = build(cls.tmp.name, [
            {"name": "d30", "format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]},
            {"name": "d271", "format": "2.7.1", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]}])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        b = self.box.name
        self.out, self.arch, self.inn, self.inc = (os.path.join(b, n) for n in ("OUT", "OUTArchiveGen", "IN", "inc"))
        for d in (self.out, self.inn, self.inc):
            os.makedirs(d)
        self.src = {"out": self.out, "archive": self.arch}

    def tearDown(self):
        self.box.cleanup()

    def run_once(self, settle=5):
        from medogen_core import router
        return router.route_once(self.src, self.inn, self.inc, settle=settle)

    def test_valid_package_goes_to_in(self):
        name = put(self.p30, self.out)
        res = self.run_once()
        self.assertEqual([r["status"] for r in res], ["sent"], res)
        self.assertEqual(os.listdir(self.inn), [name])
        self.assertEqual(os.listdir(self.out), [])
        self.assertTrue(os.path.isdir(res[0]["archived"]))
        self.assertTrue(os.path.isdir(res[0]["copy"]))
        self.assertEqual(res[0]["in_path"], os.path.join(self.inn, name))
        self.assertEqual(res[0]["format"], "3.0")
        self.assertTrue(res[0]["msg_uid"])

    def test_invalid_package_stays(self):
        name = put(self.p30, self.out)
        break_package(os.path.join(self.out, name))
        age(os.path.join(self.out, name), 60)
        res = self.run_once()
        self.assertEqual(res[0]["status"], "invalid")
        self.assertTrue(res[0]["errors"])
        self.assertEqual(os.listdir(self.inn), [])
        self.assertTrue(os.path.isdir(res[0]["copy"]))
        self.assertIsNone(res[0]["in_path"])

    def test_fresh_package_skipped(self):
        name = put(self.p30, self.out, ago=0)
        folder = os.path.join(self.out, name)
        t = time.time() - 60
        os.utime(os.path.join(folder, "envelope.ini"), (t, t))
        os.utime(folder, (t, t))
        self.assertEqual(self.run_once(), [])
        self.assertEqual(os.listdir(self.out), [name])

    def test_name_taken_in_in(self):
        n1 = put(self.p30, self.out, ago=120)
        n2 = put(self.p271, self.out, ago=60)
        os.makedirs(os.path.join(self.inn, n1))
        res = self.run_once()
        self.assertEqual([(r["name"], r["status"]) for r in res], [(n1, "put_failed"), (n2, "sent")])
        self.assertIn("уже есть пакет", res[0]["errors"][0])

    def test_order_oldest_first(self):
        n_new = put(self.p30, self.out, ago=60)
        n_old = put(self.p271, self.out, ago=120)
        self.assertEqual([r["name"] for r in self.run_once()], [n_old, n_new])

    def test_resend_invalid(self):
        from medogen_core import router
        name = put(self.p30, self.out)
        break_package(os.path.join(self.out, name))
        age(os.path.join(self.out, name), 60)
        copy = self.run_once()[0]["copy"]
        self.assertEqual(router.resend(copy, self.inn, name), os.path.join(self.inn, name))
        with self.assertRaisesRegex(MedoError, "уже есть пакет"):
            router.resend(copy, self.inn, name)

    def test_archive_failure_not_sent_and_not_repeated(self):
        from medogen_core import router
        name = put(self.p30, self.out)
        with open(self.arch, "w") as f:  # OUTArchiveGen не создать: перенос из OUT не удастся
            f.write("x")
        seen = {}
        router.route_once(self.src, self.inn, self.inc, settle=5, seen=seen)  # первый взгляд на пакет
        res = router.route_once(self.src, self.inn, self.inc, settle=5, seen=seen)
        self.assertEqual(res[0]["status"], "take_failed", res)
        self.assertIn("не перенесён", res[0]["errors"][0])
        self.assertEqual(os.listdir(self.inn), [])
        self.assertEqual(os.listdir(self.out), [name])
        self.assertEqual(router.route_once(self.src, self.inn, self.inc, settle=5, seen=seen), [])
        self.assertEqual(len(os.listdir(self.inc)), 1)

    def test_snapshot_settle_ignores_clock(self):
        """С seen пакет берётся, только когда он не изменился с прошлого прохода (часы сервера не важны)."""
        from medogen_core import router
        name = put(self.p30, self.out, ago=3600)
        seen = {}
        self.assertEqual(router.route_once(self.src, self.inn, self.inc, settle=5, seen=seen), [])
        env = os.path.join(self.out, name, "envelope.ini")
        with open(env, "ab") as f:
            f.write(b"\r\n")  # СЭД ещё пишет пакет
        age(os.path.join(self.out, name), 3600)
        self.assertEqual(router.route_once(self.src, self.inn, self.inc, settle=5, seen=seen), [])
        res = router.route_once(self.src, self.inn, self.inc, settle=5, seen=seen)
        self.assertEqual([r["status"] for r in res], ["sent"])

    def test_out_unreachable(self):
        self.src["out"] = os.path.join(self.box.name, "no-such", "OUT")
        with self.assertRaisesRegex(MedoError, "нет доступа"):
            self.run_once()


class TakeCleanup(unittest.TestCase):
    def test_take_removes_partial_copy(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as b:
            out, dest = os.path.join(b, "OUT"), os.path.join(b, "dest")
            os.makedirs(os.path.join(out, "p"))
            open(os.path.join(out, "p", "envelope.ini"), "w").close()

            def partial(src, dst, *a, **k):
                os.makedirs(dst)
                raise OSError("файл занят")
            with mock.patch("shutil.copytree", partial):
                with self.assertRaisesRegex(MedoError, "не удалось скопировать"):
                    exchange.take(out, os.path.join(b, "arch"), "p", dest)
            self.assertFalse(os.path.exists(dest))


class AppRouting(unittest.TestCase):
    """Два стенда s1 и s2 со своими папками и маршрутами друг на друга."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        built = build(cls.tmp.name, [
            {"name": "doc", "format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]},
            {"name": "receipt_ok", "format": "3.0", "type": "receipt", "reply_to": "@doc",
             "receipt": {"accepted": True}},
            {"name": "registered", "format": "3.0", "type": "notification", "reply_to": "@doc",
             "notification": {"kind": "registered", "number": "ВХ-1", "date": "today"}},
            {"name": "answer", "format": "3.0", "type": "document", "reply_to": "@doc",
             "from": "mo_duma", "to": ["pravit_mo"]}])
        cls.doc, cls.replies = built[0], built[1:]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from medogen_core import webui
        self.box = tempfile.TemporaryDirectory()
        b = self.box.name
        self.root = os.path.join(b, "root")
        os.makedirs(os.path.join(self.root, "orgs"))
        self.dirs = {}
        for s, other in (("s1", "s2.json"), ("s2", "s1.json")):
            shutil.copy(DEFAULT_ORGS, os.path.join(self.root, "orgs", s + ".json"))
            d = {k: os.path.join(b, s, k.upper()) for k in ("out", "in")}
            for p in d.values():
                os.makedirs(p)
            self.dirs[s] = d
        self.app = webui.App(self.root)
        self.app.route_settle = 0
        for s, other in (("s1", "s2.json"), ("s2", "s1.json")):
            self.save(s, route_to=other)

    def save(self, s, **kw):
        ex = {"out": self.dirs[s]["out"], "in": self.dirs[s]["in"], "route_to": "", "route_auto": True}
        ex.update(kw)
        self.app.exchange_save({"stand": s + ".json", "exchange": ex})

    def tearDown(self):
        self.box.cleanup()

    def test_route_sends_and_logs(self):
        name = put(self.doc, self.dirs["s1"]["out"])
        res = self.app.route("s1.json")["results"]
        self.assertEqual([r["status"] for r in res], ["sent"])
        self.assertEqual(os.listdir(self.dirs["s2"]["in"]), [name])
        item = self.app.route_journal()["items"][0]
        self.assertEqual((item["status"], item["from"], item["to"]), ("sent", "s1.json", "s2.json"))
        self.assertTrue(item["id"].startswith("_incoming/"))
        card = next(c for c in self.app.packages() if c["id"] == item["id"])
        self.assertEqual(card["taken_from"]["stand_file"], "s1.json")
        self.assertEqual(card["taken_from"]["route"]["status"], "sent")
        st = next(r for r in self.app.routes()["routes"] if r["from"] == "s1.json")["state"]
        self.assertEqual(st["sent"], 1)
        self.assertTrue(st["last_run"])

    def test_return_path(self):
        doc = put(self.doc, self.dirs["s1"]["out"])
        answers = sorted(put(p, self.dirs["s2"]["out"]) for p in self.replies)
        self.app.route_all_auto()
        self.assertEqual(os.listdir(self.dirs["s2"]["in"]), [doc])
        self.assertEqual(sorted(os.listdir(self.dirs["s1"]["in"])), answers)
        self.assertEqual(len(answers), 3)

    def test_config_errors(self):
        cases = [("s1.json", "на самого себя"), ("nope.json", "не найден"), ("s2.json", "не задана папка IN")]
        for target, text in cases:
            with self.subTest(target=target):
                self.save("s2", **{"in": ""}) if target == "s2.json" else self.save("s2")
                self.save("s1", route_to=target)
                r = next(r for r in self.app.routes()["routes"] if r["from"] == "s1.json")
                self.assertIn(text, r["error"] or "")
                with self.assertRaisesRegex(MedoError, text):
                    self.app.route("s1.json")

    def test_unreachable_does_not_stop_others(self):
        self.save("s1", route_to="s2.json", out=os.path.join(self.box.name, "no-such", "OUT"))
        with open(os.path.join(self.root, "orgs", "bad.json"), "w") as f:
            f.write("{broken")
        name = put(self.replies[0], self.dirs["s2"]["out"])
        self.app.route_all_auto()
        st = next(r for r in self.app.routes()["routes"] if r["from"] == "s1.json")["state"]
        self.assertIn("нет доступа", st["last_error"])
        self.assertEqual(os.listdir(self.dirs["s1"]["in"]), [name])

    def test_resend_from_journal(self):
        name = put(self.doc, self.dirs["s1"]["out"])
        break_package(os.path.join(self.dirs["s1"]["out"], name))
        self.app.route("s1.json")
        item = self.app.route_journal()["items"][0]
        self.assertEqual(item["status"], "invalid")
        self.assertEqual(os.listdir(self.dirs["s2"]["in"]), [])
        r = self.app.route_resend({"id": item["id"]})
        self.assertEqual(r["path"], os.path.join(self.dirs["s2"]["in"], name))
        self.assertEqual(self.app.route_journal()["items"][0]["status"], "resent")
        built = self.app.build({"stand": "s1.json", "scenario": {
            "format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]}})["packages"][0]
        with self.assertRaises(MedoError):
            self.app.route_resend({"id": built["id"]})

    def test_broken_target_stand_file(self):
        with open(os.path.join(self.root, "orgs", "s2.json"), "w") as f:
            f.write("{broken")
        r = next(r for r in self.app.routes()["routes"] if r["from"] == "s1.json")
        self.assertIn("не читается", r["error"])
        self.app.route_all_auto()  # не падает

    def test_manual_run_waits_for_settle(self):
        self.app.route_settle = 0.2
        name = put(self.doc, self.dirs["s1"]["out"])
        self.assertEqual(self.app.route("s1.json", wait=False)["results"], [])  # фон: первый взгляд
        res = self.app.route("s1.json")["results"]  # кнопка: ждёт и пересылает
        self.assertEqual([r["status"] for r in res], ["sent"])
        self.assertEqual(os.listdir(self.dirs["s2"]["in"]), [name])

    def test_manual_run_sends_new_package_at_once(self):
        self.app.route_settle = 0.2
        put(self.doc, self.dirs["s1"]["out"])
        self.assertEqual([r["status"] for r in self.app.route("s1.json")["results"]], ["sent"])

    def test_parallel_runs_send_once(self):
        put(self.doc, self.dirs["s1"]["out"])
        ts = [threading.Thread(target=self.app.route, args=("s1.json",)) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(os.listdir(self.dirs["s2"]["in"])), 1)


    def test_background_thread(self):
        from medogen_core import webui
        name = put(self.doc, self.dirs["s1"]["out"])
        stop = webui.start_router(self.app, 0.05)
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not os.listdir(self.dirs["s2"]["in"]):
                time.sleep(0.05)
        finally:
            stop.set()
        self.assertEqual(os.listdir(self.dirs["s2"]["in"]), [name])

    def test_background_thread_survives_exception(self):
        from medogen_core import webui
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("сбой")
        self.app.route_all_auto = boom
        with contextlib.redirect_stderr(io.StringIO()):
            stop = webui.start_router(self.app, 0.02)
            try:
                deadline = time.time() + 5
                while time.time() < deadline and len(calls) < 2:
                    time.sleep(0.02)
            finally:
                stop.set()
                time.sleep(0.05)
        self.assertGreaterEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

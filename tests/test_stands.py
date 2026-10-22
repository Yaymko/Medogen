"""Удаление устаревших стендов: файл стенда уходит в orgs/_deleted (его можно вернуть), а не стирается."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import webui  # noqa: E402
from medogen_core.common import MedoError  # noqa: E402


class DeleteStand(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.root = self.box.name
        os.makedirs(os.path.join(self.root, "orgs"))
        shutil.copy(os.path.join(ROOT, "orgs", "default.json"), os.path.join(self.root, "orgs", "default.json"))
        self.app = webui.App(self.root)
        self.app.new_stand({"file": "old_stand", "stand": "Старый стенд"})

    def tearDown(self):
        self.box.cleanup()

    def stands(self):
        return [s["file"] for s in self.app.meta()["stands"]]

    def test_delete_moves_stand_to_trash(self):
        self.assertIn("old_stand.json", self.stands())
        r = self.app.delete_stand({"stand": "old_stand.json"})
        self.assertNotIn("old_stand.json", self.stands())
        self.assertTrue(os.path.isfile(r["moved_to"]))
        self.assertEqual(os.path.dirname(r["moved_to"]), os.path.join(self.root, "orgs", "_deleted"))

    def test_delete_twice_keeps_both_copies(self):
        self.app.delete_stand({"stand": "old_stand.json"})
        self.app.new_stand({"file": "old_stand", "stand": "Старый стенд 2"})
        self.app.delete_stand({"stand": "old_stand.json"})
        self.assertEqual(len(os.listdir(os.path.join(self.root, "orgs", "_deleted"))), 2)

    def test_default_and_missing_stand_are_refused(self):
        for bad in ("default.json", "nope.json"):
            with self.subTest(stand=bad), self.assertRaises(MedoError):
                self.app.delete_stand({"stand": bad})
        self.assertIn("default.json", self.stands())

    def test_path_in_name_stays_inside_orgs(self):
        with self.assertRaises(MedoError):
            self.app.delete_stand({"stand": "../orgs/nope.json"})


if __name__ == "__main__":
    unittest.main()

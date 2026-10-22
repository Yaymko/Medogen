"""Связи между пакетами: ответ (квитанция, уведомление, ответный документ) помнит основной пакет,
а в истории у основного пакета виден список ответов."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import webui  # noqa: E402


class PackageLinks(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.root = self.box.name
        os.makedirs(os.path.join(self.root, "orgs"))
        shutil.copy(os.path.join(ROOT, "orgs", "default.json"), os.path.join(self.root, "orgs", "default.json"))
        self.app = webui.App(self.root)

    def tearDown(self):
        self.box.cleanup()

    def build(self, scenario):
        r = self.app.build({"stand": "default.json", "scenario": scenario})
        self.assertIsNone(r["error"], r.get("log"))
        return r["packages"]

    def main_doc(self):
        return self.build({"format": "2.7.1", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]})[0]

    def cards(self):
        return {c["id"]: c for c in self.app.packages()}

    def test_reply_remembers_main_package(self):
        doc = self.main_doc()
        replies = self.build({"packages": [
            {"type": "receipt", "reply_to": "ui:" + doc["id"], "receipt": {"accepted": True}},
            {"type": "notification", "reply_to": "ui:" + doc["id"], "notification": {"kind": "registered"}},
        ]})
        for r in replies:
            self.assertEqual(r["parent"]["msg_uid"], doc["msg_uid"])
            self.assertEqual(r["parent"]["kind"], "reply_to")
        cards = self.cards()
        self.assertEqual(sorted(cards[doc["id"]]["children"]), sorted(r["id"] for r in replies))
        for r in replies:
            self.assertEqual(cards[r["id"]]["parent_id"], doc["id"])

    def test_package_without_base_has_no_links(self):
        doc = self.main_doc()
        card = self.cards()[doc["id"]]
        self.assertNotIn("parent", doc)
        self.assertIsNone(card.get("parent_id"))
        self.assertEqual(card.get("children", []), [])

    def test_old_receipt_linked_by_message_uid(self):
        # квитанции, собранные до появления связей, находят основной пакет по GUID сообщения
        doc = self.main_doc()
        rc = self.build({"type": "receipt", "reply_to": "ui:" + doc["id"], "receipt": {"accepted": True}})[0]
        mpath = os.path.join(os.path.dirname(rc["folder"]), "_manifests", os.path.basename(rc["folder"]) + ".json")
        with open(mpath, encoding="utf-8") as f:
            man = json.load(f)
        man.pop("parent")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(man, f)
        cards = self.cards()
        self.assertEqual(cards[rc["id"]]["parent_id"], doc["id"])
        self.assertEqual(cards[doc["id"]]["children"], [rc["id"]])

    def test_reply_to_deleted_package_keeps_parent_info(self):
        doc = self.main_doc()
        rc = self.build({"type": "receipt", "reply_to": "ui:" + doc["id"], "receipt": {"accepted": True}})[0]
        self.app.delete_package({"id": doc["id"]})
        card = self.cards()[rc["id"]]
        self.assertIsNone(card.get("parent_id"))
        self.assertEqual(card["parent"]["msg_uid"], doc["msg_uid"])


if __name__ == "__main__":
    unittest.main()

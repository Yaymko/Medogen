"""HTTP API /api/v1 (docs/superpowers/specs/2026-10-29-http-api-design.md, docs/API.md).

Запросы идут в Api.handle напрямую, отдельные проверки — через настоящий HTTP-сервер."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import api, webui  # noqa: E402

DEFAULT_ORGS = os.path.join(ROOT, "orgs", "default.json")
DOC30 = {"format": "3.0", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]}


def q(pid):
    return urllib.parse.quote(pid, safe="")


class ApiCase(unittest.TestCase):
    token = None

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        b = self.box.name
        self.root = os.path.join(b, "root")
        os.makedirs(os.path.join(self.root, "orgs"))
        shutil.copy(DEFAULT_ORGS, os.path.join(self.root, "orgs", "default.json"))
        shutil.copytree(os.path.join(ROOT, "examples"), os.path.join(self.root, "examples"))
        self.out, self.inn = os.path.join(b, "OUT"), os.path.join(b, "IN")
        for d in (self.out, self.inn):
            os.makedirs(d)
        self.app = webui.App(self.root)
        self.app.exchange_save({"stand": "default.json", "exchange": {"out": self.out, "in": self.inn}})
        self.api = api.Api(self.app, token=self.token)

    def tearDown(self):
        self.box.cleanup()

    def call(self, method, path, body=None, headers=None, raw=False):
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
        status, payload, ctype, _h = self.api.handle(method, path, headers or {}, data or b"")
        if raw:
            return status, payload, ctype
        return status, json.loads(payload.decode("utf-8")) if payload else None

    def build(self, scenario=None, **extra):
        status, res = self.call("POST", "/api/v1/packages", {"stand": "default.json",
                                                             "scenario": scenario or dict(DOC30), **extra})
        self.assertEqual(status, 201, res)
        return res


class Reference(ApiCase):
    def test_health(self):
        status, res = self.call("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(res["api"], "v1")
        self.assertIn("2.7.1", res["formats"])

    def test_meta_and_examples(self):
        self.assertIn("receipt", self.call("GET", "/api/v1/meta")[1]["types"])
        files = [e["file"] for e in self.call("GET", "/api/v1/examples")[1]["items"]]
        self.assertIn("30_document.json", files)

    def test_stands(self):
        status, res = self.call("GET", "/api/v1/stands")
        self.assertEqual([s["file"] for s in res["items"]], ["default.json"])
        status, res = self.call("GET", "/api/v1/stands/default")
        self.assertEqual(status, 200)
        self.assertIn("pravit_mo", [o["key"] for o in res["orgs"]])
        self.assertEqual(res["exchange"]["in"], self.inn)

    def test_unknown_stand_is_404(self):
        self.assertEqual(self.call("GET", "/api/v1/stands/nope")[0], 404)

    def test_check_stand_folders(self):
        status, res = self.call("POST", "/api/v1/stands/default.json/check")
        self.assertEqual(status, 200)
        self.assertTrue(res["in"]["ok"], res)

    def test_gas(self):
        status, res = self.call("GET", "/api/v1/gas?q=" + q("Тюмень"))
        self.assertEqual(status, 200)
        self.assertIn("items", res)

    def test_unknown_address_and_method(self):
        self.assertEqual(self.call("GET", "/api/v1/nothing")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/v1/health")[0], 405)

    def test_bad_json_is_400(self):
        self.assertEqual(self.call("POST", "/api/v1/packages", b"{not json")[0], 400)


class Packages(ApiCase):
    def test_build_scenario(self):
        res = self.build()
        self.assertEqual(len(res["packages"]), 1)
        pkg = res["packages"][0]
        self.assertEqual(pkg["errors"], [])
        status, card = self.call("GET", "/api/v1/packages/" + q(pkg["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(card["msg_uid"], pkg["msg_uid"])
        ids = [c["id"] for c in self.call("GET", "/api/v1/packages")[1]["items"]]
        self.assertIn(pkg["id"], ids)

    def test_build_example(self):
        status, res = self.call("POST", "/api/v1/packages", {"stand": "default.json", "example": "30_document.json"})
        self.assertEqual(status, 201, res)
        self.assertEqual(len(res["packages"]), 2)

    def test_build_error_is_422(self):
        status, res = self.call("POST", "/api/v1/packages", {"stand": "default.json",
                                                             "scenario": {**DOC30, "from": "no_such_org"}})
        self.assertEqual(status, 422)
        self.assertIn("no_such_org", res["error"])

    def test_build_needs_scenario_or_example(self):
        self.assertEqual(self.call("POST", "/api/v1/packages", {"stand": "default.json"})[0], 400)

    def test_filter_list(self):
        self.build()
        self.build({**DOC30, "format": "2.7.1"})
        items = self.call("GET", "/api/v1/packages?format=2.7.1")[1]["items"]
        self.assertEqual([c["format"] for c in items], ["2.7.1"])
        self.assertEqual(len(self.call("GET", "/api/v1/packages?limit=1")[1]["items"]), 1)

    def test_files_and_archive(self):
        pid = self.build()["packages"][0]["id"]
        status, data, ctype = self.call("GET", f"/api/v1/packages/{q(pid)}/files/envelope.ini", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"[", data)
        status, data, ctype = self.call("GET", f"/api/v1/packages/{q(pid)}/archive", raw=True)
        self.assertEqual(ctype, "application/zip")
        self.assertIn("envelope.ini", zipfile.ZipFile(io.BytesIO(data)).namelist())

    def test_file_inside_container(self):
        pkg = self.build()["packages"][0]
        arc = next(f for f in pkg["files"] if f.get("entries"))
        entry = arc["entries"][0]["name"]
        status, data, _ = self.call(
            "GET", f"/api/v1/packages/{q(pkg['id'])}/files/{q(arc['name'])}?entry={q(entry)}", raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(len(data), arc["entries"][0]["size"])

    def test_validate_and_delete(self):
        pid = self.build()["packages"][0]["id"]
        status, res = self.call("POST", f"/api/v1/packages/{q(pid)}/validate")
        self.assertEqual((status, res["ok"], res["errors"]), (200, True, []))
        self.assertEqual(self.call("DELETE", "/api/v1/packages/" + q(pid))[0], 200)
        self.assertEqual(self.call("GET", "/api/v1/packages/" + q(pid))[0], 404)

    def test_missing_package_is_404(self):
        self.assertEqual(self.call("GET", "/api/v1/packages/nope")[0], 404)

    def test_path_escape_is_rejected(self):
        self.assertEqual(self.call("GET", "/api/v1/packages/" + q("../../orgs"))[0], 400)


class Exchange(ApiCase):
    def test_put_then_take_then_reply(self):
        pid = self.build()["packages"][0]["id"]
        status, res = self.call("POST", f"/api/v1/packages/{q(pid)}/put", {"stand": "default.json", "to": "out"})
        self.assertEqual(status, 200, res)
        self.assertTrue(res["ok"], res)
        name = os.path.basename(res["path"])
        self.assertTrue(os.path.isdir(os.path.join(self.out, name)))

        status, res = self.call("GET", "/api/v1/stands/default.json/out")
        self.assertEqual([i["name"] for i in res["items"]], [name])

        status, res = self.call("POST", "/api/v1/stands/default.json/out/take", {"name": name})
        self.assertEqual(status, 201, res)
        inc = res["package"]["id"]
        self.assertTrue(inc.startswith("_incoming/"))

        reply = self.build({"type": "receipt", "reply_to": "ui:" + inc})["packages"][0]
        self.assertEqual(reply["errors"], [])
        self.assertEqual(reply["type"], "receipt")

    def test_put_failure_is_409(self):
        pid = self.build()["packages"][0]["id"]
        self.call("POST", f"/api/v1/packages/{q(pid)}/put", {"stand": "default.json", "to": "in"})
        status, res = self.call("POST", f"/api/v1/packages/{q(pid)}/put", {"stand": "default.json", "to": "in"})
        self.assertEqual(status, 409)
        self.assertIn("уже есть", res["error"])

    def test_build_with_put(self):
        res = self.build(put="in")
        self.assertTrue(res["put"][0]["ok"], res["put"])
        self.assertEqual(len(os.listdir(self.inn)), 1)


class Upload(ApiCase):
    def zip_of_built(self):
        pid = self.build()["packages"][0]["id"]
        return self.call("GET", f"/api/v1/packages/{q(pid)}/archive", raw=True)[1]

    def test_incoming_zip(self):
        data = self.zip_of_built()
        status, card = self.call("POST", "/api/v1/incoming?name=" + q("из СЭД"), data)
        self.assertEqual(status, 201, card)
        self.assertTrue(card["incoming"])
        # незакодированный id входящего тоже принимается
        self.assertEqual(self.call("GET", "/api/v1/packages/" + card["id"])[0], 200)
        self.assertEqual(self.call("POST", "/api/v1/packages/" + card["id"] + "/validate")[0], 200)

    def test_validate_upload_keeps_nothing(self):
        data = self.zip_of_built()
        before = sorted(os.listdir(self.app.incoming))
        status, res = self.call("POST", "/api/v1/validate", data)
        self.assertEqual(status, 200, res)
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(os.listdir(self.app.incoming)), before)

    def test_validate_reports_errors(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("envelope.ini", "[ПИСЬМО КП]\r\nФАЙЛ=message.xml\r\n".encode("cp1251"))
        status, res = self.call("POST", "/api/v1/validate", buf.getvalue())
        self.assertEqual(status, 200, res)
        self.assertFalse(res["ok"])
        self.assertTrue(res["errors"])

    def test_not_a_zip_is_400(self):
        self.assertEqual(self.call("POST", "/api/v1/validate", b"hello")[0], 400)
        self.assertEqual(self.call("POST", "/api/v1/incoming", b"")[0], 400)


class Routes(ApiCase):
    def test_routes_and_journal(self):
        self.assertEqual(self.call("GET", "/api/v1/routes")[1], {"items": []})
        self.assertEqual(self.call("GET", "/api/v1/routes/journal?limit=5")[1], {"items": []})

    def test_run_without_route_is_400(self):
        status, res = self.call("POST", "/api/v1/routes/default.json/run", {})
        self.assertEqual(status, 400)
        self.assertIn("маршрут не задан", res["error"])


class Token(ApiCase):
    token = "s3cret"

    def test_token_required(self):
        self.assertEqual(self.call("GET", "/api/v1/health")[0], 401)
        self.assertEqual(self.call("GET", "/api/v1/health", headers={"Authorization": "Bearer bad"})[0], 401)
        self.assertEqual(self.call("GET", "/api/v1/health", headers={"Authorization": "Bearer s3cret"})[0], 200)
        self.assertEqual(self.call("GET", "/api/v1/health", headers={"X-Api-Key": "s3cret"})[0], 200)

    def test_openapi_is_public(self):
        self.assertEqual(self.call("GET", "/api/v1/openapi.json")[0], 200)


class Spec(ApiCase):
    def test_openapi_matches_routes(self):
        status, spec = self.call("GET", "/api/v1/openapi.json")
        self.assertEqual(spec["openapi"][:2], "3.")
        documented = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops if m != "parameters"}
        routed = {(m, "/api/v1" + t) for m, t, _fn in api.ROUTES}
        self.assertEqual(documented, routed)

    def test_every_operation_has_summary_and_responses(self):
        spec = self.call("GET", "/api/v1/openapi.json")[1]
        for p, ops in spec["paths"].items():
            for m, op in ops.items():
                if m == "parameters":
                    continue
                self.assertTrue(op.get("summary"), (m, p))
                self.assertTrue(op.get("responses"), (m, p))


class Http(ApiCase):
    def serve(self, handler):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def get(self, url, method="GET", data=None):
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_ui_server_serves_api(self):
        base = self.serve(webui.make_handler(self.app))
        status, data = self.get(base + "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["api"], "v1")
        self.assertEqual(self.get(base + "/api/v1/packages/x", "DELETE")[0], 404)

    def test_api_only_server(self):
        base = self.serve(webui.make_handler(self.app, api_token="t", ui=False))
        self.assertEqual(self.get(base + "/")[0], 404)
        self.assertEqual(self.get(base + "/api/meta")[0], 404)
        self.assertEqual(self.get(base + "/api/v1/health")[0], 401)
        self.assertEqual(self.get(base + "/api/v1/openapi.json")[0], 200)


if __name__ == "__main__":
    unittest.main()

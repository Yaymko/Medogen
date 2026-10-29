"""HTTP API генератора: /api/v1 (описание — docs/API.md и openapi.json рядом с модулем).

Тонкий слой REST поверх webui.App: та же логика, что у интерфейса, но адреса и ответы
стабильны. Таблица ROUTES — единственный список адресов; тест сверяет её с openapi.json.
Запускается вместе с интерфейсом (python medogen.py ui) или отдельно (python medogen.py api).
"""
import hmac
import io
import json
import mimetypes
import os
import tempfile
import urllib.parse
import zipfile

from . import gas, validate
from .common import MedoError

PREFIX = "/api/v1"
SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openapi.json")
FORMATS = ["3.0", "2.7.1", "2.7", "2.6"]
PUBLIC = {"/openapi.json"}  # без ключа: описание API


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Unprocessable(Exception):
    def __init__(self, payload):
        super().__init__(payload.get("error"))
        self.payload = payload


# (метод, шаблон адреса после /api/v1, метод Api). Более длинные шаблоны с {id} — раньше короткого:
# незакодированный id входящего пакета (_incoming/…) иначе «съел» бы хвост адреса.
ROUTES = [
    ("GET", "/health", "health"),
    ("GET", "/meta", "meta"),
    ("GET", "/examples", "examples"),
    ("GET", "/openapi.json", "openapi"),
    ("GET", "/stands", "stands"),
    ("GET", "/stands/{stand}", "stand"),
    ("POST", "/stands/{stand}/check", "stand_check"),
    ("GET", "/stands/{stand}/out", "stand_out"),
    ("POST", "/stands/{stand}/out/take", "stand_take"),
    ("GET", "/gas", "gas"),
    ("GET", "/packages", "packages"),
    ("POST", "/packages", "build"),
    ("GET", "/packages/{id}/archive", "package_archive"),
    ("GET", "/packages/{id}/files/{name}", "package_file"),
    ("POST", "/packages/{id}/validate", "package_validate"),
    ("POST", "/packages/{id}/put", "package_put"),
    ("GET", "/packages/{id}", "package"),
    ("DELETE", "/packages/{id}", "package_delete"),
    ("POST", "/incoming", "incoming"),
    ("POST", "/validate", "validate_upload"),
    ("GET", "/routes", "routes"),
    ("GET", "/routes/journal", "routes_journal"),
    ("POST", "/routes/{stand}/run", "route_run"),
]


def _split(path):
    return [urllib.parse.unquote(s) for s in path.split("/") if s]


_TEMPLATES = [(m, _split(t), fn) for m, t, fn in ROUTES]


def _match(tpl, segs):
    """Параметры адреса по шаблону или None. {id} — один (закодированный) сегмент или
    незакодированный «_incoming/…» до следующего слова шаблона; {name} — остаток адреса."""
    params, i = {}, 0
    for j, t in enumerate(tpl):
        if i >= len(segs):
            return None
        if t == "{id}" and segs[i] == "_incoming":
            nxt = tpl[j + 1] if j + 1 < len(tpl) else None
            k = i + 1
            while k < len(segs) and segs[k] != nxt:
                k += 1
            if k == i + 1:
                return None
            params["id"], i = "/".join(segs[i:k]), k
        elif t == "{name}":
            params["name"], i = "/".join(segs[i:]), len(segs)
        elif t.startswith("{"):
            params[t[1:-1]], i = segs[i], i + 1
        elif segs[i] != t:
            return None
        else:
            i += 1
    return params if i == len(segs) else None


def _header(headers, name):
    v = headers.get(name)
    if v is None:
        v = next((v for k, v in headers.items() if k.lower() == name.lower()), None)
    return v


class Api:
    def __init__(self, app, token=None):
        self.app = app
        self.token = token or None

    # --- разбор запроса -----------------------------------------------------------------------

    def handle(self, method, path, headers, body):
        """→ (код, тело bytes, Content-Type, доп. заголовки)."""
        url = urllib.parse.urlparse(path)
        rel = url.path[len(PREFIX):] if url.path.startswith(PREFIX) else url.path
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            if self.token and rel.rstrip("/") not in PUBLIC and not self._authorized(headers):
                return self._json(401, {"error": "нужен ключ API: Authorization: Bearer <ключ> или X-Api-Key"},
                                  {"WWW-Authenticate": "Bearer"})
            segs, allowed = [s for s in rel.split("/") if s], set()
            segs = [urllib.parse.unquote(s) for s in segs]
            for m, tpl, fn in _TEMPLATES:
                params = _match(tpl, segs)
                if params is None:
                    continue
                if m != method:
                    allowed.add(m)
                    continue
                req = {"params": params, "query": query, "headers": headers, "body": body or b""}
                res = getattr(self, fn)(req)
                return res if isinstance(res[1], (bytes, bytearray)) else self._json(*res)
            if allowed:
                return self._json(405, {"error": f"метод {method} не поддерживается, можно: {', '.join(sorted(allowed))}"},
                                  {"Allow": ", ".join(sorted(allowed))})
            return self._json(404, {"error": "нет такого адреса API"})
        except NotFound as e:
            return self._json(404, {"error": str(e)})
        except Conflict as e:
            return self._json(409, {"error": str(e)})
        except Unprocessable as e:
            return self._json(422, e.payload)
        except MedoError as e:
            return self._json(400, {"error": str(e)})
        except (FileNotFoundError, KeyError) as e:
            return self._json(404, {"error": f"не найдено: {e}"})

    def _authorized(self, headers):
        auth = _header(headers, "Authorization") or ""
        given = auth[7:].strip() if auth.lower().startswith("bearer ") else (_header(headers, "X-Api-Key") or "")
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))

    @staticmethod
    def _json(code, data, headers=None):
        return code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", \
            headers or {}

    @staticmethod
    def _file(name, data, ctype=None):
        ctype = ctype or mimetypes.guess_type(name)[0] or "application/octet-stream"
        return 200, data, ctype, {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)}

    @staticmethod
    def _body(req):
        raw = req["body"]
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise MedoError("тело запроса — не JSON")
        if not isinstance(data, dict):
            raise MedoError("тело запроса — JSON-объект {…}")
        return data

    @staticmethod
    def _zip(req):
        data = req["body"]
        if not data or not zipfile.is_zipfile(io.BytesIO(data)):
            raise MedoError("тело запроса — zip-архив пакета (Content-Type: application/zip)")
        return data

    def _stand(self, stand):
        path = self.app.stand_path(stand)
        if not os.path.isfile(path):
            raise NotFound(f"стенд {os.path.basename(path)} не найден")
        return os.path.basename(path)

    def _pkg(self, pid):
        """Папка пакета по id; служебные папки out/ui (_uploads, _manifests…) пакетами не считаются."""
        folder = self.app.pkg_path(pid)
        parts = self.app.pkg_id(folder).split("/")
        service = parts[0].startswith("_") and (parts[0] != "_incoming" or len(parts) < 2 or parts[1].startswith("_"))
        if service or not os.path.isdir(folder):
            raise NotFound(f"пакет {pid} не найден")
        return folder

    # --- справочники --------------------------------------------------------------------------

    def health(self, req):
        return 200, {"service": "medogen", "api": "v1", "xsd": validate.has_xsd(), "formats": FORMATS,
                     "auth": bool(self.token)}

    def meta(self, req):
        return 200, self.app.meta()

    def examples(self, req):
        return 200, {"items": self.app.meta()["examples"]}

    def openapi(self, req):
        with open(SPEC, "rb") as f:
            return 200, f.read(), "application/json; charset=utf-8", {}

    def stands(self, req):
        return 200, {"items": self.app.meta()["stands"]}

    def stand(self, req):
        return 200, self.app.orgs(self._stand(req["params"]["stand"]))

    def stand_check(self, req):
        return 200, self.app.exchange_check({"stand": self._stand(req["params"]["stand"])})

    def stand_out(self, req):
        return 200, self.app.exchange_out(self._stand(req["params"]["stand"]))

    def stand_take(self, req):
        body = self._body(req)
        if not body.get("name"):
            raise MedoError("укажите name — имя папки пакета в OUT")
        return 201, self.app.exchange_take({"stand": self._stand(req["params"]["stand"]), "name": body["name"]})

    def gas(self, req):
        q = req["query"]
        try:
            limit = max(1, min(int(q.get("limit") or 60), 1000))
        except ValueError:
            raise MedoError("limit — число")
        data = gas.load()
        found, n = gas.search(q.get("q", ""), limit=limit)
        return 200, {"items": found, "found": n, "total": len(data["orgs"]), "updated": data.get("updated")}

    # --- пакеты -------------------------------------------------------------------------------

    def packages(self, req):
        q = req["query"]
        try:
            limit = max(1, min(int(q.get("limit") or 100), 300))
        except ValueError:
            raise MedoError("limit — число")
        items = self.app.packages()
        if q.get("type"):
            items = [c for c in items if c.get("type") == q["type"]]
        if q.get("format"):
            items = [c for c in items if c.get("format") == q["format"]]
        if q.get("incoming") in ("true", "1", "false", "0"):
            want = q["incoming"] in ("true", "1")
            items = [c for c in items if c["incoming"] == want]
        return 200, {"items": items[:limit]}

    def build(self, req):
        body = self._body(req)
        if not body.get("scenario") and not body.get("example"):
            raise MedoError("укажите scenario (объект сценария) или example (имя файла из /examples)")
        if body.get("scenario") is not None and not isinstance(body["scenario"], dict):
            raise MedoError("scenario — JSON-объект сценария")
        to = body.get("put")
        stand = self._stand(body.get("stand") or "default.json")
        res = self.app.build({"stand": stand, "scenario": body.get("scenario"), "example": body.get("example")})
        if res["error"]:
            raise Unprocessable(res)
        if to:
            res["put"] = self.app.exchange_put({"stand": stand, "to": to,
                                                "ids": [c["id"] for c in res["packages"]]})["results"]
        return 201, res

    def package(self, req):
        return 200, self.app.package_card(self._pkg(req["params"]["id"]))

    def package_delete(self, req):
        pid = req["params"]["id"]
        self._pkg(pid)
        return 200, self.app.delete_package({"id": pid})

    def package_archive(self, req):
        pid = req["params"]["id"]
        self._pkg(pid)
        name, data = self.app.zip_package(pid)
        return self._file(name, data, "application/zip")

    def package_file(self, req):
        pid = req["params"]["id"]
        self._pkg(pid)
        name, data = self.app.read_file(pid, req["params"]["name"], req["query"].get("entry"))
        return self._file(name, data)

    def package_validate(self, req):
        warns = []
        errs = validate.validate_folder(self._pkg(req["params"]["id"]), warns)
        return 200, {"ok": not errs, "errors": errs, "warnings": warns, "xsd": validate.has_xsd()}

    def package_put(self, req):
        pid = req["params"]["id"]
        self._pkg(pid)
        body = self._body(req)
        item = self.app.exchange_put({"stand": self._stand(body.get("stand") or "default.json"),
                                      "to": body.get("to") or "in", "ids": [pid]})["results"][0]
        if not item["ok"]:
            raise Conflict(item["error"])
        return 200, item

    # --- загрузка -----------------------------------------------------------------------------

    def incoming(self, req):
        ctype = (_header(req["headers"], "Content-Type") or "").lower()
        if ctype.startswith("application/json"):
            body = self._body(req)
            if not body.get("path"):
                raise MedoError("укажите path — папку пакета на компьютере генератора")
            return 201, self.app.incoming_package({"path": body["path"]})
        data = self._zip(req)
        name = req["query"].get("name") or urllib.parse.unquote(_header(req["headers"], "X-Filename") or "") \
            or "пакет"
        if not name.lower().endswith(".zip"):
            name += ".zip"
        up = self.app.upload(name, data)
        return 201, self.app.incoming_package({"upload": up["path"]})

    def validate_upload(self, req):
        from .webui import App, extract_zip
        data = self._zip(req)
        with tempfile.TemporaryDirectory(prefix="medogen-validate-") as tmp:
            extract_zip(io.BytesIO(data), tmp)
            folder = App._find_package_root(tmp)
            warns = []
            errs = validate.validate_folder(folder, warns)
            errs = [e.replace(tmp + os.sep, "") for e in errs]
            warns = [w.replace(tmp + os.sep, "") for w in warns]
        return 200, {"ok": not errs, "errors": errs, "warnings": warns, "xsd": validate.has_xsd()}

    # --- маршрутизация ------------------------------------------------------------------------

    def routes(self, req):
        return 200, {"items": self.app.routes()["routes"]}

    def routes_journal(self, req):
        try:
            limit = max(1, min(int(req["query"].get("limit") or 200), 5000))
        except ValueError:
            raise MedoError("limit — число")
        return 200, self.app.route_journal(limit)

    def route_run(self, req):
        body = self._body(req)
        return 200, self.app.route(self._stand(req["params"]["stand"]), names=body.get("names"))

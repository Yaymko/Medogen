"""Веб-интерфейс для тестировщиков: python medogen.py ui.

Встроенный http.server, без сторонних пакетов. Страница — medogen_core/ui/index.html,
пакеты собираются в out/ui (по папке на пакет), входящие пакеты для ответов
распаковываются в out/ui/_incoming.
"""
import contextlib
import datetime as dt
import glob
import io
import json
import mimetypes
import os
import re
import shutil
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import builder, digital, exchange, gas, importer, v271, validate
from .common import (CLASSES, KINDS, PLACES, RECEIPT_REASONS, REFUSAL_REASONS, WARNINGS,
                     Directory, MedoError, load_json, safe_dir_name)
from .reply import read_package

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
LOCK = threading.Lock()
EXCHANGE_LOCK = threading.Lock()  # операции с сетевыми папками СЭД не держат LOCK сборки

TYPE_TITLES = {
    "document": "Исходящий документ",
    "gosedo": "ТК ГосЭДО",
    "citizen_appeal": "Обращение гражданина",
    "receipt": "Квитанция",
    "notification": "Уведомление",
    "assignment": "Цифровое поручение",
}
EVENT_TITLES = {k: v[2] for k, v in digital.EVENTS.items()}
NOTIFICATION_TITLES = {k: v[0] for k, v in v271.NOTIFICATION_TYPES.items()}


class App:
    def __init__(self, root):
        self.root = root
        self.orgs_dir = os.path.join(root, "orgs")
        self.examples_dir = os.path.join(root, "examples")
        self.work = os.path.join(root, "out", "ui")
        self.incoming = os.path.join(self.work, "_incoming")
        self.uploads = os.path.join(self.work, "_uploads")
        self.scenarios = os.path.join(self.work, "_scenarios")
        for d in (self.work, self.incoming, self.uploads, self.scenarios):
            os.makedirs(d, exist_ok=True)

    # --- пути -------------------------------------------------------------------

    def inside(self, path, base=None):
        base = os.path.realpath(base or self.work)
        p = os.path.realpath(path)
        if p != base and not p.startswith(base + os.sep):
            raise MedoError("путь вне рабочей папки интерфейса")
        return p

    def pkg_path(self, pid):
        """pid — путь пакета относительно out/ui (например 20261021-120000_01_... или _incoming/x)."""
        if not pid:
            raise MedoError("не указан пакет")
        return self.inside(os.path.join(self.work, pid))

    def pkg_id(self, folder):
        return os.path.relpath(folder, self.work).replace(os.sep, "/")

    def stand_path(self, stand):
        name = os.path.basename(stand or "default.json")
        if not name.endswith(".json"):
            name += ".json"
        return os.path.join(self.orgs_dir, name)

    # --- справочники ------------------------------------------------------------

    def meta(self):
        stands = []
        for p in sorted(glob.glob(os.path.join(self.orgs_dir, "*.json"))):
            try:
                data = load_json(p)
                stands.append({"file": os.path.basename(p), "stand": data.get("stand") or "",
                               "count": len(data.get("orgs") or {})})
            except Exception as e:  # битый файл не должен ронять интерфейс
                stands.append({"file": os.path.basename(p), "stand": f"ошибка: {e}", "count": 0})
        examples = []
        for p in sorted(glob.glob(os.path.join(self.examples_dir, "*.json"))):
            try:
                desc = load_json(p).get("description", "")
            except Exception:
                desc = ""
            examples.append({"file": os.path.basename(p), "description": desc})
        return {
            "types": TYPE_TITLES, "kinds": KINDS, "places": PLACES, "classes": CLASSES,
            "receipt_reasons": RECEIPT_REASONS, "refusal_reasons": REFUSAL_REASONS,
            "notifications": NOTIFICATION_TITLES, "events": EVENT_TITLES,
            "stands": stands, "examples": examples, "xsd": validate.has_xsd(),
            "work_dir": self.work,
        }

    def orgs(self, stand):
        path = self.stand_path(stand)
        data = load_json(path) if os.path.isfile(path) else {"orgs": {}}
        return {"file": os.path.basename(path), "stand": data.get("stand", ""),
                "self": data.get("self"), "exchange": data.get("exchange") or {},
                "orgs": [{"key": k, **v} for k, v in (data.get("orgs") or {}).items()]}

    def save_org(self, body):
        path = self.stand_path(body.get("stand"))
        data = load_json(path) if os.path.isfile(path) else {"stand": body.get("stand_title", ""),
                                                             "orgs": {}}
        data.setdefault("orgs", {})
        o = body.get("org") or {}
        key = (o.get("key") or "").strip()
        if not re.match(r"^[\w\-]+$", key):
            raise MedoError("ключ организации: латиница, цифры, _ и -")
        entry = {"guid": o.get("guid", "").strip().lower(), "name": (o.get("name") or "").strip()}
        if o.get("address"):
            entry["address"] = o["address"].strip()
        Directory._make(entry, key)  # проверка GUID и обязательных полей
        old = body.get("old_key")
        if old and old != key:
            data["orgs"].pop(old, None)
            if data.get("self") == old:
                data["self"] = key
        data["orgs"][key] = entry
        if body.get("make_self") or not data.get("self"):
            data["self"] = key
        self._write_json(path, data)
        return self.orgs(os.path.basename(path))

    def delete_org(self, body):
        path = self.stand_path(body.get("stand"))
        data = load_json(path)
        data.get("orgs", {}).pop(body.get("key"), None)
        if data.get("self") == body.get("key"):
            data["self"] = next(iter(data.get("orgs") or {}), None)
        self._write_json(path, data)
        return self.orgs(os.path.basename(path))

    def new_stand(self, body):
        name = safe_dir_name(body.get("file") or "")
        if not name or name == "package":
            raise MedoError("укажите имя файла стенда")
        path = self.stand_path(name)
        if os.path.exists(path):
            raise MedoError(f"стенд {os.path.basename(path)} уже существует")
        data = {"stand": body.get("stand") or name, "self": None, "orgs": {}}
        if body.get("copy_from"):
            src = load_json(self.stand_path(body["copy_from"]))
            data["orgs"], data["self"] = src.get("orgs", {}), src.get("self")
        self._write_json(path, data)
        return {"file": os.path.basename(path)}

    def delete_stand(self, body):
        """Устаревший стенд переносится в orgs/_deleted/<имя>_<время>.json; вернуть — перенести файл обратно."""
        path = self.stand_path(body.get("stand"))
        name = os.path.basename(path)
        if name == "default.json":
            raise MedoError("стенд-пример default.json не удаляется: на нём работают тесты и примеры")
        if not os.path.isfile(path):
            raise MedoError(f"стенд {name} не найден")
        trash = os.path.join(self.orgs_dir, "_deleted")
        os.makedirs(trash, exist_ok=True)
        target = os.path.join(trash, f"{os.path.splitext(name)[0]}_{_stamp()}.json")
        shutil.move(path, target)
        return {"moved_to": target}

    def import_orgs(self, body):
        src = self.inside(body["upload"], self.uploads)
        path = self.stand_path(body.get("file") or "imported")
        n = importer.import_file(src, path, body.get("stand"))
        return {"file": os.path.basename(path), "count": n}

    def gas_search(self, q):
        data = gas.load()
        found, n = gas.search(q, limit=60)
        return {"items": found, "found": n, "total": len(data["orgs"]),
                "updated": data.get("updated"), "source": data.get("source")}

    def gas_import(self, body):
        src = self.inside(body["upload"], self.uploads)
        added, updated, total = gas.import_file(src)
        if not added and not updated:
            raise MedoError("в файле не найдено организаций с GUID — формат страницы мог измениться")
        return {"added": added, "updated": updated, "total": total}

    @staticmethod
    def _write_json(path, data):
        with open(path, "w", encoding="utf-8") as f:
            f.write("{\n")
            f.write(f'  "stand": {json.dumps(data.get("stand", ""), ensure_ascii=False)},\n')
            f.write(f'  "self": {json.dumps(data.get("self"), ensure_ascii=False)},\n')
            if data.get("exchange"):
                f.write(f'  "exchange": {json.dumps(data["exchange"], ensure_ascii=False)},\n')
            f.write('  "orgs": {')
            items = list((data.get("orgs") or {}).items())
            for i, (k, v) in enumerate(items):
                f.write(f'\n    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}')
                f.write("," if i < len(items) - 1 else "\n  ")
            f.write("}\n}\n")

    # --- загрузка файлов ----------------------------------------------------------

    def upload(self, filename, data):
        name = os.path.basename(filename.replace("\\", "/")) or "file"
        folder = os.path.join(self.uploads, uuid.uuid4().hex[:12])
        os.makedirs(folder)
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(data)
        return {"path": path, "name": name, "size": len(data)}

    def incoming_package(self, body):
        """Входящий пакет из СЭД: zip, набор файлов пакета или путь к папке на этом компьютере."""
        uploads = body.get("uploads") or ([body["upload"]] if body.get("upload") else [])
        if uploads:
            srcs = [self.inside(u, self.uploads) for u in uploads]
            if len(srcs) == 1 and zipfile.is_zipfile(srcs[0]) and not srcs[0].lower().endswith(".edc.zip"):
                base = os.path.splitext(os.path.basename(srcs[0]))[0]
            else:
                base = body.get("name") or "пакет"
            dest = os.path.join(self.incoming, safe_dir_name(f"{_stamp()}_{base}"))
            os.makedirs(dest)
            if len(srcs) == 1 and zipfile.is_zipfile(srcs[0]) and not srcs[0].lower().endswith(".edc.zip"):
                with zipfile.ZipFile(srcs[0]) as z:
                    root = os.path.realpath(dest)
                    for m in z.infolist():
                        target = os.path.realpath(os.path.join(dest, m.filename))
                        if target != root and not target.startswith(root + os.sep):
                            raise MedoError(f"zip: недопустимый путь {m.filename}")
                    z.extractall(dest)
            else:  # отдельные файлы пакета: envelope.ini, message.xml, *.edc.zip ...
                for src in srcs:
                    shutil.copy(src, os.path.join(dest, os.path.basename(src)))
            folder = self._find_package_root(dest)
        else:
            path = (body.get("path") or "").strip().strip('"')
            if not os.path.isdir(path):
                raise MedoError(f"папка не найдена: {path}")
            base = os.path.basename(os.path.normpath(path))
            dest = os.path.join(self.incoming, safe_dir_name(f"{_stamp()}_{base}"))
            shutil.copytree(path, dest)
            folder = self._find_package_root(dest)
        return self.package_card(folder)

    @staticmethod
    def _find_package_root(dest):
        for cur, _dirs, files in os.walk(dest):
            names = {f.lower() for f in files}
            if "envelope.ini" in names or "message.xml" in names or "container.xml" in names \
                    or "communication.xml" in names:
                return cur
        raise MedoError("в архиве нет envelope.ini / message.xml")

    # --- пакеты -----------------------------------------------------------------------

    def package_card(self, folder, with_errors=True):
        card = {"id": self.pkg_id(folder), "folder": folder,
                "incoming": self.pkg_id(folder).startswith("_incoming/"),
                "created": dt.datetime.fromtimestamp(os.path.getmtime(folder)).strftime("%d.%m.%Y %H:%M:%S")}
        mpath = os.path.join(os.path.dirname(folder), "_manifests", os.path.basename(folder) + ".json")
        if not os.path.isfile(mpath):
            mpath = os.path.join(folder, "manifest.json")  # пакеты старых версий
        if os.path.isfile(mpath):
            card.update(load_json(mpath))
        else:
            try:
                info = read_package(folder)
                card.update({"format": info.get("format"), "type": "incoming",
                             "name": os.path.basename(folder), "msg_uid": info.get("msg_uid"),
                             "doc_uid": info.get("doc_uid"), "reg_number": info.get("doc_number"),
                             "reg_date": info.get("doc_date"),
                             "from": {"guid": info["source"]["guid"], "name": info["source"]["name"]},
                             "to": info.get("receivers", []),
                             "process_uid": info.get("process_uid"),
                             "items": info.get("items")})
            except Exception as e:
                card.update({"type": "incoming", "name": os.path.basename(folder), "read_error": str(e)})
        tpath = self._taken_path(folder)
        if tpath and os.path.isfile(tpath):
            card["taken_from"] = load_json(tpath)
        card["files"] = self.file_tree(folder)
        if with_errors:
            card["warnings"] = []
            card["errors"] = validate.validate_folder(folder, card["warnings"])
        return card

    def file_tree(self, folder):
        out = []
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if not os.path.isfile(p):
                continue
            item = {"name": name, "size": os.path.getsize(p)}
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z:
                    item["entries"] = [{"name": i.filename, "size": i.file_size} for i in z.infolist()]
            out.append(item)
        return out

    def packages(self):
        cards = []
        for base in (self.work, self.incoming):
            for name in os.listdir(base):
                folder = os.path.join(base, name)
                if name.startswith("_") or not os.path.isdir(folder):
                    continue
                if base == self.incoming:
                    try:
                        folder = self._find_package_root(folder)
                    except MedoError:
                        continue
                try:
                    cards.append(self.package_card(folder, with_errors=False))
                except Exception:
                    continue
        _link(cards)
        cards.sort(key=lambda c: os.path.getmtime(c["folder"]), reverse=True)
        return cards[:300]

    def delete_package(self, body):
        folder = self.pkg_path(body["id"])
        if body["id"].startswith("_incoming/"):
            folder = os.path.join(self.incoming, body["id"].split("/")[1])
            tpath = self._taken_path(folder)
            if tpath and os.path.isfile(tpath):
                os.remove(tpath)
        shutil.rmtree(folder)
        mpath = os.path.join(os.path.dirname(folder), "_manifests", os.path.basename(folder) + ".json")
        if os.path.isfile(mpath):
            os.remove(mpath)
        return {"ok": True}

    def read_file(self, pid, name, entry=None):
        folder = self.pkg_path(pid)
        path = self.inside(os.path.join(folder, name), folder)
        with open(path, "rb") as f:
            data = f.read()
        if entry:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                data = z.read(entry)
            name = entry
        return name, data

    def zip_package(self, pid):
        folder = self.pkg_path(pid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(folder)):
                p = os.path.join(folder, name)
                if os.path.isfile(p) and name != "manifest.json":
                    z.write(p, name)
        return os.path.basename(folder) + ".zip", buf.getvalue()

    def zip_many(self, ids):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for pid in ids:
                folder = self.pkg_path(pid)
                for name in sorted(os.listdir(folder)):
                    p = os.path.join(folder, name)
                    if os.path.isfile(p) and name != "manifest.json":
                        z.write(p, f"{os.path.basename(folder)}/{name}")
        return f"ответы_{_stamp()}.zip", buf.getvalue()

    # --- обмен с СЭД через папки OUT / OUTArchiveGen / IN ------------------------------------

    def _exchange_paths(self, stand):
        path = self.stand_path(stand)
        p = exchange.paths(load_json(path) if os.path.isfile(path) else {})
        if not p:
            raise MedoError("для стенда не заданы папки обмена с СЭД (вкладка «Организации»)")
        return p

    def exchange_save(self, body):
        path = self.stand_path(body.get("stand"))
        data = load_json(path) if os.path.isfile(path) else {"stand": "", "self": None, "orgs": {}}
        ex = body.get("exchange") or {}
        data["exchange"] = {k: (ex.get(k) or "").strip() for k in ("out", "archive", "in")}
        self._write_json(path, data)
        return self.orgs(os.path.basename(path))

    def exchange_check(self, body):
        p = self._exchange_paths(body.get("stand"))
        res = {}
        with EXCHANGE_LOCK:
            for key in ("out", "archive", "in"):
                folder = p[key]
                if not folder:
                    res[key] = {"ok": False, "error": "путь не задан"}
                    continue
                try:
                    if key == "out":
                        os.listdir(folder)
                    else:
                        if key == "archive" and not os.path.isdir(folder):
                            os.mkdir(folder)  # только сам архив, без несуществующих родителей
                        probe = os.path.join(folder, f".medogen-probe-{uuid.uuid4().hex[:8]}.tmp")
                        with open(probe, "w") as f:
                            f.write("medogen")
                        os.remove(probe)
                    res[key] = {"ok": True, "path": folder}
                except OSError as e:
                    res[key] = {"ok": False, "path": folder, "error": f"нет доступа к {folder}: {e}"}
        return res

    def exchange_out(self, stand):
        p = self._exchange_paths(stand)
        try:
            return {"out": p["out"], "items": exchange.list_out(p["out"]), "error": None}
        except MedoError as e:
            return {"out": p["out"], "items": [], "error": str(e)}

    def exchange_take(self, body):
        p = self._exchange_paths(body.get("stand"))
        name = body.get("name") or ""
        with EXCHANGE_LOCK:
            dest = os.path.join(self.incoming, safe_dir_name(f"{_stamp()}_{name}"))
            copy, archived, err = exchange.take(p["out"], p["archive"], name, dest)
        stand_file = os.path.basename(self.stand_path(body.get("stand")))
        self._write_taken(dest, {"stand_file": stand_file, "stand": self.orgs(stand_file).get("stand") or stand_file,
                                 "out": p["out"], "archived": archived,
                                 "at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S")})
        return {"package": self.package_card(self._find_package_root(copy)),
                "archived": archived, "archive_error": err}

    def _taken_path(self, folder):
        """_incoming/_taken/<папка>.json — откуда (стенд, OUT) забран входящий пакет."""
        rel = self.pkg_id(folder)
        if not rel.startswith("_incoming/"):
            return None
        return os.path.join(self.incoming, "_taken", rel.split("/")[1] + ".json")

    def _write_taken(self, dest, info):
        path = self._taken_path(dest)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

    def _mark_sent_in(self, folder, path):
        """Отметка «положен в IN» в манифесте; ошибка записи отметки не отменяет саму отправку."""
        try:
            mpath = builder.manifest_path(folder)
            old = os.path.join(folder, "manifest.json")  # пакеты старых версий
            src = mpath if os.path.isfile(mpath) else old if os.path.isfile(old) else None
            man = load_json(src) if src else {}
            man.setdefault("sent_in", []).append({"at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "path": path})
            with open(mpath, "w", encoding="utf-8") as f:
                json.dump(man, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"пакет положен в IN, но отметка не записана: {e}"
        return None

    def exchange_put(self, body):
        p = self._exchange_paths(body.get("stand"))
        if not p["in"]:
            raise MedoError("для стенда не задана папка IN")
        results = []
        for pid in body.get("ids") or []:
            item = {"id": pid, "ok": False}
            try:
                folder = self.pkg_path(pid)
                if self.pkg_id(folder).split("/")[0] in ("_incoming", "_uploads", "_scenarios", "_manifests"):
                    raise MedoError("входящий пакет в IN не кладётся")
                if validate.validate_folder(folder, []):
                    raise MedoError("в пакете ошибки проверки, в IN не положен")
                with EXCHANGE_LOCK:
                    item["path"] = exchange.put_in(folder, p["in"])
                item["ok"] = True
                warn = self._mark_sent_in(folder, item["path"])
                if warn:
                    item["warning"] = warn
            except MedoError as e:
                item["error"] = str(e)
            except Exception as e:  # один плохой пакет не должен срывать остальные
                item["error"] = f"{type(e).__name__}: {e}"
            results.append(item)
        return {"results": results}

    # --- сборка ---------------------------------------------------------------------------

    def _fix_refs(self, spec):
        for key in ("reply_to", "based_on"):
            v = spec.get(key)
            if isinstance(v, str) and v.startswith("ui:"):
                spec[key] = self.pkg_path(v[3:])
        return spec

    def build(self, body):
        scenario = body.get("scenario") or {}
        if body.get("example"):
            path = os.path.join(self.examples_dir, os.path.basename(body["example"]))
            prefix = os.path.splitext(os.path.basename(path))[0]
        else:
            if "packages" in scenario:
                scenario["packages"] = [self._fix_refs(p) for p in scenario["packages"]]
            else:
                self._fix_refs(scenario)
            prefix = _stamp()
            path = os.path.join(self.scenarios, prefix + ".json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(scenario, f, ensure_ascii=False, indent=2)
        stand = self.stand_path(body.get("stand"))
        log = io.StringIO()
        result = {"packages": [], "warnings": [], "error": None}
        with LOCK:
            WARNINGS.clear()
            try:
                with contextlib.redirect_stdout(log):
                    run = builder.Run(path, [stand] if os.path.isfile(stand) else [], self.work)
                    if body.get("example"):
                        run.scenario_name = f"{_stamp()}_{prefix}"
                    else:
                        run.scenario_name = prefix
                    built = run.build_all()
                for folder, _ in built:
                    result["packages"].append(self.package_card(folder))
            except MedoError as e:
                result["error"] = str(e)
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"
                log.write(traceback.format_exc())
            result["warnings"] = list(WARNINGS)
        result["log"] = log.getvalue()
        result["scenario"] = scenario if not body.get("example") else load_json(path)
        return result


def _link(cards):
    """parent_id у ответа и children у основного пакета. Основание ищется по папке из манифеста,
    затем по GUID сообщения; у старых квитанций без parent — по GUID сообщения, на которое они отвечают."""
    by_folder = {os.path.normcase(os.path.abspath(c["folder"])): c for c in cards}
    by_msg = {}
    for c in cards:
        if c.get("msg_uid"):
            by_msg.setdefault(c["msg_uid"].lower(), c)
    for c in cards:
        c.setdefault("children", [])
    for c in cards:
        par, ref = c.get("parent") or {}, None
        if par.get("folder"):
            ref = by_folder.get(os.path.normcase(os.path.abspath(par["folder"])))
        uid = par.get("msg_uid") or (c.get("on_msg_uid") if c.get("type") == "receipt" else None)
        if ref is None and uid:
            ref = by_msg.get(uid.lower())
        if ref is not None and ref is not c:
            c["parent_id"] = ref["id"]
            ref["children"].append(c["id"])


def _stamp():
    """Префикс папок: время и случайный хвост, чтобы сборки в одну секунду не затирали друг друга."""
    return f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:3]}"


# --- HTTP -----------------------------------------------------------------------------------

def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "medogen-ui"

        def log_message(self, fmt, *args):  # тише в консоли
            pass

        def _send(self, code, data, ctype="application/json; charset=utf-8", headers=None):
            if not isinstance(data, (bytes, bytearray)):
                data = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _json(self):
            raw = self._body()
            return json.loads(raw.decode("utf-8")) if raw else {}

        def _guard(self, fn):
            try:
                fn()
            except MedoError as e:
                self._send(400, {"error": str(e)})
            except (FileNotFoundError, KeyError) as e:
                self._send(404, {"error": f"не найдено: {e}"})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            self._guard(self._get)

        def do_POST(self):
            self._guard(self._post)

        def _get(self):
            url = urllib.parse.urlparse(self.path)
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            path = url.path
            if path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/meta":
                return self._send(200, app.meta())
            if path == "/api/orgs":
                return self._send(200, app.orgs(q.get("stand")))
            if path == "/api/packages":
                return self._send(200, app.packages())
            if path == "/api/exchange/out":
                return self._send(200, app.exchange_out(q.get("stand")))
            if path == "/api/gas":
                return self._send(200, app.gas_search(q.get("q", "")))
            if path == "/api/package":
                return self._send(200, app.package_card(app.pkg_path(q["id"])))
            if path == "/api/file":
                name, data = app.read_file(q["id"], q["name"], q.get("entry"))
                if q.get("raw"):
                    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    return self._send(200, data, ctype, {
                        "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
                return self._send(200, {"name": name, "text": _as_text(name, data), "size": len(data)})
            if path == "/api/download-many":
                name, data = app.zip_many([x for x in q["ids"].split(",") if x])
                return self._send(200, data, "application/zip", {
                    "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
            if path == "/api/download":
                name, data = app.zip_package(q["id"])
                return self._send(200, data, "application/zip", {
                    "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
            return self._send(404, {"error": "нет такого адреса"})

        def _post(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/upload":
                raw = self.headers.get("X-Filename") or "file"
                if not raw.isascii():  # имя пришло без кодирования (не из браузера)
                    raw = urllib.parse.quote(raw.encode("latin-1", "replace"))
                name = urllib.parse.unquote(raw)
                return self._send(200, app.upload(name, self._body()))
            body = self._json()
            routes = {
                "/api/build": app.build,
                "/api/incoming": app.incoming_package,
                "/api/delete": app.delete_package,
                "/api/orgs/save": app.save_org,
                "/api/orgs/delete": app.delete_org,
                "/api/orgs/new": app.new_stand,
                "/api/orgs/delete-stand": app.delete_stand,
                "/api/orgs/import": app.import_orgs,
                "/api/gas/import": app.gas_import,
                "/api/exchange/save": app.exchange_save,
                "/api/exchange/check": app.exchange_check,
                "/api/exchange/take": app.exchange_take,
                "/api/exchange/put": app.exchange_put,
            }
            if path not in routes:
                return self._send(404, {"error": "нет такого адреса"})
            return self._send(200, routes[path](body))

    return Handler


def _as_text(name, data):
    """Текст файла для просмотра: XML/ini/txt/json; для двоичных — пометка."""
    low = name.lower()
    if low.endswith(".ini"):
        try:
            return data.decode("cp1251")
        except UnicodeDecodeError:
            pass
    if low.endswith((".xml", ".txt", ".json", ".ini", ".csv", ".html")):
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        for enc in ("utf-8", "cp1251"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
    if data[:5] == b"-----" or (low.endswith(".p7s") and all(32 <= b < 127 or b in (9, 10, 13) for b in data[:200])):
        return data.decode("ascii", "replace")
    return None


def serve(root, host="127.0.0.1", port=8765, open_browser=True):
    app = App(root)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"Интерфейс medogen: {url}")
    print(f"Пакеты сохраняются в {app.work}")
    print("Остановить: Ctrl+C")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Остановлено")
    finally:
        httpd.server_close()

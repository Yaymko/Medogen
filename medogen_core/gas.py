"""Локальная копия справочника ГАС ГосЭДО (gas/gas.json): все абоненты МЭДО с GUID,
наименованием и адресом. В отличие от orgs/*.json (организации конкретного стенда)
это общий справочник, из которого можно выбрать любого отправителя или получателя.

Наполняется импортом сохранённой страницы addresseesActual.html или CSV
(python medogen.py import-gas файл, либо кнопкой в интерфейсе); повторный импорт
дополняет и обновляет записи."""
import datetime as dt
import json
import os

from . import importer

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gas", "gas.json")


_CACHE = {}
_HAY = {}  # строки поиска для закэшированного справочника


def load(path=None):
    """Справочник целиком; кэшируется по времени изменения файла (в нём ~40 тыс. записей)."""
    path = path or DEFAULT_PATH
    if not os.path.isfile(path):
        return {"updated": None, "source": None, "orgs": {}}
    key = (os.path.getmtime(path), os.path.getsize(path))
    if _CACHE.get(path, (None,))[0] != key:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        data.setdefault("orgs", {})
        _CACHE[path] = (key, data)
    return _CACHE[path][1]


def by_guid(path=None):
    """{guid: {guid, name, address}}"""
    return {o["guid"].lower(): o for o in load(path)["orgs"].values() if o.get("guid")}


def save(data, path=None):
    path = path or DEFAULT_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        f.write(f'  "updated": {json.dumps(data.get("updated"))},\n')
        f.write(f'  "source": {json.dumps(data.get("source"), ensure_ascii=False)},\n')
        f.write('  "orgs": {')
        items = sorted(data["orgs"].items(), key=lambda kv: kv[1].get("name", "").lower())
        for i, (k, v) in enumerate(items):
            f.write(f'\n    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}')
            f.write("," if i < len(items) - 1 else "\n  ")
        f.write("}\n}\n")


def merge(entries, source=None, path=None, replace=False):
    """entries: {любой ключ: {guid, name, address[, org, active]}}.
    replace=True — полная выгрузка ГАС: записи, которых в ней нет, удаляются.
    Возвращает (добавлено, обновлено, всего)."""
    data = dict(load(path))
    old_orgs = data["orgs"]
    orgs = {} if replace else old_orgs
    added = updated = 0
    for e in entries.values():
        guid = e["guid"].lower()
        new = {"guid": guid, "name": e["name"], "address": e.get("address")}
        if e.get("org") and e["org"] != e["name"]:
            new["org"] = e["org"]
        if e.get("active") is False:
            new["active"] = False
        old = old_orgs.get(guid)
        if old is None:
            added += 1
        elif old != new:
            if not new["address"]:  # не затираем известный адрес пустым
                new["address"] = old.get("address")
            updated += old != new
        orgs[guid] = new
    data["orgs"] = orgs
    data["updated"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    data["source"] = source or data.get("source")
    save(data, path)
    return added, updated, len(orgs)


def import_file(src, path=None):
    text = importer.read_text(src)
    rows = importer.parse_gosedo(text) if not src.lower().endswith(".csv") else None
    if rows is not None:  # полная выгрузка ГАС
        return merge(importer.gosedo_entries(rows), f"{os.path.basename(src)}, записей {len(rows)}",
                     path, replace=True)
    return merge(importer.parse(text, src), os.path.basename(src), path)


def search(q, limit=50, path=None):
    """Поиск по наименованию, адресу и GUID; слова запроса ищутся независимо."""
    words = (q or "").lower().split()
    data = load(path)
    if _HAY.get(id(data), (None,))[0] is not data:
        _HAY.clear()
        _HAY[id(data)] = (data, [(f'{o.get("name", "")} {o.get("org", "")} {o.get("address") or ""} '
                                  f'{o["guid"]}'.lower(), o) for o in data["orgs"].values()])
    res = [o for hay, o in _HAY[id(data)][1] if all(w in hay for w in words)]
    res.sort(key=lambda o: (o.get("active") is False,
                            not o.get("name", "").lower().startswith(words[0]) if words else False,
                            len(o.get("name", "")), o.get("name", "")))
    return res[:limit], len(res)

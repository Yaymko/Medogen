"""Обмен с СЭД через общие папки: OUT (исходящие СЭД), OUTArchiveGen (забранные), IN (входящие для СЭД).
Собранный пакет можно положить и в IN, и в OUT (тогда его заберёт маршрутизация или «Забрать»).

Пакет — подпапка с envelope.ini. Функции получают пути явно и не зависят от интерфейса."""
import datetime as dt
import json
import os
import shutil

from .builder import manifest_path
from .common import MedoError, load_json
from .reply import read_package


def paths(stand):
    """Пути обмена из стенда; archive по умолчанию — OUTArchiveGen рядом с OUT. None, если обмен не настроен."""
    ex = (stand or {}).get("exchange") or {}
    out, inn = (ex.get("out") or "").strip(), (ex.get("in") or "").strip()
    if not out and not inn:
        return None
    archive = (ex.get("archive") or "").strip()
    if not archive and out:
        archive = sibling_archive(out)
    return {"out": out, "archive": archive, "in": inn,
            "route_to": (ex.get("route_to") or "").strip(), "route_auto": bool(ex.get("route_auto"))}


def sibling_archive(out, pathmod=os.path):
    r"""OUTArchiveGen рядом с OUT. Если OUT — корень общей папки (\\srv\OUT), это общая папка OUTArchiveGen."""
    out = out.rstrip("\\/") or out
    drive, rest = pathmod.splitdrive(out)
    if drive.startswith(("\\\\", "//")) and not rest.strip("\\/"):
        return drive + "ArchiveGen"
    return pathmod.join(pathmod.dirname(out), "OUTArchiveGen")


def _is_package(folder):
    return os.path.isfile(os.path.join(folder, "envelope.ini"))


def list_out(out_dir):
    """Пакеты в OUT, от новых к старым. Нечитаемый пакет попадает в список с полем error."""
    try:
        names = os.listdir(out_dir)
    except OSError as e:
        raise MedoError(f"нет доступа к {out_dir}: {e}")
    items = []
    for name in names:
        folder = os.path.join(out_dir, name)
        if name.startswith((".", "_")) or not os.path.isdir(folder) or not _is_package(folder):
            continue
        item = {"name": name, "mtime": os.path.getmtime(folder)}
        try:
            info = read_package(folder)
            item.update({"format": info.get("format"), "msg_uid": info.get("msg_uid"),
                         "from": info.get("source"), "to": info.get("receivers", []),
                         "reg_number": info.get("doc_number"), "reg_date": info.get("doc_date")})
        except Exception as e:  # пакет может ещё копироваться
            item["error"] = str(e) or type(e).__name__
        items.append(item)
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def _free_name(path):
    if not os.path.exists(path):
        return path
    n = 1
    while os.path.exists(f"{path}_{n}"):
        n += 1
    return f"{path}_{n}"


def take(out_dir, archive_dir, name, dest_dir):
    """Копирует пакет из OUT в dest_dir и переносит оригинал в архив.

    Возвращает (папка копии, путь в архиве или None, текст ошибки переноса или None).
    Копия делается первой, поэтому сбой переноса пакет не теряет."""
    if not isinstance(name, str) or not name or name.startswith((".", "_")) or any(c in name for c in "\\/:"):
        raise MedoError(f"недопустимое имя пакета: {name!r}")
    try:
        present = name in os.listdir(out_dir)
    except OSError as e:
        raise MedoError(f"нет доступа к {out_dir}: {e}")
    src = os.path.join(out_dir, name)
    if not present or not os.path.isdir(src):
        raise MedoError(f"пакет уже забран или не найден: {name}")
    if not _is_package(src):
        raise MedoError(f"в папке {name} нет envelope.ini, это не пакет")
    try:
        shutil.copytree(src, dest_dir)
    except OSError as e:
        shutil.rmtree(dest_dir, ignore_errors=True)  # недописанная копия не должна выглядеть пакетом
        raise MedoError(f"не удалось скопировать пакет {name}: {e}")
    try:
        if not os.path.isdir(archive_dir):
            os.mkdir(archive_dir)
        archived = _free_name(os.path.join(archive_dir, name))
        shutil.move(src, archived)
    except OSError as e:
        return dest_dir, None, f"пакет скопирован, но не перенесён в архив {archive_dir}: {e}"
    return dest_dir, archived, None


def _copy_package(src, dst):
    """Копия папки пакета; envelope.ini пишется последним, чтобы СЭД не приняла недописанную папку за пакет."""
    os.makedirs(dst)
    names = [n for n in os.listdir(src) if n != "manifest.json"]
    names.sort(key=lambda n: n.lower() == "envelope.ini")
    for n in names:
        s = os.path.join(src, n)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dst, n))
        else:
            shutil.copy2(s, os.path.join(dst, n))


TARGETS = {"in": "IN", "out": "OUT"}  # куда можно положить собранный пакет


def target_dir(p, to="in"):
    """Папка стенда для выкладки пакета: IN (входящие для СЭД) или OUT (как будто пакет отправила СЭД)."""
    to = (to or "in").lower()
    if to not in TARGETS:
        raise MedoError(f"положить пакет можно только в IN или OUT, а не в {to!r}")
    folder = (p or {}).get(to) or ""
    if not folder:
        raise MedoError(f"для стенда не задана папка {TARGETS[to]}")
    return folder


def put_in(package_dir, in_dir, name=None, label="IN"):
    """Кладёт папку пакета в IN (или OUT) под тем же (или заданным) именем: сначала во временную папку,
    затем переименование. Имя временной папки начинается с точки — list_out её не видит."""
    name = name or os.path.basename(os.path.normpath(package_dir))
    target = os.path.join(in_dir, name)
    if os.path.exists(target):
        raise MedoError(f"в {label} уже есть пакет {name}")
    tmp = os.path.join(in_dir, f".{name}.medogen-tmp")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        _copy_package(package_dir, tmp)
        os.rename(tmp, target)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise MedoError(f"не удалось положить пакет в {in_dir}: {e}")
    return target


def mark_sent(folder, to, path):
    """Отметка «положен в IN/OUT» (sent_in / sent_out) в манифесте пакета. Возвращает текст ошибки или None:
    сбой отметки не отменяет саму выкладку."""
    try:
        mpath = manifest_path(folder)
        old = os.path.join(folder, "manifest.json")  # пакеты старых версий
        src = mpath if os.path.isfile(mpath) else old if os.path.isfile(old) else None
        man = load_json(src) if src else {}
        man.setdefault(f"sent_{to}", []).append({"at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "path": path})
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"пакет положен в {TARGETS[to]}, но отметка не записана: {e}"
    return None

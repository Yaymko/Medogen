"""Обмен с СЭД через общие папки: OUT (исходящие СЭД), OUTArchive (забранные), IN (входящие для СЭД).

Пакет — подпапка с envelope.ini. Функции получают пути явно и не зависят от интерфейса."""
import os
import shutil

from .common import MedoError
from .reply import read_package


def paths(stand):
    """Пути обмена из стенда; archive по умолчанию — OUTArchive рядом с OUT. None, если обмен не настроен."""
    ex = (stand or {}).get("exchange") or {}
    out, inn = (ex.get("out") or "").strip(), (ex.get("in") or "").strip()
    if not out and not inn:
        return None
    archive = (ex.get("archive") or "").strip()
    if not archive and out:
        archive = os.path.join(os.path.dirname(os.path.normpath(out)), "OUTArchive")
    return {"out": out, "archive": archive, "in": inn}


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
    if not name or name in (".", "..") or name != os.path.basename(name.replace("\\", "/")):
        raise MedoError(f"недопустимое имя пакета: {name!r}")
    src = os.path.join(out_dir, name)
    if not os.path.isdir(src):
        raise MedoError(f"пакет уже забран или не найден: {name}")
    try:
        shutil.copytree(src, dest_dir)
    except OSError as e:
        raise MedoError(f"не удалось скопировать пакет {name}: {e}")
    try:
        os.makedirs(archive_dir, exist_ok=True)
        archived = _free_name(os.path.join(archive_dir, name))
        shutil.move(src, archived)
    except OSError as e:
        return dest_dir, None, f"пакет скопирован, но не перенесён в архив {archive_dir}: {e}"
    return dest_dir, archived, None


def put_in(package_dir, in_dir):
    """Кладёт папку пакета в IN под тем же именем: сначала во временную папку, затем переименование."""
    name = os.path.basename(os.path.normpath(package_dir))
    target = os.path.join(in_dir, name)
    if os.path.exists(target):
        raise MedoError(f"в IN уже есть пакет {name}")
    tmp = os.path.join(in_dir, f".{name}.medogen-tmp")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(package_dir, tmp)
        os.rename(tmp, target)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise MedoError(f"не удалось положить пакет в {in_dir}: {e}")
    return target

"""Маршрутизация между стендами: пакеты из OUT одного стенда после проверки кладутся в IN другого.

Один проход маршрута поверх exchange.take / validate.validate_folder / exchange.put_in.
Функции получают пути явно и не зависят от интерфейса."""
import datetime as dt
import os
import time

from . import exchange, validate
from .common import MedoError, dir_stamp, safe_dir_name

SETTLE = 5  # секунд без изменений, прежде чем пакет считается дописанным


def last_change(folder):
    """Самое позднее время изменения папки и всего, что в ней лежит."""
    latest = os.path.getmtime(folder)
    for cur, dirs, files in os.walk(folder):
        for n in dirs + files:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(cur, n)))
            except OSError:  # файл переименовали или удалили, пока мы смотрели
                latest = time.time()
    return latest


BLOCKED = "blocked"  # отметка в seen: пакет не перенесён из OUT, повторно не берём


def snapshot(folder):
    """Состав, размеры и времена файлов пакета: если между проходами не изменились, пакет дописан."""
    sig = []
    for cur, _dirs, files in os.walk(folder):
        for n in files:
            st = os.stat(os.path.join(cur, n))
            sig.append((os.path.relpath(os.path.join(cur, n), folder), st.st_size, st.st_mtime))
    return sorted(sig)


def _ready(folder, name, settle, seen):
    """Дописан ли пакет. С seen — сравнение со снимком прошлого прохода (не зависит от часов
    сервера СЭД); без seen — пакет не менялся settle секунд по местным часам."""
    if settle <= 0:
        return True
    if seen is None:
        return time.time() - last_change(folder) >= settle
    sig = snapshot(folder)
    ready = seen.get(name) == sig
    seen[name] = sig
    return ready


def route_once(src, dst_in, incoming_dir, settle=SETTLE, seen=None, names=None):
    """Один проход: всё дописанное из src["out"] — во входящие, правильное — в dst_in.

    seen — словарь между проходами (снимки пакетов и отметки BLOCKED); меняется на месте.
    names — только эти пакеты (выбор тестировщика); пакета нет в OUT — запись take_failed.
    Возвращает записи по пакетам, от старых к новым. Нет доступа к OUT — MedoError."""
    items = exchange.list_out(src["out"])
    items.sort(key=lambda i: i["mtime"])
    if seen is not None:
        for gone in set(seen) - {i["name"] for i in items}:
            del seen[gone]  # пакет ушёл из OUT, отметки больше не нужны
    results = []
    if names is not None:
        present = {i["name"] for i in items}
        for name in names:
            if name not in present:
                results.append({"at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "name": name,
                                "format": None, "msg_uid": None, "copy": None, "archived": None,
                                "status": "take_failed", "in_path": None,
                                "errors": [f"пакет уже забран или не найден: {name}"], "warnings": []})
        items = [i for i in items if i["name"] in set(names)]
    for item in items:
        name = item["name"]
        if seen is not None and seen.get(name) == BLOCKED:
            continue
        try:
            if not _ready(os.path.join(src["out"], name), name, settle, seen):
                continue  # пакет ещё дописывается
        except OSError:
            continue
        rec = {"at": dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "name": name,
               "format": item.get("format"), "msg_uid": item.get("msg_uid"), "copy": None,
               "archived": None, "status": None, "in_path": None, "errors": [], "warnings": []}
        results.append(rec)
        dest = os.path.join(incoming_dir, safe_dir_name(f"{dir_stamp()}_{name}"))
        try:
            rec["copy"], rec["archived"], err = exchange.take(src["out"], src["archive"], name, dest)
        except MedoError as e:
            rec["status"], rec["errors"] = "take_failed", [str(e)]
            continue
        if err:  # оригинал остался в OUT: переслать сейчас — значит слать его на каждом проходе
            rec["status"] = "take_failed"
            rec["errors"] = [err + ". Пакет не переслан, чтобы не отправить его повторно; исправьте доступ "
                             "и нажмите «Переслать сейчас» или уберите пакет из OUT"]
            if seen is not None:
                seen[name] = BLOCKED
            continue
        try:
            rec["errors"] = validate.validate_folder(rec["copy"], rec["warnings"])
        except Exception as e:  # проверка не должна ронять проход
            rec["errors"] = [f"проверка не выполнена: {type(e).__name__}: {e}"]
        if rec["errors"]:
            rec["status"] = "invalid"
            continue
        try:
            rec["in_path"] = exchange.put_in(rec["copy"], dst_in, name)
            rec["status"] = "sent"
        except MedoError as e:
            rec["status"], rec["errors"] = "put_failed", [str(e)]
    return results


def resend(copy, dst_in, name=None):
    """Повторная отправка пакета из входящих в IN без проверки. Возвращает путь в IN."""
    return exchange.put_in(copy, dst_in, name)

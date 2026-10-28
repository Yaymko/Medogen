"""Маршрутизация между стендами: пакеты из OUT одного стенда после проверки кладутся в IN другого.

Один проход маршрута поверх exchange.take / validate.validate_folder / exchange.put_in.
Функции получают пути явно и не зависят от интерфейса."""
import datetime as dt
import os
import time

from . import exchange, validate
from .common import MedoError, dir_stamp, safe_dir_name


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


def route_once(src, dst_in, incoming_dir, settle=5):
    """Один проход: всё устоявшееся из src["out"] — во входящие, правильное — в dst_in.

    Возвращает записи по пакетам, от старых к новым. Нет доступа к OUT — MedoError."""
    items = exchange.list_out(src["out"])
    items.sort(key=lambda i: i["mtime"])
    results = []
    for item in items:
        name = item["name"]
        try:
            if time.time() - last_change(os.path.join(src["out"], name)) < settle:
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
        if err:
            rec["warnings"].append(err)
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

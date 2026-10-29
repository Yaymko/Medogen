#!/usr/bin/env python3
"""medogen — генератор тестовых пакетов МЭДО 2.7.1 и 3.0.

Примеры:
  python medogen.py build examples\\30_document.json
  python medogen.py build examples --orgs orgs\\stand_test.json --zip
  python medogen.py build examples\\30_document.json --orgs orgs\\stand_test.json --put out
  python medogen.py put out\\30_document_01_3.0_document_ДЭВ --orgs orgs\\stand_test.json --to in
  python medogen.py validate out\\30_document_01_3.0_document_ДЭВ
  python medogen.py import-orgs addresseesActual.html orgs\\stand_test.json
  python medogen.py import-gas addresseesActual.html     (справочник ГАС: gas\\gas.json)
  python medogen.py gas Тюмен                             (поиск в справочнике ГАС)
  python medogen.py orgs
  python medogen.py ui                      (веб-интерфейс для тестировщиков)
"""
import argparse
import glob
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from medogen_core import builder, exchange, gas, importer, validate  # noqa: E402
from medogen_core.common import WARNINGS, Directory, MedoError, load_json  # noqa: E402

DEFAULT_ORGS = os.path.join(HERE, "orgs", "default.json")


def _console_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _stand_target(stand, to):
    """Папка IN или OUT стенда из его файла организаций (раздел exchange)."""
    p = exchange.paths(load_json(stand))
    if not p:
        raise MedoError(f"в {stand} не заданы папки обмена с СЭД (exchange: in/out)")
    return exchange.target_dir(p, to)


def _put(folder, target, to):
    """Кладёт проверенный пакет в IN/OUT стенда; возвращает 1 при ошибке, 0 при успехе."""
    label = exchange.TARGETS[to]
    errs = validate.validate_folder(folder)
    if errs:
        print(f"  {folder}: ошибки проверки, в {label} не положен")
        for e in errs:
            print(f"    {e}")
        return 1
    try:
        path = exchange.put_in(folder, target, label=label)
    except MedoError as e:
        print(f"  ОШИБКА: {e}")
        return 1
    warn = exchange.mark_sent(folder, to, path)
    print(f"  в {label}: {path}" + (f" ({warn})" if warn else ""))
    return 0


def cmd_build(args):
    paths = []
    for p in args.scenarios:
        if os.path.isdir(p):
            paths += sorted(glob.glob(os.path.join(p, "*.json")))
        else:
            paths.append(p)
    orgs = args.orgs or ([DEFAULT_ORGS] if os.path.isfile(DEFAULT_ORGS) else [])
    if args.put and not orgs:
        raise MedoError("для --put нужен стенд: укажите --orgs")
    target = _stand_target(orgs[0], args.put) if args.put else None
    os.makedirs(args.out, exist_ok=True)
    total_err = 0
    for path in paths:
        print(f"== {path}")
        try:
            run = builder.Run(path, orgs, args.out)
            results = run.build_all()
        except MedoError as e:
            print(f"  ОШИБКА: {e}")
            total_err += 1
            continue
        for folder, _ in results:
            if not args.no_validate:
                errs = validate.validate_folder(folder)
                for e in errs:
                    print(f"    XSD/структура: {e}")
                total_err += len(errs)
            if args.zip:
                shutil.make_archive(folder, "zip", folder)
            if target:
                total_err += _put(folder, target, args.put)
    if not validate.has_xsd() and not args.no_validate:
        print("Примечание: lxml не установлен, проверка по XSD пропущена (pip install lxml)")
    print(f"Готово. Предупреждений: {len(WARNINGS)}, ошибок: {total_err}")
    return 1 if total_err else 0


def cmd_validate(args):
    bad = 0
    for folder in args.folders:
        warns = []
        errs = validate.validate_folder(folder, warns)
        print(f"{folder}: {'OK' if not errs else 'ошибки'}")
        for e in errs:
            print(f"  {e}")
        for w in warns:
            print(f"  предупреждение: {w}")
        bad += bool(errs)
    if not validate.has_xsd():
        print("Примечание: lxml не установлен, проверка по XSD пропущена (pip install lxml)")
    return 1 if bad else 0


def cmd_put(args):
    stand = args.orgs or DEFAULT_ORGS
    target = _stand_target(stand, args.to)
    bad = sum(_put(folder, target, args.to) for folder in args.folders)
    return 1 if bad else 0


def cmd_import(args):
    n = importer.import_file(args.source, args.target, args.stand)
    print(f"Импортировано организаций: {n} -> {args.target}")
    print("Проверьте адреса МЭДО (address) и укажите свою организацию в поле self.")
    return 0


def cmd_import_gas(args):
    added, updated, total = gas.import_file(args.source)
    print(f"Справочник ГАС {gas.DEFAULT_PATH}: добавлено {added}, обновлено {updated}, всего {total}")
    if not total:
        print("Организации не найдены: пришлите файл разработчику генератора, формат мог измениться.")
    return 0


def cmd_gas(args):
    found, n = gas.search(" ".join(args.query), limit=args.limit)
    for o in found:
        print(f"  {o['guid']}  {o.get('address') or '-':25} {o['name']}")
    print(f"Найдено: {n}" + (f", показано {len(found)}" if n > len(found) else ""))
    return 0


def cmd_orgs(args):
    paths = args.orgs or [DEFAULT_ORGS]
    for p in paths:
        data = load_json(p)
        d = Directory(data)
        print(f"{p} (стенд: {data.get('stand', '-')}, self: {data.get('self')})")
        for key, o in d.orgs.items():
            print(f"  {key:20} {o.guid}  {o.address or '-':25} {o.name}")
    return 0


def cmd_ui(args):
    from medogen_core import webui
    webui.serve(HERE, args.host, args.port, not args.no_browser, args.route_interval)
    return 0


def main():
    _console_utf8()
    ap = argparse.ArgumentParser(prog="medogen", description="Генератор пакетов МЭДО 2.7.1 / 3.0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="собрать пакеты по сценариям JSON")
    b.add_argument("scenarios", nargs="+", help="файлы сценариев или папки с ними")
    b.add_argument("--orgs", action="append", help="справочник организаций стенда (можно несколько)")
    b.add_argument("--out", default=os.path.join(HERE, "out"), help="папка для пакетов (по умолчанию out)")
    b.add_argument("--zip", action="store_true", help="дополнительно упаковать каждый пакет в .zip")
    b.add_argument("--no-validate", action="store_true", help="не проверять собранные пакеты")
    b.add_argument("--put", choices=["in", "out"],
                   help="положить пакеты без ошибок в папку IN или OUT стенда (первый --orgs, раздел exchange)")
    b.set_defaults(func=cmd_build)

    pt = sub.add_parser("put", help="положить готовые пакеты в папку IN или OUT стенда")
    pt.add_argument("folders", nargs="+", help="папки пакетов (с envelope.ini)")
    pt.add_argument("--orgs", help="файл стенда с папками обмена (по умолчанию orgs/default.json)")
    pt.add_argument("--to", choices=["in", "out"], default="in", help="куда положить: in (по умолчанию) или out")
    pt.set_defaults(func=cmd_put)

    v = sub.add_parser("validate", help="проверить готовый пакет (папку с envelope.ini)")
    v.add_argument("folders", nargs="+")
    v.set_defaults(func=cmd_validate)

    i = sub.add_parser("import-orgs", help="импорт организаций из addresseesActual.html или CSV")
    i.add_argument("source")
    i.add_argument("target")
    i.add_argument("--stand", help="название стенда")
    i.set_defaults(func=cmd_import)

    g = sub.add_parser("import-gas", help="загрузить справочник ГАС (addresseesActual.html или CSV) в gas/gas.json")
    g.add_argument("source")
    g.set_defaults(func=cmd_import_gas)

    gs = sub.add_parser("gas", help="поиск в справочнике ГАС")
    gs.add_argument("query", nargs="*")
    gs.add_argument("--limit", type=int, default=30)
    gs.set_defaults(func=cmd_gas)

    o = sub.add_parser("orgs", help="показать справочник организаций")
    o.add_argument("--orgs", action="append")
    o.set_defaults(func=cmd_orgs)

    u = sub.add_parser("ui", help="веб-интерфейс в браузере")
    u.add_argument("--host", default="127.0.0.1",
                   help="адрес (0.0.0.0 — открыть доступ коллегам по сети)")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    u.add_argument("--route-interval", type=float, default=10,
                   help="опрос маршрутов между стендами, секунд (0 — не опрашивать)")
    u.set_defaults(func=cmd_ui)

    args = ap.parse_args()
    try:
        sys.exit(args.func(args))
    except MedoError as e:
        print(f"ОШИБКА: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()

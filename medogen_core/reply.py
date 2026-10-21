"""Чтение входящего пакета МЭДО для ответов (квитанция, уведомление, следующее
событие по поручению): reply_to в сценарии указывает на папку пакета."""
import os
import zipfile
import xml.etree.ElementTree as ET

from .common import MedoError

NS = {
    "m27": "urn:IEDMS:MESSAGE",
    "c27": "urn:IEDMS:CONTAINER",
    "m30": "urn:IEDMS:MESSAGE:3.0",
    "c30": "urn:IEDMS:CONTAINER:3.0",
    "cdm": "urn:Adm01-002:CoreDomainMeta:v1.0.0",
    "exc": "urn:Exc01-005-00001:ExchangeMeta:v1.0.0",
    "exc401": "urn:Exc01-004-00001:ExchangeMeta:v1.0.0",
    "sdm005": "urn:Adm01-005:SubjectDomainMeta:v1.0.0",
}


def _parse(data):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return ET.fromstring(data)


def _attr(el, name, ns):
    if el is None:
        return None
    return el.get(f"{{{NS[ns]}}}{name}") or el.get(name)


def read_package(path):
    """Возвращает словарь со сведениями о пакете."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    msg_path = path if os.path.isfile(path) else None
    if not msg_path:
        for cand in ("message.xml", "container.xml", "communication.xml"):
            if os.path.isfile(os.path.join(folder, cand)):
                msg_path = os.path.join(folder, cand)
                break
    if not msg_path:
        raise MedoError(f"reply_to: в {folder} нет message.xml / container.xml")
    with open(msg_path, "rb") as f:
        root = _parse(f.read())
    info = {"folder": folder, "receivers": []}

    if root.tag == f"{{{NS['m30']}}}message":
        info["format"] = "3.0"
        h = root.find("m30:header", NS)
        info["msg_uid"] = _attr(h, "msgUid", "m30")
        src = h.find("m30:source", NS)
        info["source"] = {"guid": _attr(src, "uid", "m30"), "name": (src.text or "").strip()}
        for r in root.findall("m30:receivers/m30:receiver", NS):
            info["receivers"].append({"guid": _attr(r, "uid", "m30"), "name": (r.text or "").strip()})
        f = root.find("m30:payload/m30:container/m30:file", NS)
        info["container_file"] = f.text.strip() if f is not None else None
    elif root.tag == f"{{{NS['m27']}}}communication":
        info["format"] = "2.7.1"
        h = root.find("m27:header", NS)
        info["msg_uid"] = _attr(h, "uid", "m27")
        src = h.find("m27:source", NS)
        info["source"] = {"guid": _attr(src, "uid", "m27"),
                          "name": src.findtext("m27:organization", "", NS).strip()}
        for d in root.findall("m27:deliveryIndex/m27:destination/m27:destination", NS):
            info["receivers"].append({"guid": _attr(d, "uid", "m27"),
                                      "name": d.findtext("m27:organization", "", NS).strip()})
        b = root.find("m27:container/m27:body", NS)
        info["container_file"] = b.text.strip() if b is not None else None
    else:
        raise MedoError(f"reply_to: {msg_path} не похож на паспорт ЭС 2.7.1/3.0")

    zpath = os.path.join(folder, info["container_file"]) if info.get("container_file") else None
    if zpath and os.path.isfile(zpath):
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            if "passport.xml" in names:
                _read_passport(_parse(z.read("passport.xml")), info)
            if "digital.xml" in names:
                _read_digital(_parse(z.read("digital.xml")), info)
    return info


def _read_passport(p, info):
    if p.tag == f"{{{NS['c30']}}}container":
        d = p.find("c30:document", NS)
        info["doc_uid"] = _attr(d, "docUid", "c30")
        reg = p.find("c30:authors/c30:author/c30:registration", NS)
        org = p.find("c30:authors/c30:author/c30:organization", NS)
        info["doc_number"] = reg.findtext("c30:number", "", NS)
        info["doc_date"] = reg.findtext("c30:date", "", NS)
        info["doc_org"] = org.findtext("c30:title", "", NS)
    else:
        info["doc_uid"] = _attr(p, "uid", "c27")
        reg = p.find("c27:authors/c27:author/c27:registration", NS)
        org = p.find("c27:authors/c27:author/c27:organization", NS)
        info["doc_number"] = reg.findtext("c27:number", "", NS)
        info["doc_date"] = reg.findtext("c27:date", "", NS)
        info["doc_org"] = org.findtext("c27:title", "", NS)


def _read_digital(d, info):
    if not info.get("doc_uid"):  # ТК ГосЭДО без паспорта: УУИД документа берём из digital.xml
        ref_doc = d.find(".//cdm:СсылкаДокумента", NS)
        if ref_doc is not None:
            info["doc_uid"] = _attr(ref_doc, "ДокументУУИД", "cdm")
    reg = d.find(".//exc401:РегистрацияДокумента/exc401:ИнформацияОПроцессе/cdm:СсылкаПроцесса", NS)
    if reg is not None:
        info["reg_process_uid"] = _attr(reg, "ПроцессУУИД", "cdm")
        info["reg_process_title"] = _attr(reg, "Представление", "cdm")
        created = d.find(".//exc401:РегистрацияДокумента/exc401:ИнформацияОПроцессе/cdm:ВремяСоздания", NS)
        info["reg_process_created"] = created.text if created is not None else None
    items = []
    for ev in d.findall(".//doc:СообщенияДокумента/*", {"doc": d.tag[1:].split("}")[0]}):
        proc = ev.find("exc:ИнформацияОПроцессе/cdm:СсылкаПроцесса", NS)
        if proc is None:
            continue
        info["process_uid"] = _attr(proc, "ПроцессУУИД", "cdm")
        created = ev.find("exc:ИнформацияОПроцессе/cdm:ВремяСоздания", NS)
        info["process_created"] = created.text if created is not None else None
        num = ev.find(".//sdm005:Пункт", NS)
        ex = ev.find("exc:АгентыСобытия/exc:АгентыПолучатели/exc:Исполнитель/cdm:СсылкаАгента", NS)
        co = ev.findall("exc:АгентыСобытия/exc:АгентыПолучатели/exc:Соисполнитель/cdm:СсылкаАгента", NS)
        deadline = ev.find(".//exc:СрокИсполнения", NS)
        items.append({
            "number": num.text if num is not None else "1",
            "executor": _attr(ex, "АгентУУИД", "cdm"),
            "coexecutors": [_attr(c, "АгентУУИД", "cdm") for c in co],
            "deadline": deadline.text if deadline is not None else None,
        })
    if items:
        info["items"] = items

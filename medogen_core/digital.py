"""Формирование digital.xml (структурированные данные ГосЭДО).

Структуры повторяют эталоны «ЦП по формату 3.0», «ЦП 2.7.1» и «ДЭВ с digital»,
события Exc01-004-00001 «Регистрация входящего» и «Отказ в регистрации»
построены по Типовому регламенту ГосЭДО (таблица 2), эталонов для них нет.
"""
import base64
import hashlib
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from . import cms
from .common import MedoError, REFUSAL_REASONS, new_uuid
from .xmlb import E, Raw, document

NS_COMMON = {
    "xmlns:cdm": "urn:Adm01-002:CoreDomainMeta:v1.0.0",
    "xmlns:exc": "urn:Exc01-005-00001:ExchangeMeta:v1.0.0",
    "xmlns:mdm": "urn:Adm01-003:MasterDomainMeta:v1.0.0",
    "xmlns:sdm005": "urn:Adm01-005:SubjectDomainMeta:v1.0.0",
    "xmlns:sdm004": "urn:Adm01-004:SubjectDomainMeta:v1.0.0",
    "xmlns:adm": "urn:Adm01-001:AnyDomainMeta:v1.0.0",
    "xmlns:ds": "http://www.w3.org/2000/09/xmldsig#",
}
NS_401 = {
    "xmlns:exc401": "urn:Exc01-004-00001:ExchangeMeta:v1.0.0",
    "xmlns:cat401": "urn:Cat01-004-00001:CatalogMeta:v1.0.0",
    "xmlns:cat402": "urn:Cat01-004-00002:CatalogMeta:v1.0.0",
    "xmlns:cat403": "urn:Cat01-004-00003:CatalogMeta:v1.0.0",
    "xmlns:cat404": "urn:Cat01-004-00004:CatalogMeta:v1.0.0",
    "xmlns:cat405": "urn:Cat01-004-00005:CatalogMeta:v1.0.0",
}

DOC_RESOLUTION = ("Doc01-005-00001", "Резолюция")
DOC_NOTICE = ("Doc01-002-00001", "Документ (уведомление)")
NOTICE_TEMPLATE = "Doc01-002-00001_DocumentMeta_v1.0.0.xsl"

# Поручения, процесс Exc01-005-00001
EVENTS = {
    "issue": ("ВыдачаПоручения", "Event0001", "Выдача поручения", "Инициация"),
    "return": ("ВозвращениеДокладаНаДоработку", "Event0005", "Возвращение доклада на доработку",
               "Координация"),
    "stop": ("ПрекращениеКонтроляПоручения", "Event0006", "Прекращение контроля поручения",
             "Завершение"),
    "accept": ("ПринятиеИнформацииОбИсполнении", "Event0007", "Принятие информации об исполнении",
               "Координация"),
    "change_deadline": ("ИзменениеСрокаПоручения", "Event0010", "Изменение срока поручения",
                        "Координация"),
}
P5 = "Exc01-005-00001"
P4 = "Exc01-004-00001"


def _agent_ref(org):
    return E("cdm:СсылкаАгента", None, {"cdm:АгентУУИД": org.guid, "cdm:Представление": org.name})


def _process(prefix, pid, pname, uid, title, creator, created):
    return E(f"{prefix}:ИнформацияОПроцессе", None, {f"{prefix}:ВидИД": pid, f"{prefix}:ВидНазвание": pname},
             E("cdm:СсылкаПроцесса", None, {"cdm:ПроцессУУИД": uid, "cdm:Представление": title}),
             E("cdm:СсылкаСоздателя", None, {"cdm:АгентУУИД": creator.guid,
                                              "cdm:Представление": creator.name}),
             E("cdm:ВремяСоздания", created))


def _event(prefix, eid, ename, status, when):
    uid = new_uuid()
    return E(f"{prefix}:ИнформацияОСобытии", None, {f"{prefix}:ВидИД": eid, f"{prefix}:ВидНазвание": ename},
             E("cdm:СсылкаСобытия", None, {"cdm:СобытиеУУИД": uid,
                                            "cdm:Представление": f"{ename} от {when[:10]} #{uid}"}),
             E("cdm:ВремяСобытия", when),
             E(f"{prefix}:СтатусСобытия", status))


def _kind(prefix, pid, code, name):
    return {f"{prefix}:ВидИД": f"{pid}-{code}", f"{prefix}:ВидНазвание": name}


# --- Поручения ---------------------------------------------------------------

def assignment_messages(event, a, sender, when):
    """a: AssignmentSpec. Один элемент события на каждый пункт поручения."""
    if event not in EVENTS:
        raise MedoError(f"assignment.event: {event!r}, допустимо: {', '.join(EVENTS)}")
    tag, ecode, ename, status = EVENTS[event]
    out = []
    for item in a.items:
        receivers = [E("exc:Исполнитель", None, _kind("exc", P5, "Agent0002", "Исполнитель"),
                       _agent_ref(item.executor))]
        receivers += [E("exc:Соисполнитель", None, _kind("exc", P5, "Agent0003", "Соисполнитель"),
                        _agent_ref(o)) for o in item.coexecutors]
        punkt = E("exc:ПунктПоручения", None, None,
                  E("sdm005:РеквизитыПункта", None, None, E("sdm005:Пункт", item.number)))
        owner_init = E("exc:ВидВладельца", None, _kind("exc", P5, "Agent0001", "Инициатор"))
        owner_exec = E("exc:ВидВладельца", None, _kind("exc", P5, "Agent0002", "Исполнитель"))

        if event == "issue":
            order = E("exc:Поручение", None, _kind("exc", P5, "Thing0001", "Поручение"),
                      owner_init, E("exc:СостояниеПредмета", "Выдано"),
                      E("exc:ДанныеДляВизуализации", None, None, E("exc:ТекстПоручения", item.text)),
                      E("exc:ДанныеДляКоординации", None, None, punkt,
                        E("exc:АвторПоручения", None, {"mdm:ФИО": a.author_name,
                                                        "mdm:ИД": a.author_id})))
            report = E("exc:Доклад", None, _kind("exc", P5, "Thing0002", "Доклад"), owner_exec,
                       E("exc:СостояниеПредмета", "Запрошен"),
                       E("exc:ДанныеДляКоординации", None, None,
                         E("exc:СрокИсполнения", item.deadline)))
        else:
            order = E("exc:Поручение", None, _kind("exc", P5, "Thing0001", "Поручение"), owner_init,
                      E("exc:СостояниеПредмета", "Закрыто") if event == "stop" else None,
                      E("exc:ДанныеДляВизуализации", None, None, punkt))
            if event == "change_deadline":
                report = E("exc:Доклад", None, _kind("exc", P5, "Thing0002", "Доклад"), owner_exec,
                           E("exc:ДанныеДляКоординации", None, None,
                             E("exc:СрокИзмененный", item.new_deadline)))
            elif event == "return":
                report = E("exc:Доклад", None, _kind("exc", P5, "Thing0002", "Доклад"), owner_exec,
                           E("exc:СостояниеПредмета", "Возвращен"),
                           E("exc:ДанныеДляВизуализации", None, None,
                             E("exc:ТекстЗамечаний", item.remarks)),
                           E("exc:ДанныеДляКоординации", None, None,
                             E("exc:СрокУстраненияЗамечаний", item.fix_deadline)))
            elif event == "accept":
                report = E("exc:Доклад", None, _kind("exc", P5, "Thing0002", "Доклад"), owner_exec,
                           E("exc:СостояниеПредмета", "Принят"))
            else:
                report = None

        out.append(E(f"exc:{tag}", None, None,
                     _process("exc", P5, "Работа с поручением", a.process_uid,
                              f"Работа с поручением от {a.process_created[:10]} #{a.process_uid}",
                              sender, a.process_created),
                     _event("exc", f"{P5}-{ecode}", ename, status, when),
                     E("exc:АгентыСобытия", None, None,
                       E("exc:АгентОтправитель", None, None,
                         E("exc:Инициатор", None, _kind("exc", P5, "Agent0001", "Инициатор"),
                           _agent_ref(sender))),
                       E("exc:АгентыПолучатели", None, None, *receivers)),
                     E("exc:ПредметыСобытия", None, None, order, report)))
    return out


# --- Официальная регистрация (Exc01-004-00001) -------------------------------

def registration_block(doc, process_uid, receivers, when):
    """Событие «Регистрация документа» (раздел doc:РегистрацияДокумента)."""
    sender = doc.author
    title = f"Регистрация от {doc.reg_date} #{doc.reg_number}"
    links = None
    if doc.links:
        links = E("exc401:СвязиДокумента", None, None, *[
            E("exc401:СвязьДокумента", None, None,
              E("sdm004:СсылкаДокумента", None, {"cdm:ДокументУУИД": ln.uid,
                                                  "cdm:Представление": f"Документ # {ln.number} от {ln.date}"}),
              E("cat404:СсылкаТипСвязиДокументов", None, {"cdm:ИД": ln.type[0], "cdm:Название": ln.type[1]}))
            for ln in doc.links])
    return E("exc401:РегистрацияДокумента", None, NS_401,
             _process("exc401", P4, "Официальная регистрация документа", process_uid, title, sender, when),
             _event("exc401", f"{P4}-Event0001", "Регистрация документа", "Инициация", when),
             E("exc401:АгентыСобытия", None, None,
               E("exc401:АгентОтправитель", None, None,
                 E("exc401:Отправитель", None, _kind("exc401", P4, "Agent0001", "Отправитель"),
                   _agent_ref(sender))),
               E("exc401:АгентыПолучатели", None, None, *[
                   E("exc401:Получатель", None, _kind("exc401", P4, "Agent0002", "Получатель"),
                     _agent_ref(o)) for o in receivers])),
             E("exc401:ПредметыСобытия", None, None,
               E("exc401:Документ", None, _kind("exc401", P4, "Thing0001", "Документ"),
                 E("exc401:ВидВладельца", None, _kind("exc401", P4, "Agent0001", "Отправитель")),
                 E("exc401:СостояниеПредмета", "Документ отправлен"),
                 E("exc401:ДанныеДляКоординации", None, None,
                   E("exc401:РеквизитыДокумента", None, {"cdm:ДокументУУИД": doc.uid,
                                                          "cdm:Представление": title},
                     E("sdm004:РегистрацияДокумента", None, None,
                       E("sdm004:ОрганРегистрации", None, {"cdm:АгентУУИД": sender.guid,
                                                            "cdm:Представление": sender.name}),
                       E("sdm004:НомерРегистрации", doc.reg_number),
                       E("sdm004:ДатаРегистрации", doc.reg_date))),
                   E("cat401:СсылкаВидОфициальногоДокумента", None,
                     {"cdm:ИД": doc.kind[0], "cdm:Название": doc.kind[1]}),
                   E("cat402:СсылкаМестоСоставленияДокумента", None,
                     {"cdm:ИД": doc.place[0], "cdm:Название": doc.place[1]}),
                   E("cat403:СсылкаГрифОграниченияДоступа", None,
                     {"cdm:ИД": doc.classification[0], "cdm:Название": doc.classification[1]}),
                   links)),
               E("exc401:Экземпляр", None, _kind("exc401", P4, "Thing0002", "Экземпляр"),
                 E("exc401:ВидВладельца", None, _kind("exc401", P4, "Agent0002", "Получатель")),
                 E("exc401:СостояниеПредмета", "Запрошена регистрация"))))


def registration_answer(kind, n, me, original_sender, when):
    """Уведомление «Регистрация входящего» (kind='registered') или «Отказ в регистрации»
    (kind='refused'). n: NotificationSpec."""
    if kind == "registered":
        tag, code, ename, state = "РегистрацияВходящего", n.event_code or "Event0002", \
            "Регистрация входящего", "Выполнена регистрация"
        # элемент объявлен в схеме Exc01-004 (тип sdm004:ТипРегистрацияДокумента), поэтому сам он
        # в пространстве exc401, а его дочерние — в sdm004 (показатель 10.2 бота)
        data = [E("exc401:ДанныеДляКоординации", None, None,
                  E("exc401:РегистрацияДокумента", None, None,
                    E("sdm004:ОрганРегистрации", None, {"cdm:АгентУУИД": me.guid,
                                                         "cdm:Представление": me.name}),
                    E("sdm004:НомерРегистрации", n.number),
                    E("sdm004:ДатаРегистрации", n.date)))]
    else:
        tag, code, ename, state = "ОтказВРегистрации", n.event_code or "Event0003", \
            "Отказ в регистрации", "Отказ в регистрации"
        rid, rname = n.reasons[0]
        data = [E("exc401:ДанныеДляВизуализации", None, None,
                  E("exc401:ПояснениеОтказа", n.comment)) if n.comment else None,
                E("exc401:ДанныеДляКоординации", None, None,
                  E("cat405:СсылкаПричинаОтказа", None, {"cdm:ИД": rid, "cdm:Название": rname}))]
    return E(f"exc401:{tag}", None, NS_401,
             _process("exc401", P4, "Официальная регистрация документа", n.process_uid,
                      n.process_title, original_sender, n.process_created or when),
             _event("exc401", f"{P4}-{code}", ename, "Координация", when),
             E("exc401:АгентыСобытия", None, None,
               E("exc401:АгентОтправитель", None, None,
                 E("exc401:Получатель", None, _kind("exc401", P4, "Agent0002", "Получатель"),
                   _agent_ref(me))),
               E("exc401:АгентыПолучатели", None, None,
                 E("exc401:Отправитель", None, _kind("exc401", P4, "Agent0001", "Отправитель"),
                   _agent_ref(original_sender)))),
             E("exc401:ПредметыСобытия", None, None,
               E("exc401:Экземпляр", None, _kind("exc401", P4, "Thing0002", "Экземпляр"),
                 E("exc401:ВидВладельца", None, _kind("exc401", P4, "Agent0002", "Получатель")),
                 E("exc401:СостояниеПредмета", state), *data)))


def refusal_reason(value):
    if isinstance(value, dict):
        return str(value["id"]), value.get("name") or REFUSAL_REASONS.get(str(value["id"]), "")
    v = str(value)
    if v in REFUSAL_REASONS:
        return v, REFUSAL_REASONS[v]
    raise MedoError(f"причина отказа {v!r} не найдена (Cat01-004-00005: "
                    f"{', '.join(REFUSAL_REASONS)}), укажите {{\"id\":..., \"name\":...}}")


# --- Документ целиком --------------------------------------------------------

def _digits(seed, n):
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return [int(c, 16) % 10 for c in h[:n]]


def test_snils(seed):
    """СНИЛС из 11 цифр с верной контрольной суммой (детерминированный по seed)."""
    d = _digits("snils" + seed, 9)
    d[0] = d[0] or 1
    s = sum(x * (9 - i) for i, x in enumerate(d))
    c = s if s < 100 else 0 if s in (100, 101) else s % 101
    c = 0 if c == 100 else c
    return "".join(map(str, d)) + f"{c:02d}"


def _inn_check(d, coefs):
    return sum(x * k for x, k in zip(d, coefs)) % 11 % 10


def test_inn(seed, legal=False):
    """ИНН физлица (12 цифр) или организации (10 цифр) с верными контрольными цифрами."""
    if legal:
        d = [7, 7] + _digits("innul" + seed, 7)
        return "".join(map(str, d + [_inn_check(d, (2, 4, 10, 3, 5, 9, 4, 6, 8))]))
    d = [7, 7] + _digits("innfl" + seed, 8)
    d.append(_inn_check(d, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)))
    d.append(_inn_check(d, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)))
    return "".join(map(str, d))


def test_ogrn(seed):
    d = "1" + "".join(map(str, _digits("ogrn" + seed, 11)))
    return d + str(int(d) % 11 % 10)


def _principal(org):
    """ДанныеДоверителя, как в эталонах ЦП: организация подписанта (тестовые ОГРН и ИНН)."""
    if org is None:
        return ""
    return f"""
    <mdm:ДанныеДоверителя>
      <mdm:РоссийскоеЮридическоеЛицо>
        <mdm:Название>{escape(org.name)}</mdm:Название>
        <mdm:ОГРН>{test_ogrn(org.guid)}</mdm:ОГРН>
        <mdm:ИНН>{test_inn(org.guid, legal=True)}</mdm:ИНН>
      </mdm:РоссийскоеЮридическоеЛицо>
    </mdm:ДанныеДоверителя>"""


def signature_stub(signer, payload, org=None):
    """XMLDSig поручения (как в эталонах ЦП 3.0). Подписывается тестовым RSA-сертификатом medogen:
    структура, сертификат и значения разбираются, но без канонизации и ГОСТ подпись не пройдёт проверку УЦ."""
    digest = base64.b64encode(hashlib.sha256(payload.encode("utf-8")).digest()).decode()
    sig = _xmldsig("#ДанныеДокумента", ("http://www.w3.org/2001/10/xml-exc-c14n#",), digest)
    fam, _, rest = (escape(x) for x in signer.name.partition(" "))
    return Raw(f"""<mdm:ПодписьДокумента>
  {sig}
  <mdm:СтатусПодписи>{escape(signer.type)}</mdm:СтатусПодписи>
  <mdm:СертификатУКЭП>
    <mdm:СерийныйНомер>{cms.SERIAL:x}</mdm:СерийныйНомер>
    <mdm:ДатаНачала>2026-01-01</mdm:ДатаНачала>
    <mdm:ДатаОкончания>2055-12-25</mdm:ДатаОкончания>
    <mdm:ДанныеДержателя>
      <mdm:Фамилия>{fam}</mdm:Фамилия>
      <mdm:ИмяОтчество>{rest}</mdm:ИмяОтчество>
      <mdm:СНИЛС>{escape(signer.snils or test_snils(signer.name))}</mdm:СНИЛС>
      <mdm:ИНН>{escape(signer.inn or test_inn(signer.name))}</mdm:ИНН>
    </mdm:ДанныеДержателя>{_principal(org)}
  </mdm:СертификатУКЭП>
</mdm:ПодписьДокумента>""")


def _xmldsig(reference_uri, transforms, digest):
    """Signature c SignedInfo, подписанной тестовым RSA-сертификатом medogen (см. cms.py)."""
    signed_info = ("<SignedInfo>"
                   '<CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>'
                   '<SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
                   f'<Reference URI="{reference_uri}"><Transforms>'
                   + "".join(f'<Transform Algorithm="{t}"/>' for t in transforms) +
                   "</Transforms>"
                   '<DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
                   f"<DigestValue>{digest}</DigestValue></Reference></SignedInfo>")
    value = base64.b64encode(cms._rsa_sign(signed_info.encode("utf-8"))).decode()
    return (f'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">{signed_info}'
            f"<SignatureValue>{value}</SignatureValue>"
            f"<KeyInfo><X509Data><X509Certificate>{cms.CERT}</X509Certificate></X509Data></KeyInfo>"
            "</Signature>")


def sign_enveloped(xml_bytes):
    """Подпись контейнера ГосЭДО: /Документ/Signature, Reference URI="" + enveloped-signature,
    как в эталоне ЦП «Выдача поручения». Дайджест считается по канонической форме документа
    (C14N стандартной библиотеки), подпись — тестовым сертификатом."""
    text = xml_bytes.decode("utf-8")
    canon = ET.canonicalize(xml_data=text.split("?>", 1)[1] if text.startswith("<?xml") else text)
    digest = base64.b64encode(hashlib.sha256(canon.encode("utf-8")).digest()).decode()
    sig = _xmldsig("", ("http://www.w3.org/2000/09/xmldsig#enveloped-signature",
                        "http://www.w3.org/2001/10/xml-exc-c14n#"), digest)
    end = text.rindex("</doc:Документ>")
    return (text[:end] + sig + "\n" + text[end:]).encode("utf-8")


def build(doc_kind, doc_uid, creator, created, messages, needs_sign, needs_reg,
          registration=None, signers=None, fixed_template=None, dsp=False):
    ns_doc = f"urn:{doc_kind[0]}:DocumentMeta:v1.0.0"
    header = E("doc:ЗаголовокДокумента", None, {"doc:ВидИД": doc_kind[0], "doc:ВидНазвание": doc_kind[1]},
               E("cdm:СсылкаДокумента", None, {"cdm:ДокументУУИД": doc_uid,
                                                "cdm:Представление": f"{doc_kind[1]} от {created[:10]} #{doc_uid}"}),
               E("cdm:СсылкаСоздателя", None, {"cdm:АгентУУИД": creator.guid,
                                                "cdm:Представление": creator.name}),
               E("cdm:ВремяСоздания", created),
               E("cdm:СодержитСведенияДСП", "Да" if dsp else "Нет"),
               E("doc:ТребуетПодписания", "Да" if needs_sign else "Нет"),
               E("doc:ТребуетРегистрации", "Да" if needs_reg else "Нет"),
               E("doc:ФиксированныйШаблон", fixed_template) if fixed_template else None)
    data = E("doc:ДанныеДокумента", None, {"xml:id": "ДанныеДокумента"}, header,
             E("doc:СообщенияДокумента", None, None, *messages))
    root = E("doc:Документ", None, {"xmlns:doc": ns_doc, **NS_COMMON}, data)
    if needs_sign and signers:
        payload = data.render()
        root.add(E("doc:ПодписиДокумента", None, None, *[signature_stub(s, payload, creator) for s in signers]))
    if registration is not None:
        root.add(E("doc:РегистрацияДокумента", None, None, registration))
    return document(root, "utf-8").encode("utf-8")

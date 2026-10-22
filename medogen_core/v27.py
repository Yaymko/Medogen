"""Формат 2.7: паспорт ЭС в http://www.infpres.com/IEDMS (та же схема, что у 2.6), паспорт ТК в
http://minsvyaz.ru/container (version 1.0). Структура как у 2.7.1, отличия — в Dialect."""
from . import v271
from .common import MedoError

NS_M = "http://www.infpres.com/IEDMS"
NS_C = "http://minsvyaz.ru/container"
VERSION = "2.7"

# Коды справочника причин отказа → значения перечисления reasonForRejection схемы 2.7/2.6
REFUSAL_TEXTS = {
    "103": "Не указан корреспондент",
    "104": "Нет № и даты",
    "106": "Ошибка адресации",
    "110": "Не полная комплектация",
    "111": "Несоответствие содержимого транспортного контейнера утвержденным требованиям",
    "201": "Документ ДСП",
    "202": "Документ зарегистрирован ранее, текст взамен разосланного загружен",
    "203": "Текст не соответствует регламенту",
    "204": "Несоответствие переданных реквизитов тексту документа",
    "205": "Документ неверно адресован",
    "208": "Документ получен ранее на бумажном носителе",
    "209": "Нет подписи",
    "210": "Текст отсутствует",
    "211": "Текст неразборчив",
}
REFUSAL_ENUM = set(REFUSAL_TEXTS.values()) | {
    "Не подлежит регистрации", "Нет искового заявления", "Отсутствует текст", "Приложение отсутствует",
    "Листаж приложения указан ошибочно", "Несовпадение реквизитов приложения с приложенными документами",
    "Дата документа неразборчива", "Исходящий номер документа неразборчив",
    "Отсутствует (не видна) подпись", "Перевернуты страницы", "Продублированы страницы",
    "В приложении проект нормативного акта", "Документ и/или приложение отсканированы с оборотом",
    "Количество страниц в тексте документа не соответствует заявленному",
    "Отсутствует приложение, указанное в документе",
}


def refusal_text(rid, rname):
    if rname in REFUSAL_ENUM:
        return rname
    if str(rid) in REFUSAL_TEXTS:
        return REFUSAL_TEXTS[str(rid)]
    raise MedoError(f"причина отказа {rid} «{rname}» не предусмотрена схемой 2.7/2.6: укажите код "
                    f"{', '.join(REFUSAL_TEXTS)} или текст из перечня: {'; '.join(sorted(REFUSAL_ENUM))}")


class Dialect(v271.Dialect):
    """Имена файлов по эталону «2.7. Входящее письмо»: <имя>.pdf, <имя>_<n>_SigStamp.sig/.png,
    <имя>_RegStamp.png; приложения <имя>_att<i>.<ext> и .sig."""
    ns = NS_C
    version = "1.0"
    doc_name = "document.pdf"
    reg_stamp = "document_RegStamp.png"
    link_type = False
    sign_types = ("Утверждающая", "Визирующая")
    container_signature = False  # в эталонах 2.7 подписи ТК нет

    def sign(self, i):
        return f"document_{i + 1}_SigStamp.sig", f"document_{i + 1}_SigStamp.png"

    def attachment(self, idx, ext):
        return f"document_att{idx}.{ext}", f"document_att{idx}.sig"


def build_container(ctx, doc, extra=()):
    return v271.build_container(ctx, doc, extra, Dialect())


def message(ctx, mtype, uid, receivers, version=VERSION, **kw):
    return v271.message(ctx, mtype, uid, receivers, ns=NS_M, version=version, **kw)

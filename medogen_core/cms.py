"""Тестовая подпись CMS/PKCS#7 (detached, SHA-256 + RSA) без сторонних библиотек.

Подпись криптографически корректна, но сделана ключом тестового самоподписанного
сертификата «MEDOGEN TEST SIGNER - NOT FOR PRODUCTION»: проверки формата
(«файл похож на CMS/PKCS#7») она проходит, проверку доверия и ГОСТ — нет.
Ключ встроен сюда намеренно: он ничего не защищает и нужен только для тестов.
"""
import base64
import datetime as dt
import hashlib

CERT = (
    "MIIDVDCCAjygAwIBAgIUYmWNgJG2lBh/z3rP411zVmBxhYcwDQYJKoZIhvcNAQELBQAwUjELMAkGA1UEBhMCUlUx"
    "EDAOBgNVBAoMB21lZG9nZW4xMTAvBgNVBAMMKE1FRE9HRU4gVEVTVCBTSUdORVIgLSBOT1QgRk9SIFBST0RVQ1RJ"
    "T04wIBcNMjYwMTAxMDAwMDAwWhgPMjA1NTEyMjUwMDAwMDBaMFIxCzAJBgNVBAYTAlJVMRAwDgYDVQQKDAdtZWRv"
    "Z2VuMTEwLwYDVQQDDChNRURPR0VOIFRFU1QgU0lHTkVSIC0gTk9UIEZPUiBQUk9EVUNUSU9OMIIBIjANBgkqhkiG"
    "9w0BAQEFAAOCAQ8AMIIBCgKCAQEAhqLEL0DGgU/XYbSKFH0qCiANRs9dy8XswFlx9mECREIdE7707vWOypDGTOF/"
    "UAlTke9oY36Cpt2U+Zei/OiwT/w/H+/KudoI0RzJfYAwBVONAxcYq+Hiz4e+HTUotVhboQH//lXAtzocsdAPtmgr"
    "UrbXyZE0xlMxCqZJUPcg3SHJQzsd66fPSx9NnzsvSPhZpnx3N6P+/ykVVmAK5CVrthFp8ydqdZyv3gIyYjcyWP24"
    "O2/hwARMhsdURqY5n6gT1qWWQfGqOr2Zw5Jujzzo/qKnRYq6UtIw9jI74KKLHkljNTUWbWgf0s2EyQihQK3AEwai"
    "j0JSFqS6HYpoTaXCpwIDAQABoyAwHjAMBgNVHRMBAf8EAjAAMA4GA1UdDwEB/wQEAwIGwDANBgkqhkiG9w0BAQsF"
    "AAOCAQEAgo9IGcLvhrxmfK/MgZ1ylQrtckJJzb/cjqJlggVk8v5xSxzEupXJSOqVMIfPn1KKZWZqvRd5hVRfZ6aD"
    "reB7JC/TqTFDoNCpxc0fZckmz4jd2AMlUVRdNMVVxwOOZ+lDDyjVwjIDYggnseYU+N1ZjPgySHJuo03+uvpc0M71"
    "J7HYeDhP+XnhnywDk5QAqKxs3hLqVcY9GmwtTO0t9mi62GHTemEribMDT8uhhez2rXpB+O27HWBFVSbcdkPn1bj1"
    "aB2vaBL9L2DgI6IpqSx1hUrdxMgvYIvYjBmROQHGnWeJhcoLwloRz/skCogSkEZBg5JPf8ai+JRwPl2KYyDEtA=="
)
SERIAL = 561745797387933271541762509696071970012716434823
ISSUER = bytes.fromhex("3052310b30090603550406130252553110300e060355040a0c076d65646f67656e3131302f06035504030c284d45444f47454e2054455354205349474e4552202d204e4f5420464f522050524f44554354494f4e")
N = int(
    "86a2c42f40c6814fd761b48a147d2a0a200d46cf5dcbc5ecc05971f6610244421d13bef4eef58eca90c64ce1"
    "7f50095391ef68637e82a6dd94f997a2fce8b04ffc3f1fefcab9da08d11cc97d803005538d031718abe1e2cf"
    "87be1d3528b5585ba101fffe55c0b73a1cb1d00fb6682b52b6d7c99134c653310aa64950f720dd21c9433b1d"
    "eba7cf4b1f4d9f3b2f48f859a67c7737a3feff291556600ae4256bb61169f3276a759cafde023262373258fd"
    "b83b6fe1c0044c86c75446a6399fa813d6a59641f1aa3abd99c3926e8f3ce8fea2a7458aba52d230f6323be0"
    "a28b1e49633535166d681fd2cd84c908a140adc01306a28f425216a4ba1d8a684da5c2a7"
    , 16)
D = int(
    "1e88e8c4acc4b88c32957abc3c445cd5fa09f8cecbff8cae92bb5680e1e481359b61a093ed642cd3dd3cd42f"
    "ea4f6d261279812bdb83e73366f7aa7ff12b16ddb5205dee65047ca23646f89da9867a96c5eaf70085ef0fa1"
    "30b099f03600d6fec507ee96b0bd29f11893ed53baf36a2bf2c302f8974709941c4db560130455926155f28a"
    "5abdf1d9f3dd55d235fb007ca29c5e83575d422a0a2553bd0c74720a0f245e27c3f2de37033064943fe82085"
    "f495a3a4198a236d21e168b961afc75805c0cecb1ed7491703c728ffb1b1999069f11ece5ae9f3979137a0bf"
    "ee02ae090269375bb58ea66bee93350c77fa1bc6847cb71bf5fb7c3119c551cbdb38a0c5"
    , 16)
E = 65537


def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, body):
    return bytes([tag]) + _len(len(body)) + body


def _seq(*items):
    return _tlv(0x30, b"".join(items))


def _set(*items):
    return _tlv(0x31, b"".join(sorted(items)))  # DER: элементы SET OF по возрастанию


def _int(v):
    b = v.to_bytes(v.bit_length() // 8 + 1, "big")
    return _tlv(0x02, b)


def _oid(dotted):
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


NULL = b"\x05\x00"
OID_DATA = "1.2.840.113549.1.7.1"
OID_SIGNED = "1.2.840.113549.1.7.2"
SHA256 = _seq(_oid("2.16.840.1.101.3.4.2.1"), NULL)
RSA = _seq(_oid("1.2.840.113549.1.1.1"), NULL)
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")  # префикс DigestInfo для SHA-256


def _rsa_sign(data):
    k = (N.bit_length() + 7) // 8
    t = DIGEST_INFO + hashlib.sha256(data).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), D, N).to_bytes(k, "big")


def sign(data, when=None):
    """Отсоединённая подпись CMS SignedData (DER) для data."""
    when = when or dt.datetime.now(dt.timezone.utc)
    utc = when.astimezone(dt.timezone.utc).strftime("%y%m%d%H%M%SZ").encode()
    attrs = [
        _seq(_oid("1.2.840.113549.1.9.3"), _set(_oid(OID_DATA))),               # contentType
        _seq(_oid("1.2.840.113549.1.9.5"), _set(_tlv(0x17, utc))),               # signingTime
        _seq(_oid("1.2.840.113549.1.9.4"), _set(_tlv(0x04, hashlib.sha256(data).digest()))),  # messageDigest
    ]
    signed_attrs = _set(*attrs)  # подписывается как SET (тег 0x31)
    signature = _rsa_sign(signed_attrs)
    signer_info = _seq(
        _int(1),
        _seq(ISSUER, _int(SERIAL)),
        SHA256,
        b"\xa0" + signed_attrs[1:],  # [0] IMPLICIT
        RSA,
        _tlv(0x04, signature),
    )
    cert = base64.b64decode("".join(CERT))
    signed_data = _seq(
        _int(1),
        _set(SHA256),
        _seq(_oid(OID_DATA)),        # содержимое отсоединено
        _tlv(0xA0, cert),            # certificates [0] IMPLICIT
        _set(signer_info),
    )
    return _seq(_oid(OID_SIGNED), _tlv(0xA0, signed_data))

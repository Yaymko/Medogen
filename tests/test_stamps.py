"""Картинки штампов: штамп ЭП показывает данные сертификата из подписи, штамп регистрации —
дату и номер документа. Сверка с эталонами 2.7.1 «Входящее письмо» и 3.0 «ДЭВ без digital»."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

REF_DIR = os.environ.get("MEDO_REF_DIR")
ROOT = os.environ.get("MEDOGEN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from medogen_core import certinfo, cms, stamps  # noqa: E402
from medogen_core.common import stub_png  # noqa: E402


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "не PNG"
    return struct.unpack(">II", data[16:24])


def png_pixels(data):
    """Декодирует 8-битный PNG без чересстрочности (тип 0, 2 или 4) в строки яркости на белом фоне."""
    w, h = png_size(data)
    ctype = data[25]
    bpp = {0: 1, 2: 3, 4: 2}[ctype]
    pos, idat = 8, b""
    while pos < len(data):
        ln, tag = struct.unpack(">I4s", data[pos:pos + 8])
        if tag == b"IDAT":
            idat += data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if ctype == 4:  # серый + альфа: накладываем на белый
            rows.append(bytes(255 - a * (255 - g) // 255 for g, a in zip(line[::2], line[1::2])))
        else:
            rows.append(bytes(line[::bpp]))
        prev = line
    return rows


def dark_share(data):
    rows = png_pixels(data)
    dark = sum(1 for r in rows for v in r if v < 128)
    return dark / (len(rows) * len(rows[0]))


class CertInfo(unittest.TestCase):
    def test_reads_serial_and_validity_from_test_signature(self):
        info = certinfo.from_signature(cms.sign(b"data"))
        self.assertEqual(info["serial"], "62658D8091B694187FCF7ACFE35D735660718587")
        self.assertEqual(info["not_before"], "01.01.2026")
        self.assertEqual(info["not_after"], "25.12.2055")
        self.assertEqual(info["owner"], "MEDOGEN TEST SIGNER - NOT FOR PRODUCTION")
        self.assertTrue(info["test"])

    def test_owner_is_surname_and_given_name_when_present(self):
        # в квалифицированных сертификатах ФИО лежит в SN (фамилия) и G (имя отчество)
        name = certinfo.parse_name(certinfo.build_name([("2.5.4.3", "ООО Ромашка"),
                                                       ("2.5.4.4", "Антонов"),
                                                       ("2.5.4.42", "Сергей Анатольевич")]))
        self.assertEqual(certinfo.owner(name), "Антонов Сергей Анатольевич")

    def test_owner_falls_back_to_common_name(self):
        name = certinfo.parse_name(certinfo.build_name([("2.5.4.3", "Петров Петр Петрович")]))
        self.assertEqual(certinfo.owner(name), "Петров Петр Петрович")

    def test_serial_keeps_leading_zero_of_first_byte(self):
        # серийный номер показывается побайтно, как в эталонном штампе 3.0: 065EAD99…
        self.assertEqual(certinfo.serial_hex(bytes.fromhex("065EAD99")), "065EAD99")
        self.assertEqual(certinfo.serial_hex(bytes.fromhex("00FF10")), "FF10")  # 00 — знаковый байт DER

    @unittest.skipUnless(REF_DIR, "MEDO_REF_DIR не задана: подписи эталонов не проверяются")
    def test_reference_signature_matches_reference_stamp(self):
        path = os.path.join(REF_DIR, "3.0", "ДЭВ без digital", "document.edc.zip")
        info = certinfo.from_signature(zipfile.ZipFile(path).read("document_sign_0.p7s"))
        self.assertEqual(info["serial"], "065EAD990062B4959149BB545F40AACE23")
        self.assertEqual(info["owner"], "Антонов Сергей Анатольевич")
        self.assertEqual((info["not_before"], info["not_after"]), ("07.06.2026", "07.09.2026"))

    def test_garbage_gives_none(self):
        self.assertIsNone(certinfo.from_signature(b"not a signature"))


class StampText(unittest.TestCase):
    def test_signature_lines_follow_reference(self):
        info = {"serial": "065EAD99", "owner": "Антонов Сергей Анатольевич",
                "not_before": "07.06.2026", "not_after": "07.09.2026", "test": False}
        self.assertEqual(stamps.signature_lines(info, "Другое Имя"), [
            "Сертификат: 065EAD99",
            "Владелец: Антонов Сергей Анатольевич",
            "Действителен с 07.06.2026 до 07.09.2026",
        ])

    def test_test_certificate_shows_signer_from_scenario(self):
        info = certinfo.from_signature(cms.sign(b"x"))
        self.assertIn("Владелец: Иванов Иван Иванович", stamps.signature_lines(info, "Иванов Иван Иванович"))

    def test_unreadable_signature_still_names_signer(self):
        self.assertEqual(stamps.signature_lines(None, "Иванов И. И."), [
            "Сертификат: —", "Владелец: Иванов И. И.", "Действителен с — до —"])

    def test_registration_lines(self):
        self.assertEqual(stamps.registration_lines("2025-01-30", "025"), ["30.01.2025 № 025"])

    def test_registration_lines_with_link(self):
        self.assertEqual(stamps.registration_lines("2025-01-30", "025", ("17-И", "2025-01-20")),
                         ["30.01.2025 № 025", "На № 17-И от 20.01.2025"])

    def test_font_covers_all_used_characters(self):
        text = ("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,:;-—–№/()\"«»'_")
        missing = [c for c in text if not stamps.has_glyph(c)]
        self.assertEqual(missing, [])


class StampImage(unittest.TestCase):
    INFO = {"serial": "065EAD990062B4959149BB545F40AACE23", "owner": "Антонов Сергей Анатольевич",
            "not_before": "07.06.2026", "not_after": "07.09.2026", "test": False}

    def test_signature_stamp_keeps_aspect_of_stamp_box(self):
        w, h = png_size(stamps.signature_stamp(self.INFO, "x", 41, 18))
        self.assertAlmostEqual(w / h, 41 / 18, delta=0.05)
        self.assertGreaterEqual(w, 600)

    def test_registration_stamp_keeps_aspect_of_stamp_box(self):
        w, h = png_size(stamps.registration_stamp("2026-06-07", "П-МО-96", None, 38, 10))
        self.assertAlmostEqual(w / h, 38 / 10, delta=0.05)

    def test_stamp_has_text_not_just_a_frame(self):
        img = stamps.signature_stamp(self.INFO, "x", 41, 18)
        self.assertGreater(dark_share(img), 0.06)
        other = stamps.signature_stamp({**self.INFO, "owner": "Босов Павел Владимирович"}, "x", 41, 18)
        self.assertNotEqual(img, other)

    def test_registration_stamp_has_text(self):
        self.assertGreater(dark_share(stamps.registration_stamp("2026-06-07", "П-МО-96", None, 38, 10)), 0.04)


def run_cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "medogen.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


class StampsInPackages(unittest.TestCase):
    """Штампы в собранных пакетах — не заглушки, а картинки с данными подписи и регистрации."""
    FORMATS = {"3.0": ("document_sign_0.p7s", "sign_stamp_0.png", "reg_stamp.png"),
               "2.7.1": ("document_sign_0.p7s", "sign_stamp_0.png", "reg_stamp.png"),
               "2.7": ("document_1_SigStamp.sig", "document_1_SigStamp.png", "document_RegStamp.png")}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        doc = {"reg_number": "ИСХ-42", "reg_date": "2026-06-07",
               "signer": {"post": "Министр", "name": "Сидоров Сидор Сидорович"}}
        scen = {"packages": [{"name": f"f{v.replace('.', '')}", "format": v, "type": "document",
                              "from": "pravit_mo", "to": ["mo_duma"], "document": doc}
                             for v in cls.FORMATS]}
        path = os.path.join(cls.tmp.name, "s.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(scen, f, ensure_ascii=False)
        cls.out = os.path.join(cls.tmp.name, "out")
        r = run_cli("build", path, "--out", cls.out)
        if r.returncode != 0:
            raise AssertionError(f"сборка не удалась:\n{r.stdout}\n{r.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def container(self, version):
        key = f"_f{version.replace('.', '')}"
        folder = next(os.path.join(self.out, n) for n in os.listdir(self.out) if n.endswith(key) or key + "_" in n)
        zname = next(n for n in os.listdir(folder) if n.endswith(".zip"))
        z = zipfile.ZipFile(os.path.join(folder, zname))
        return {n: z.read(n) for n in z.namelist()}

    def test_signature_stamp_is_rendered_from_signature(self):
        for v, (sig, stamp, _) in self.FORMATS.items():
            with self.subTest(format=v):
                files = self.container(v)
                info = certinfo.from_signature(files[sig])
                self.assertIsNotNone(info)
                self.assertEqual(files[stamp], stamps.signature_stamp(info, "Сидоров Сидор Сидорович", 41, 18))
                self.assertNotEqual(files[stamp], stub_png(300, 130))

    def test_registration_stamp_shows_number_and_date(self):
        for v, (_, _, reg) in self.FORMATS.items():
            with self.subTest(format=v):
                self.assertEqual(self.container(v)[reg],
                                 stamps.registration_stamp("2026-06-07", "ИСХ-42", None, 38, 10))


if __name__ == "__main__":
    unittest.main()

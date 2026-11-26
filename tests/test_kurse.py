"""Tests für scraper.kurse (ohne Netz)."""
import datetime as dt
import unittest

from scraper import kurse

LISTE = """## Quelle: https://fb.example-attrappe.koeln/liste
★ Eltern-Kind-Kurse
★ Musikzwerge für Kinder von 1-3 Jahren
★ Di. 12.01.2027 - Di. 23.03.2027
★ 10x | 10.00 - 10.45 Uhr
★ Ort
★ Pfarrsaal St. Urban
★ 51063 Köln
★ Gebühr
★ 85,00 €
★ 2643000001
★ Babytreff für Kinder geboren 08-10/26
★ Mo. 11.01.2027 - Mo. 15.02.2027
★ 6x | 9.30 - 11.00 Uhr
★ gebührenfrei
★ 2643000002
"""


class KurseTest(unittest.TestCase):
    def test_parse_und_kurs(self):
        xs = kurse.parse_webbasys(LISTE, None)
        self.assertEqual([x["nr"] for x in xs], ["2643000001", "2643000002"])
        heute = dt.date(2026, 11, 26)
        k = kurse.kurs("ff", "FF", xs[0], heute)
        self.assertEqual((k["art"], k["mmin"], k["mmax"], k["pk"], k["wd"], k["t"], k["termine"]),
                         ("Musik", 12, 47, 85, [2], "10:00–10:45", 10))
        self.assertEqual(k["veedel"], "Mülheim")
        b = kurse.kurs("ff", "FF", xs[1], heute)
        self.assertEqual((b["pk"], b["mmin"], b["mmax"], b["place"]), (0, 3, 11, "Pfarrsaal St. Urban 51063 Köln"))

    def test_vorbei(self):
        x = kurse.parse_webbasys(LISTE, None)[0]
        self.assertIsNone(kurse.kurs("ff", "FF", x, dt.date(2027, 3, 1)))


if __name__ == "__main__":
    unittest.main()

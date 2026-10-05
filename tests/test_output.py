import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from yota_parser.output import RESULT_HEADER, write_top_tariffs
from yota_parser.cli import main


class TopTariffsTests(unittest.TestCase):
    def test_numeric_sort_limit_latest_record_and_stock(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "tariffs.txt"
            target = Path(folder) / "top_tariffs.txt"
            with source.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file, delimiter="\t")
                writer.writerow(RESULT_HEADER)
                for number in range(1, 16):
                    writer.writerow([100 + number, 2, f"https://archive.yota.ru/tariff/{number}"])
                writer.writerow([900, 9, "https://archive.yota.ru/tariff/1"])
                writer.writerow(["80,50", 3, "https://archive.yota.ru/tariff/2"])
                writer.writerow(["80.50", 15, "https://archive.yota.ru/tariff/3"])
                writer.writerow([1, 0, "https://archive.yota.ru/tariff/4"])
                writer.writerow(["NaN", 9, "https://archive.yota.ru/tariff/16"])
                writer.writerow(["bad row"])
                writer.writerow(RESULT_HEADER)
            original = source.read_bytes()
            self.assertEqual(write_top_tariffs(source, target, limit=10), 10)
            self.assertEqual(source.read_bytes(), original)
            with target.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file, delimiter="\t"))
            self.assertEqual(rows[0], RESULT_HEADER)
            self.assertEqual(rows[1], ["80.50", "15", "https://archive.yota.ru/tariff/3"])
            self.assertEqual(rows[2], ["80.50", "3", "https://archive.yota.ru/tariff/2"])
            ids = [int(row[2].rsplit("/", 1)[1]) for row in rows[1:]]
            self.assertEqual(ids, [3, 2, 5, 6, 7, 8, 9, 10, 11, 12])

    def test_fewer_than_requested_and_many_stock(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "tariffs.txt"
            target = Path(folder) / "top_tariffs.txt"
            source.write_text("100\t2\thttps://archive.yota.ru/tariff/1\n"
                              "100\tмного\thttps://archive.yota.ru/tariff/2\n", encoding="utf-8")
            self.assertEqual(write_top_tariffs(source, target), 2)
            with target.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file, delimiter="\t"))
            self.assertEqual(rows[1][1], "много")
            source.write_text("", encoding="utf-8")
            self.assertEqual(write_top_tariffs(source, target), 0)

    def test_top_is_generated_after_both_parser_modes_finish(self):
        for mode in ("api", "browser"):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                source = Path(folder) / "tariffs.txt"
                source.write_text("".join(f"{100 + number}\t9\thttps://archive.yota.ru/tariff/{number}\n"
                                          for number in range(1, 8)), encoding="utf-8")
                with patch("sys.argv", ["yota_parser.py", "--start", "305", "--end", "305",
                                        "--mode", mode, "--output", str(source), "--top-count", "3"]), patch("builtins.print"), patch("yota_parser.api.run_api", new_callable=AsyncMock, return_value=0), patch("yota_parser.cli.run_browser", return_value=0):
                    self.assertEqual(main(), 0)
                with (Path(folder) / "top_tariffs.txt").open(encoding="utf-8-sig", newline="") as file:
                    rows = list(csv.reader(file, delimiter="\t"))
                self.assertEqual(len(rows), 4)
                self.assertEqual([row[0] for row in rows[1:]], ["101", "102", "103"])


if __name__ == "__main__":
    unittest.main()

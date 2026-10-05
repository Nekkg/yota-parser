import asyncio
import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from yota_parser.api import AccessDeniedError, NetworkError, check_api_access, extract_tariff, fetch_tariff, run_api


def payload(tariff_id=305, quantity=9, unlimited=True, many=False, price=172.43):
    return {"promocode": {"promoCodePack": {"promoCodePackId": tariff_id,
                                          "quantity": quantity, "isUnlimited": many},
                          "ratePlan": {"price": {"cost": price},
                                       "resource": {"internet": {"flagUnlimited": unlimited}}}}}


class ExtractTests(unittest.TestCase):
    def test_stock_and_price(self):
        for quantity in (1, 9, 25):
            result = extract_tariff(payload(quantity=quantity), 305)
            self.assertEqual(result, {"status": "match", "price": "172.43", "remaining": quantity})

    def test_no_stock_limited_and_many(self):
        self.assertEqual(extract_tariff(payload(quantity=0), 305)["status"], "no_stock")
        self.assertEqual(extract_tariff(payload(unlimited=False), 305)["status"], "limited")
        self.assertEqual(extract_tariff(payload(quantity=0, many=True), 305)["remaining"], "много")

    def test_bad_data_and_wrong_id_are_errors(self):
        for item in ({}, payload(tariff_id=306), payload(quantity="9"), payload(price="NaN")):
            with self.assertRaises(ValueError):
                extract_tariff(item, 305)


def response(status, body, headers=None):
    return SimpleNamespace(status_code=status, content=json.dumps(body).encode(), headers=headers or {})


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def limiter(self):
        return SimpleNamespace(wait=AsyncMock(), cooldown=lambda seconds: self.cooldowns.append(seconds))

    def setUp(self):
        self.cooldowns = []
        self.counters = {"requests": 0, "rate_limited": 0}

    async def test_missing_and_forbidden_are_different(self):
        client = SimpleNamespace(get=AsyncMock(return_value=response(404, {"code": 110414})))
        self.assertEqual((await fetch_tariff(client, 305, self.limiter(), 2, self.counters))["status"], "missing")
        client.get.return_value = response(403, {"code": 403})
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            await fetch_tariff(client, 305, self.limiter(), 2, self.counters)

    async def test_html_403_is_access_failure_before_json_parsing(self):
        client = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
            status_code=403, content=b'<html><h1>403 Forbidden</h1></html>',
            headers={"Content-Type": "text/html"})))
        with self.assertRaisesRegex(AccessDeniedError, "сервер Yota отказал"):
            await fetch_tariff(client, 305, self.limiter(), 2, self.counters)
        self.assertEqual(client.get.await_count, 1)

    async def test_preflight_retries_connection_timeout_then_succeeds(self):
        expected = {"status": "match", "price": "172.43", "remaining": 9}
        with patch("yota_parser.api.fetch_tariff", new_callable=AsyncMock,
                   side_effect=[NetworkError("connect timeout"), expected]) as fetch, patch("yota_parser.api.asyncio.sleep", new_callable=AsyncMock), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(await check_api_access(None, 305, self.limiter(), 2, self.counters), expected)
            self.assertEqual(fetch.await_count, 2)

    async def test_preflight_does_not_retry_403(self):
        with patch("yota_parser.api.fetch_tariff", new_callable=AsyncMock,
                   side_effect=AccessDeniedError("HTTP 403")) as fetch:
            with self.assertRaises(AccessDeniedError):
                await check_api_access(None, 305, self.limiter(), 2, self.counters)
            self.assertEqual(fetch.await_count, 1)

    async def test_preflight_stops_after_network_retries_exhausted(self):
        with patch("yota_parser.api.fetch_tariff", new_callable=AsyncMock,
                   side_effect=NetworkError("connect timeout")) as fetch, patch("yota_parser.api.asyncio.sleep", new_callable=AsyncMock), patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(NetworkError):
                await check_api_access(None, 305, self.limiter(), 2, self.counters)
            self.assertEqual(fetch.await_count, 3)

    async def test_failed_preflight_preserves_both_existing_files(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / "results.txt"
            errors = Path(folder) / "results.errors.txt"
            output.write_text("previous tariffs", encoding="utf-8")
            errors.write_text("previous errors", encoding="utf-8")
            args = SimpleNamespace(start=300, end=999, rate=5, workers=3, retries=2,
                                   timeout=5, output=output)
            with patch("yota_parser.api.fetch_tariff", new_callable=AsyncMock,
                       side_effect=AccessDeniedError("HTTP 403")) as fetch, patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(await run_api(args), 1)
                self.assertEqual(fetch.await_count, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous tariffs")
            self.assertEqual(errors.read_text(encoding="utf-8"), "previous errors")

    async def test_403_mid_scan_stops_remaining_ids(self):
        seen = []

        async def fake_fetch(client, tariff_id, limiter, retries, counters):
            await asyncio.sleep(0)
            seen.append(tariff_id)
            if tariff_id >= 4:
                raise AccessDeniedError("HTTP 403")
            return {"status": "match", "price": "172.43", "remaining": tariff_id}

        with TemporaryDirectory() as folder:
            args = SimpleNamespace(start=1, end=700, rate=5, workers=3, retries=2,
                                   timeout=5, output=Path(folder) / "results.txt")
            with patch("yota_parser.api.fetch_tariff", side_effect=fake_fetch), patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(await run_api(args), 1)
            self.assertLess(len(seen), 15)
            with args.output.open(encoding="utf-8-sig") as output:
                rows = list(csv.reader(output, delimiter="\t"))
            self.assertEqual(len(rows), 4)  # заголовок и первые 3 результата

    async def test_429_retries_with_shared_cooldown(self):
        client = SimpleNamespace(get=AsyncMock(side_effect=[response(429, {}, {"Retry-After": "3"}),
                                                          response(200, payload())]))
        result = await fetch_tariff(client, 305, self.limiter(), 2, self.counters)
        self.assertEqual(result["remaining"], 9)
        self.assertEqual(self.cooldowns, [3])
        self.assertEqual(self.counters, {"requests": 2, "rate_limited": 1})

    async def test_server_error_retries_and_exhaustion(self):
        client = SimpleNamespace(get=AsyncMock(side_effect=[response(503, {}), response(200, payload())]))
        with patch("yota_parser.api.asyncio.sleep", new_callable=AsyncMock):
            self.assertEqual((await fetch_tariff(client, 305, self.limiter(), 1, self.counters))["status"], "match")
            client.get = AsyncMock(return_value=response(503, {}))
            with self.assertRaisesRegex(RuntimeError, "после 2 попыток"):
                await fetch_tariff(client, 305, self.limiter(), 1, self.counters)

    async def test_redirect_never_fetches_wrong_tariff(self):
        client = SimpleNamespace(get=AsyncMock())
        self.assertEqual((await fetch_tariff(client, 844, self.limiter(), 2, self.counters))["status"], "redirect")
        client.get.assert_not_awaited()

    async def test_parallel_scan_does_not_lose_ids_and_records_failures(self):
        seen = []

        async def fake_fetch(client, tariff_id, limiter, retries, counters):
            await asyncio.sleep(0)
            seen.append(tariff_id)
            if tariff_id == 8:
                raise RuntimeError("HTTP 403")
            return {"status": "match", "price": "172.43", "remaining": tariff_id}

        with TemporaryDirectory() as folder:
            args = SimpleNamespace(start=1, end=20, rate=120, workers=5, retries=2,
                                   timeout=5, output=Path(folder) / "results.txt")
            with patch("yota_parser.api.fetch_tariff", side_effect=fake_fetch), patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
                code = await run_api(args)
            self.assertEqual(code, 1)
            self.assertEqual(sorted(seen), list(range(1, 21)))
            with args.output.open(encoding="utf-8-sig") as output:
                rows = list(csv.reader(output, delimiter="\t"))
            self.assertEqual(len(rows), 20)  # заголовок + 19 совпадений
            self.assertIn("8\tHTTP 403", (Path(folder) / "results.errors.txt").read_text(encoding="utf-8-sig"))

    async def test_multiple_runs_append_and_preserve_existing_rows(self):
        async def fake_fetch(client, tariff_id, limiter, retries, counters):
            return {"status": "match", "price": "172.43", "remaining": 9}

        with TemporaryDirectory() as folder:
            output = Path(folder) / "results.txt"
            # Файл без последнего переноса строки: новая запись должна остаться отдельной строкой.
            original = "Цена, руб.\tОсталось, шт.\tСсылка\r\n100\t2\thttps://archive.yota.ru/tariff/10".encode("utf-8-sig")
            output.write_bytes(original)
            args = SimpleNamespace(start=305, end=305, rate=5, workers=3, retries=2,
                                   timeout=5, output=output)
            with patch("yota_parser.api.fetch_tariff", side_effect=fake_fetch), patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(await run_api(args), 0)
                args.start = args.end = 306
                self.assertEqual(await run_api(args), 0)
            data = output.read_bytes()
            self.assertTrue(data.startswith(original))
            self.assertEqual(data.count(b"\xef\xbb\xbf"), 1)
            with output.open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file, delimiter="\t"))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1], ["100", "2", "https://archive.yota.ru/tariff/10"])
            self.assertEqual(rows[2][2], "https://archive.yota.ru/tariff/305")
            self.assertEqual(rows[3][2], "https://archive.yota.ru/tariff/306")


if __name__ == "__main__":
    unittest.main()

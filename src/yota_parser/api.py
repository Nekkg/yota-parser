"""Быстрый перебор через тот же JSON API, который использует сайт Yota."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import json
import sys
import time

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from .output import append_results

API_URL = "https://www.yota.ru/yws-api/promocodes/{tariff_id}?type=pack"
# Эти адреса перенаправляет React Router сайта: как и браузерный режим,
# пропускаем их, чтобы не сохранять другой тариф под исходным номером.
REDIRECT_IDS = {844, 845, 23835, 23836, 23838, 23839, 23841}

# Заголовки соответствуют запросу из JavaScript archive.yota.ru.
API_HEADERS = {"Accept": "application/json", "Content-Type": "application/json",
               "Origin": "https://archive.yota.ru", "Referer": "https://archive.yota.ru/"}


class AccessDeniedError(RuntimeError):
    """Отказ сервера в доступе: продолжать массовый обход нельзя."""


class NetworkError(RuntimeError):
    """Сбой DNS, подключения или передачи: допустима повторная попытка."""


def extract_tariff(payload, tariff_id):
    """Повторяет преобразование v1 и условия отображения тарифной карточки."""
    try:
        tariff = payload["promocode"]
        pack = tariff["promoCodePack"]
        plan = tariff["ratePlan"]
        if str(pack["promoCodePackId"]) != str(tariff_id):
            raise ValueError("API вернул другой номер тарифа")
        unlimited = plan["resource"]["internet"]["flagUnlimited"]
        if not isinstance(unlimited, bool):
            raise ValueError("Не распознан признак безлимитных ГБ")
        if not unlimited:
            return {"status": "limited"}
        many = pack.get("isUnlimited", False)
        if not isinstance(many, bool):
            raise ValueError("Не распознан признак остатка")
        quantity = pack.get("quantity")
        quantity = 0 if quantity is None else quantity
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise ValueError("Не распознан остаток")
        if not many and quantity == 0:
            return {"status": "no_stock"}
        price = Decimal(str(plan["price"]["cost"]))
        if not price.is_finite() or price < 0:
            raise ValueError("Не распознана цена")
        return {"status": "match", "price": format(price, "f"),
                "remaining": "много" if many else quantity}
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError("Неожиданная структура JSON тарифа") from exc


class RateLimiter:
    def __init__(self, rate):
        self.rate = rate
        self.next_start = 0
        self.blocked_until = 0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                delay = max(self.next_start, self.blocked_until) - now
                if delay <= 0:
                    # Компенсируем округление таймеров Windows. Перенос слота
                    # от текущего времени терял бы скорость на каждом sleep.
                    # Допускается максимум один накопленный слот после паузы.
                    self.next_start = max(self.next_start + 1 / self.rate, now)
                    return
                await asyncio.sleep(delay)

    def cooldown(self, seconds):
        self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)
        self.rate = max(1, self.rate / 2)


def retry_after_seconds(value, fallback):
    try:
        seconds = float(value)
        if seconds >= 0 and seconds < float("inf"):
            return seconds
    except (TypeError, ValueError):
        pass
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, (date - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return fallback


async def fetch_tariff(client, tariff_id, limiter, retries, counters):
    if tariff_id in REDIRECT_IDS:
        return {"status": "redirect"}
    for attempt in range(retries + 1):
        await limiter.wait()
        counters["requests"] += 1
        try:
            response = await client.get(API_URL.format(tariff_id=tariff_id))
        except RequestException as exc:
            if attempt == retries:
                raise NetworkError(f"{type(exc).__name__}: {exc}") from exc
            await asyncio.sleep(0.5 * 2 ** attempt)
            continue
        if response.status_code in (401, 403):
            content_type = response.headers.get("Content-Type", "не указан")
            raise AccessDeniedError(f"HTTP {response.status_code}: сервер Yota отказал в доступе "
                                    f"(Content-Type: {content_type}). Это не отсутствие тарифа.")
        if response.status_code in (429, 500, 502, 503, 504):
            if response.status_code == 429:
                counters["rate_limited"] += 1
                limiter.cooldown(max(0.5, retry_after_seconds(response.headers.get("Retry-After"), 5)))
            if attempt == retries:
                raise RuntimeError(f"HTTP {response.status_code} после {attempt + 1} попыток")
            if response.status_code != 429:
                await asyncio.sleep(retry_after_seconds(response.headers.get("Retry-After"), 0.5 * 2 ** attempt))
            continue
        try:
            payload = json.loads(response.content, parse_float=Decimal)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError(f"HTTP {response.status_code}: ответ не является JSON") from exc
        # Только известный код отсутствующего тарифа считается пропуском.
        # 403, ошибки авторизации и другие сбои не маскируются под отсутствие.
        if response.status_code in (200, 400, 404, 410) and isinstance(payload, dict) and payload.get("code") == 110414:
            return {"status": "missing"}
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {str(payload)[:200]}")
        return extract_tariff(payload, tariff_id)


async def check_api_access(client, tariff_id, limiter, retries, counters):
    # Один запрос одновременно. Отказы HTTP 401/403 сюда не попадают:
    # повторяем только сетевые сбои, не запуская массовый обход.
    for attempt in range(retries + 1):
        try:
            return await fetch_tariff(client, tariff_id, limiter, 0, counters)
        except NetworkError:
            if attempt == retries:
                raise
            delay = 0.5 * 2 ** attempt
            print(f"Сбой соединения с Yota. Повтор проверки {attempt + 1}/{retries} "
                  f"через {delay:g} с...", flush=True)
            await asyncio.sleep(delay)


async def run_api(args):
    total = args.end - args.start + 1
    counters = {"done": 0, "found": 0, "errors": 0, "requests": 0, "rate_limited": 0}
    limiter = RateLimiter(args.rate)
    ids = iter(range(args.start, args.end + 1))
    started = time.monotonic()
    error_path = args.output.with_name(args.output.stem + ".errors.txt")

    def progress():
        elapsed = max(0.001, time.monotonic() - started)
        print(f"Проверено {counters['done']}/{total}; найдено {counters['found']}; "
              f"ошибок {counters['errors']}; скорость {counters['done'] / elapsed:.1f} тарифов/с; "
              f"HTTP 429: {counters['rate_limited']}", flush=True)

    async def report():
        while True:
            await asyncio.sleep(1)
            progress()

    print("Проверка доступа к API перед перебором...", flush=True)
    connect_timeout = min(getattr(args, "connect_timeout", 5), args.timeout)
    # curl_cffi складывает два значения в общий таймаут запроса.
    timeout = (connect_timeout, args.timeout - connect_timeout)
    async with AsyncSession(max_clients=args.workers, timeout=timeout,
                            impersonate="chrome", headers=API_HEADERS) as client:
        probe_id = next((i for i in range(args.start, args.end + 1) if i not in REDIRECT_IDS), None)
        cache = {}
        if probe_id is not None:
            try:
                cache[probe_id] = await check_api_access(client, probe_id, limiter, args.retries, counters)
            except (RuntimeError, ValueError) as exc:
                print(f"Проверка доступа не пройдена: {exc}\n"
                      "Перебор не начат. Предыдущие файлы результатов и ошибок сохранены.\n"
                      "Проверьте, отображается ли сам тариф в браузере. При отказе сервера "
                      "все запросы будут ошибочными независимо от скорости перебора.", file=sys.stderr, flush=True)
                return 1
        print(f"Режим API: до {args.rate:g} запросов/с, параллельно до {args.workers}. "
              "Фактическая скорость зависит от ответов сервера.", flush=True)
        return await scan_available_api(client, args, ids, cache, counters, limiter,
                                        started, error_path, progress, report)


async def scan_available_api(client, args, ids, cache, counters, limiter,
                             started, error_path, progress, report):
    error_output = None
    blocked = False
    with append_results(args.output) as (output, writer):

        async def worker():
            nonlocal error_output
            for tariff_id in ids:
                try:
                    if tariff_id in cache:
                        result = cache.pop(tariff_id)
                    else:
                        result = await fetch_tariff(client, tariff_id, limiter, args.retries, counters)
                    if result["status"] == "match":
                        writer.writerow([result["price"], result["remaining"],
                                         f"https://archive.yota.ru/tariff/{tariff_id}"])
                        output.flush()
                        counters["found"] += 1
                    counters["done"] += 1
                except (RuntimeError, ValueError) as exc:
                    counters["errors"] += 1
                    if error_output is None:
                        error_output = error_path.open("w", encoding="utf-8-sig")
                    error_output.write(f"{tariff_id}\t{exc}\n")
                    error_output.flush()
                    if counters["errors"] <= 10:
                        print(f"{tariff_id}: ошибка: {exc}", file=sys.stderr, flush=True)
                    if isinstance(exc, AccessDeniedError):
                        raise

        reporter = asyncio.create_task(report())
        workers = [asyncio.create_task(worker()) for _ in range(min(args.end - args.start + 1, args.workers))]
        try:
            await asyncio.gather(*workers)
        except AccessDeniedError:
            blocked = True
            print("Перебор остановлен: API отказал в доступе. Остальные номера не проверены. "
                  "Уже найденные тарифы сохранены.", file=sys.stderr, flush=True)
        finally:
            for task in workers + [reporter]:
                task.cancel()
            await asyncio.gather(*workers, reporter, return_exceptions=True)
            if error_output is not None:
                error_output.close()
            progress()
    elapsed = time.monotonic() - started
    print(f"Время: {elapsed:.2f} с. Запросов: {counters['requests']}. Файл: {args.output.resolve()}")
    if counters["errors"]:
        print(f"Ошибки с номерами тарифов: {error_path.resolve()}")
    return 1 if counters["errors"] or blocked else 0

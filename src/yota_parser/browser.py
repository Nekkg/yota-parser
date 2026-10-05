"""Optional Chromium parser."""

import os
import sys
import time

from .output import append_results
from .paths import project_root

# Браузер можно установить локально в проект через PLAYWRIGHT_BROWSERS_PATH.
LOCAL_BROWSERS = project_root() / ".runtime" / "browsers"
if LOCAL_BROWSERS.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(LOCAL_BROWSERS))

# SVG взят из компонента бесконечности на archive.yota.ru.
# Проверяем форму path, а не просто наличие произвольного SVG.
INFINITY_PATH = (
    "m36 26.4 6.7 6.5a9 9 0 0 0 6.8 2.6 10 10 0 0 0 7-2.5q1.3-1.2 2.1-3 .8-1.7.8-3.6"
    "a9 9 0 0 0-.8-3.6 10 10 0 0 0-5.4-5 10 10 0 0 0-3.7-.5 10 10 0 0 0-6.7 2.6zm-30.5"
    " 0a9 9 0 0 0 6 8.8q2.7 1 5.5.5a10 10 0 0 0 5-2.6l6.8-6.7-6.8-6.7a10 10 0 0 0-6.8-2.6"
    " 10 10 0 0 0-9.8 9.3m32.5 11L32.3 32l-5.6 5.5a16 16 0 0 1-17 3.2A15 15 0 0 1 0 26.4"
    "q0-4.6 2.6-8.5a16 16 0 0 1 16-6.6q4.7 1 8 4.1l5.7 5.5 5.7-5.5a16.3 16.3 0 0 1 22.4.1"
    "A15.4 15.4 0 0 1 49.2 42q-6.5-.1-11.1-4.6z"
)

EXTRACT = r"""(infinityPath) => {
    const norm = s => s.replace(/\s+/g, '');
    const gb = [...document.querySelectorAll('h1')].find(el =>
        [...el.querySelectorAll('span')].some(s => s.textContent.trim().toLowerCase() === 'гб'));
    if (!gb) return {status: 'missing'};
    const card = (() => { let el = gb.parentElement; while (el) {
            if ([...el.classList].some(c => /^_tariff_[^_]+_\d+$/.test(c))) return el;
            el = el.parentElement;
        } return null; })();
    if (!card) return {status: 'layout', reason: 'Не найден блок тарифа'};
    const unlimited = [...gb.querySelectorAll('svg path')].some(p =>
        norm(p.getAttribute('d') || '') === norm(infinityPath));
    if (!unlimited) return {status: 'limited'};
    const stock = card.innerText.match(/Остал(?:ось|ась)\s+(\d+)\s+шт(?=\s|[.,!]|$)/iu);
    const many = /Осталось\s+много/iu.test(card.innerText);
    if ((!stock && !many) || (stock && Number(stock[1]) === 0)) return {status: 'no_stock'};
    const priceEl = [...card.querySelectorAll('[class]')].find(el =>
        [...el.classList].some(c => /^_price_[^_]+_\d+$/.test(c)));
    if (!priceEl) return {status: 'layout', reason: 'Не найдена цена'};
    // Компонент цены анимирован; скрытый элемент содержит итоговую сумму сразу.
    const original = priceEl.querySelector('[class*="_wrap__hidden_"]');
    const price = (original ? original.textContent : priceEl.innerText)
        .replace(/\s/g, '').replace('₽', '').replace(',', '.');
    if (!/^\d+(?:\.\d+)?$/.test(price)) return {status: 'layout', reason: 'Не распознана цена'};
    return {status: 'match', price, remaining: stock ? Number(stock[1]) : 'много'};
}"""


def scan(page, tariff_id, timeout):
    url = f"https://archive.yota.ru/tariff/{tariff_id}"
    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    if response and response.status >= 400:
        if response.status in (404, 410):
            return {"status": "missing"}
        raise RuntimeError(f"HTTP {response.status}")
    page.wait_for_function(r"""() =>
        !location.pathname.startsWith('/tariff/') ||
        [...document.querySelectorAll('h1 span')].some(el => el.textContent.trim().toLowerCase() === 'гб') ||
        /Не удалось|Что-то пошло не так|Попробовать снова/i.test(document.body.innerText)
    """, timeout=timeout)
    if not page.url.split("?", 1)[0].rstrip("/").endswith(f"/tariff/{tariff_id}"):
        return {"status": "redirect"}
    result = page.evaluate(EXTRACT, INFINITY_PATH)
    if result["status"] == "missing":
        raise RuntimeError("Страница не загрузила данные тарифа")
    if result["status"] == "layout":
        raise RuntimeError(result["reason"])
    return result


def run_browser(args):
    from playwright.sync_api import Error, sync_playwright
    found = errors = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 900})
            page = context.new_page()
            with append_results(args.output) as (output, writer):
                for tariff_id in range(args.start, args.end + 1):
                    try:
                        result = scan(page, tariff_id, args.timeout * 1000)
                        if result["status"] == "match":
                            url = f"https://archive.yota.ru/tariff/{tariff_id}"
                            writer.writerow([result["price"], result["remaining"], url])
                            output.flush()
                            found += 1
                            print(f"{tariff_id}: {result['price']} руб., осталось {result['remaining']}", flush=True)
                        else:
                            print(f"{tariff_id}: пропуск ({result['status']})", flush=True)
                    except (Error, RuntimeError) as exc:
                        errors += 1
                        print(f"{tariff_id}: ошибка: {exc}", file=sys.stderr, flush=True)
                    if tariff_id < args.end:
                        time.sleep(args.delay)
            browser.close()
    except KeyboardInterrupt:
        print("\nОстановлено. Уже найденные тарифы сохранены.")
    except Error as exc:
        print(f"Ошибка браузера: {exc}\nУстановите браузер: python -m playwright install chromium", file=sys.stderr)
        return 1
    print(f"Найдено: {found}. Ошибок: {errors}. Файл: {args.output.resolve()}")
    return 1 if errors else 0

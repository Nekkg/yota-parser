"""Запись результатов с сохранением всех ранее записанных строк."""

from contextlib import contextmanager
import csv
from decimal import Decimal, InvalidOperation
from heapq import nsmallest
import re

RESULT_HEADER = ["Цена, руб.", "Осталось, шт.", "Ссылка"]


@contextmanager
def append_results(path):
    size = path.stat().st_size if path.exists() else 0
    needs_newline = False
    if size:
        with path.open("rb") as previous:
            previous.seek(-1, 2)
            needs_newline = previous.read(1) not in (b"\n", b"\r")
    with path.open("a", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output, delimiter="\t")
        if size == 0:
            writer.writerow(RESULT_HEADER)
        elif needs_newline:
            output.write("\r\n")
        output.flush()
        yield output, writer


def write_top_tariffs(source, destination, limit=25):
    """Самые дешёвые уникальные тарифы из всех накопленных результатов."""
    latest = {}
    with source.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.reader(file, delimiter="\t"):
            if len(row) != 3:
                continue
            price_text, remaining_text, url = (value.strip() for value in row)
            url = url.rstrip("/")
            if not re.fullmatch(r"https://archive\.yota\.ru/tariff/[1-9]\d*", url):
                continue
            try:
                price = Decimal("".join(price_text.split()).replace(",", "."))
                quantity = Decimal("Infinity") if remaining_text.lower() == "много" else int(remaining_text)
                if not price.is_finite() or price < 0 or quantity < 0:
                    continue
            except (InvalidOperation, ValueError):
                continue
            # Последняя запись отражает последний сохранённый остаток и цену.
            if quantity == 0:
                latest.pop(url, None)
            else:
                latest[url] = (price, quantity, remaining_text, url)

    top = nsmallest(limit, latest.values(), key=lambda item:
                   (item[0], -item[1], int(item[3].rsplit("/", 1)[1])))
    with destination.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(RESULT_HEADER)
        for price, _, remaining, url in top:
            writer.writerow([format(price, "f"), remaining, url])
    return len(top)

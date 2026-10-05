"""Перебор тарифов Yota: безлимитные ГБ и положительный остаток."""

import argparse
import asyncio
from datetime import datetime
import math
from pathlib import Path
import sys
import traceback

from . import __version__
from .config import load_settings
from .output import write_top_tariffs
from .paths import project_root, resolve_output


def configure_console():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv=None):
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config", type=Path, default=project_root() / "settings.ini")
    config_args, _ = preliminary.parse_known_args(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    try:
        settings = load_settings(config_args.config)
    except ValueError as exc:
        parser.error(str(exc))
    parser.add_argument("--config", type=Path, default=config_args.config, help="Файл настроек")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--start", type=int, help="Первый номер (включительно)")
    parser.add_argument("--end", type=int, help="Последний номер (включительно)")
    parser.add_argument("--output", type=Path, default=Path(settings.output), help="Файл результатов")
    parser.add_argument("--mode", choices=("api", "browser"), default=settings.mode, help="api — быстрый режим, browser — проверка SVG из исходников")
    parser.add_argument("--workers", type=int, default=settings.parallel_requests, help="Количество параллельных API-запросов")
    parser.add_argument("--rate", type=float, default=settings.requests_per_second, help="Максимум запросов в секунду, включая повторы")
    parser.add_argument("--retries", type=int, default=settings.network_retries, help="Количество повторов временных ошибок API")
    parser.add_argument("--delay", type=float, default=settings.browser_delay_seconds, help="Пауза между страницами в browser, секунды")
    parser.add_argument("--timeout", type=float, default=settings.timeout_seconds, help="Общий таймаут запроса, секунды")
    parser.add_argument("--connect-timeout", type=float, default=settings.connect_timeout_seconds, help="Таймаут соединения, секунды")
    parser.add_argument("--headed", action="store_true", help="Показывать браузер")
    parser.add_argument("--top-count", type=int, default=settings.top_count, help="Сколько самых дешёвых тарифов сохранять в топ")
    parser.add_argument("--top-only", action="store_true", help="Пересобрать топ из сохранённых результатов без запросов к Yota")
    parser.add_argument("--self-check", action="store_true", help="Проверить запуск и зависимости без запросов к Yota")
    args = parser.parse_args(argv)
    if args.top_count < 1:
        parser.error("top-count должен быть целым числом больше 0")
    if (args.start is None) != (args.end is None):
        parser.error("Укажите --start и --end вместе")
    if args.start is None and not (args.top_only or args.self_check):
        print("Перебор тарифов Yota")
        print(f"Режим {args.mode}: до {args.rate:g} запросов/с, одновременно до {args.workers}.")
        print(f"Новые результаты добавляются в {resolve_output(args.output)}")
        print(f"После завершения будет сохранён ТОП-{args.top_count} по цене. При HTTP 403 перебор остановится.\n")
        try:
            args.start = int(input("Начальный номер тарифа: ").strip())
            args.end = int(input("Конечный номер тарифа: ").strip())
        except ValueError:
            parser.error("Номера тарифов должны быть целыми числами")
    if args.start is not None and not 0 < args.start <= args.end:
        parser.error("Нужно 0 < start <= end")
    if not math.isfinite(args.delay) or not math.isfinite(args.timeout) or args.delay < 0 or args.timeout <= 0:
        parser.error("delay должен быть >= 0, timeout > 0")
    if not 0 < args.connect_timeout < float("inf"):
        parser.error("connect-timeout должен быть положительным конечным числом")
    if not 1 <= args.workers <= 200 or not 0 < args.rate <= 1000 or not 0 <= args.retries <= 5:
        parser.error("Нужно 1 <= workers <= 200, 0 < rate <= 1000, 0 <= retries <= 5")
    args.output = resolve_output(args.output)
    return args


def run_browser(args):
    if getattr(sys, "frozen", False):
        raise RuntimeError("Для проверки SVG используйте исходники: scripts/setup.ps1 -Browser, затем scripts/start-source.bat --mode browser")
    from .browser import run_browser as scan_browser
    return scan_browser(args)


def save_top(source, limit):
    if not source.exists():
        print(f"Файл результатов пока не создан: {source}", file=sys.stderr)
        return 1
    top_path = source.with_name("top_" + source.name)
    try:
        count = write_top_tariffs(source, top_path, limit=limit)
        print(f"ТОП-{limit} по цене: {count} тарифов. Файл: {top_path}")
        return 0
    except (OSError, UnicodeError) as exc:
        print(f"Не удалось сохранить топ тарифов: {exc}", file=sys.stderr)
        return 1


def main(argv=None):
    configure_console()
    args = parse_args(argv)
    if args.self_check:
        import curl_cffi
        from .api import run_api  # Проверяет загрузку нативных DLL, без сетевых запросов.
        print(f"Yota Parser {__version__}; Python {sys.version.split()[0]}; curl_cffi {curl_cffi.__version__}")
        print(f"Папка программы: {project_root()}")
        print(f"Результаты: {args.output}")
        print("Проверка запуска пройдена. Подключение к Yota не проверялось.")
        return 0
    if args.top_only:
        return save_top(args.output, args.top_count)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.mode == "browser":
            exit_code = run_browser(args)
        else:
            from .api import run_api
            if sys.platform == "win32" and sys.version_info >= (3, 12):
                exit_code = asyncio.run(run_api(args), loop_factory=asyncio.SelectorEventLoop)
            else:
                exit_code = asyncio.run(run_api(args))
    except ImportError as exc:
        print(f"Не найдена зависимость: {exc}. Для исходников запустите scripts/start-source.bat; для EXE скопируйте всю папку сборки заново.", file=sys.stderr)
        exit_code = 1
    except (OSError, RuntimeError) as exc:
        print(f"Не удалось выполнить перебор: {exc}", file=sys.stderr)
        exit_code = 1
    except KeyboardInterrupt:
        print("\nОстановлено. Уже найденные тарифы сохранены.")
        exit_code = 130

    # Читаем весь накопленный файл после закрытия записи в обоих режимах.
    if args.output.exists():
        if save_top(args.output, args.top_count):
            exit_code = 1
    return exit_code


def entrypoint():
    try:
        return main()
    except (KeyboardInterrupt, EOFError):
        print("\nВвод или перебор прерван. Сохранённые результаты остаются в файле.")
        return 130
    except Exception as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        try:
            log = project_root() / "results" / "crash.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as file:
                file.write(f"\n{datetime.now().isoformat()}\n")
                traceback.print_exc(file=file)
            print(f"Подробности: {log}", file=sys.stderr)
        except OSError:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())

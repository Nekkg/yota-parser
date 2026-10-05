"""Настройки скорости и файлов в обычном INI."""

from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Settings:
    requests_per_second: float = 5.0
    parallel_requests: int = 3
    connect_timeout_seconds: float = 5.0
    timeout_seconds: float = 25.0
    network_retries: int = 2
    top_count: int = 25
    browser_delay_seconds: float = 1.0
    mode: str = "api"
    output: str = "results/tariffs.txt"


def load_settings(path):
    if not path.exists():
        return Settings()
    config = ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8-sig") as file:
            config.read_file(file)
        defaults = Settings()
        values = {}
        for name in defaults.__dataclass_fields__:
            default = getattr(defaults, name)
            if isinstance(default, int):
                value = config.getint("parser", name, fallback=default)
            elif isinstance(default, float):
                value = config.getfloat("parser", name, fallback=default)
            else:
                value = config.get("parser", name, fallback=default).strip()
            values[name] = value
        settings = Settings(**values)
        validate_settings(settings)
        return settings
    except (ConfigError, ValueError, UnicodeError, OSError) as exc:
        raise ValueError(f"Ошибка в настройках {path}: {exc}") from exc


def validate_settings(settings):
    if not 0 < settings.requests_per_second <= 1000:
        raise ValueError("requests_per_second: нужно число больше 0 и не больше 1000")
    if not 1 <= settings.parallel_requests <= 200:
        raise ValueError("parallel_requests: нужно целое число от 1 до 200")
    if not 0 <= settings.network_retries <= 5:
        raise ValueError("network_retries: нужно целое число от 0 до 5")
    if settings.top_count < 1:
        raise ValueError("top_count: нужно целое число больше 0")
    for name in ("connect_timeout_seconds", "timeout_seconds", "browser_delay_seconds"):
        value = getattr(settings, name)
        if not math.isfinite(value) or (value < 0 if name == "browser_delay_seconds" else value <= 0):
            raise ValueError(f"{name}: некорректный таймаут или интервал")
    if settings.mode not in ("api", "browser") or not settings.output:
        raise ValueError("mode должен быть api или browser, output не должен быть пустым")

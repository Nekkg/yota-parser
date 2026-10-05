"""Пути не зависят от текущей папки консоли и установленного Python."""

from pathlib import Path
import sys


def project_root():
    if getattr(sys, "frozen", False):
        folder = Path(sys.executable).resolve().parent
        return folder.parent if folder.name.lower() == "bin" else folder
    return Path(__file__).resolve().parents[2]


def resolve_output(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root() / path

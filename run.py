"""Запуск из исходников и точка входа для портативной сборки."""

from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from yota_parser.cli import entrypoint

if __name__ == "__main__":
    raise SystemExit(entrypoint())

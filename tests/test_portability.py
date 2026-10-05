import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from yota_parser.cli import main, parse_args
from yota_parser.config import load_settings
from yota_parser.paths import project_root


class PortabilityTests(unittest.TestCase):
    def test_source_root_is_independent_of_working_directory(self):
        expected = Path(__file__).resolve().parents[1]
        with patch("yota_parser.paths.Path.cwd", return_value=Path("C:/elsewhere")):
            self.assertEqual(project_root(), expected)

    def test_frozen_root_uses_executable_folder_not_bundle_or_old_python(self):
        with TemporaryDirectory(prefix="Yota ") as folder:
            root = Path(folder)
            (root / "bin").mkdir()
            with patch("sys.frozen", True, create=True), patch("sys.executable", str(root / "bin" / "YotaParser.exe")):
                self.assertEqual(project_root(), root.resolve())
                with patch("builtins.print"):
                    args = parse_args(["--self-check"])
                self.assertEqual(args.output, root / "results" / "tariffs.txt")

    def test_config_float_rate_and_cli_override(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "settings.ini"
            config.write_text("[parser]\nrequests_per_second = 12.5\nparallel_requests = 7\ntop_count = 7\noutput = results/custom.txt\n", encoding="utf-8-sig")
            self.assertEqual(load_settings(config).requests_per_second, 12.5)
            with patch("yota_parser.cli.project_root", return_value=root), patch("yota_parser.paths.project_root", return_value=root):
                inherited = parse_args(["--config", str(config), "--self-check"])
                args = parse_args(["--config", str(config), "--start", "1", "--end", "2", "--rate", "20", "--top-count", "50"])
            self.assertEqual((args.rate, args.workers), (20, 7))
            self.assertEqual((inherited.top_count, args.top_count), (7, 50))
            self.assertEqual(args.output, root / "results" / "custom.txt")

    def test_bad_settings_are_rejected(self):
        with TemporaryDirectory() as folder:
            config = Path(folder) / "settings.ini"
            for settings in ("requests_per_second=NaN", "requests_per_second=0", "timeout_seconds=inf", "parallel_requests=0", "network_retries=9", "top_count=0", "top_count=-1", "top_count=1.5"):
                with self.subTest(settings=settings):
                    config.write_text("[parser]\n" + settings, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_settings(config)

    def test_interactive_input_stays_data_and_invalid_range_exits_before_network(self):
        with patch("builtins.print"), patch("sys.stderr", new=io.StringIO()):
            with patch("builtins.input", side_effect=["350", "400"]):
                args = parse_args([])
                self.assertEqual((args.start, args.end), (350, 400))
            for values in (["1 & echo injected", "20"], ["400", "350"]):
                with patch("builtins.input", side_effect=values), self.assertRaises(SystemExit) as error:
                    parse_args([])
                self.assertEqual(error.exception.code, 2)
            for value in ("0", "-1", "1.5"):
                with self.subTest(top_count=value), self.assertRaises(SystemExit) as error:
                    parse_args(["--self-check", "--top-count", value])
                self.assertEqual(error.exception.code, 2)

    def test_top_only_does_not_connect_to_yota(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "custom.txt"
            source.write_text("100\t3\thttps://archive.yota.ru/tariff/1\n"
                              "90\t2\thttps://archive.yota.ru/tariff/2\n", encoding="utf-8")
            original = source.read_bytes()
            with patch("builtins.print"), patch("yota_parser.api.run_api") as run:
                self.assertEqual(main(["--top-only", "--output", str(source), "--top-count", "1"]), 0)
                run.assert_not_called()
            self.assertEqual(source.read_bytes(), original)
            top = source.with_name("top_custom.txt").read_text(encoding="utf-8-sig")
            self.assertEqual(len(top.splitlines()), 2)
            self.assertIn("90\t2\thttps://archive.yota.ru/tariff/2", top)

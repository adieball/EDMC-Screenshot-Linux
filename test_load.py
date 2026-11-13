"""Headless tests for load.py helpers."""

import json
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory


def _stub(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# EDMC and Pillow are provided by the host application, so stub them for tests.
_stub("config", appname="EDMC", config=types.SimpleNamespace())
_stub("myNotebook")
_stub("PIL", Image=object, ImageTk=object)

tk = _stub("tkinter")
tk.Frame = object
tk.StringVar = object
tk.ttk = _stub("tkinter.ttk")
tk.filedialog = _stub("tkinter.filedialog")

import load  # noqa: E402


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get_str(self, key):
        return self.values.get(key, "")


class FakeImage:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def save(self, path, format, optimize):
        Path(path).write_bytes(b"png")

    def thumbnail(self, size):
        self.thumb_size = size

    def copy(self):
        return "thumb"


class FilenameTests(unittest.TestCase):
    dt = datetime(2026, 4, 3, 22, 18, 0)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name):
        (self.dir / name).write_bytes(b"")

    def test_counter_without_body(self):
        first = load._build_filename(1, self.dt, "Sol", "", "", self.dir)
        self._write(first)
        second = load._build_filename(1, self.dt, "Sol", "", "", self.dir)

        self.assertEqual(first, "Sol_00001.png")
        self.assertEqual(second, "Sol_00002.png")

    def test_counter_with_body(self):
        first = load._build_filename(1, self.dt, "Sol", "Earth", "", self.dir)
        self._write(first)
        second = load._build_filename(1, self.dt, "Sol", "Earth", "", self.dir)

        self.assertEqual(first, "Sol(Earth)_00001.png")
        self.assertEqual(second, "Sol(Earth)_00002.png")

    def test_counter_when_body_equals_system(self):
        first = load._build_filename(1, self.dt, "Sol", "Sol", "", self.dir)
        self._write(first)
        second = load._build_filename(1, self.dt, "Sol", "Sol", "", self.dir)

        self.assertEqual(first, "Sol_00001.png")
        self.assertEqual(second, "Sol_00002.png")

    def test_other_formats(self):
        args = (self.dt, "Eta Carinae", "A 1", "F0RD42", self.dir)

        self.assertEqual(
            load._build_filename(0, *args),
            "20260403-221800_Eta Carinae_A 1.png",
        )
        self.assertEqual(
            load._build_filename(2, *args),
            "F0RD42_20260403-221800_Eta Carinae_A 1.png",
        )
        self.assertEqual(
            load._build_filename(1, *args),
            "Eta Carinae(A 1)_00001.png",
        )
        self.assertEqual(
            load._build_filename(3, *args),
            "Eta Carinae_A 1_20260403-221800.png",
        )

    def test_body_system_prefix_is_removed(self):
        system = "Eeshorps BL-M d8-1439"
        body = f"{system} B 6 a"

        self.assertEqual(
            load._build_filename(3, self.dt, system, body, "", self.dir),
            "Eeshorps BL-M d8-1439_B 6 a_20260403-221800.png",
        )
        self.assertEqual(
            load._build_filename(1, self.dt, system, body, "", self.dir),
            "Eeshorps BL-M d8-1439(B 6 a)_00001.png",
        )

    def test_sanitize(self):
        self.assertEqual(load._sanitize("A/B:C"), "A_B_C")


class ConvertTests(unittest.TestCase):
    dt = datetime(2026, 4, 3, 22, 18, 0)

    def test_convert_avoids_collision_and_deletes_bmp(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bmp = root / "shot.bmp"
            bmp.write_bytes(b"bmp")
            out = root / "out"
            out.mkdir()
            (out / "20260403-221800_Sol.png").write_bytes(b"exists")

            old_image = load.Image
            old_widgets = load._app_widgets
            load.Image = types.SimpleNamespace(open=FakeImage)
            load._app_widgets = {}
            try:
                load._convert(bmp, bmp.name, out, 0, self.dt, "Sol", "", "", True)
            finally:
                load.Image = old_image
                load._app_widgets = old_widgets

            self.assertFalse(bmp.exists())
            self.assertTrue((out / "20260403-221800_Sol_1.png").exists())


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _dir(self, *parts):
        path = self.home.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_finds_steam_heroic_lutris_and_wine_prefixes(self):
        native = self._dir(
            "Pictures/Frontier Developments/Elite Dangerous"
        )
        steam = self._dir(
            ".local/share/Steam/steamapps/compatdata/359320/pfx/drive_c/"
            "users/steamuser/Pictures/Frontier Developments/Elite Dangerous"
        )
        flatpak_steam = self._dir(
            ".var/app/com.valvesoftware.Steam/.steam/steam/steamapps/"
            "compatdata/359320/pfx/drive_c/users/steamuser/"
            "My Pictures/Frontier Developments/Elite Dangerous"
        )
        heroic = self._dir(
            "Games/Heroic/Prefixes/Elite/drive_c/users/steamuser/"
            "Pictures/Frontier Developments/Elite Dangerous"
        )
        heroic_default = self._dir(
            "Games/Heroic/Prefixes/default/Elite Dangerous/drive_c/"
            "users/steamuser/My Pictures/Frontier Developments/Elite Dangerous"
        )
        lutris = self._dir(
            "Games/elite/drive_c/users/steamuser/"
            "My Pictures/Frontier Developments/Elite Dangerous"
        )
        lutris_nested = self._dir(
            "Games/elite-nested/prefix/drive_c/users/steamuser/"
            "Pictures/Frontier Developments/Elite Dangerous"
        )
        wine = self._dir(
            ".wine/drive_c/users/steamuser/"
            "Pictures/Frontier Developments/Elite Dangerous"
        )

        candidates = load._screenshot_candidates(self.home)

        for path in (
            native, steam, flatpak_steam, heroic, heroic_default,
            lutris, lutris_nested, wine,
        ):
            self.assertIn(path, candidates)

    def test_reads_steam_library_vdf(self):
        steamapps = self._dir(".local/share/Steam/steamapps")
        external = Path(self.tmp.name) / "SteamLibrary"
        (external / "steamapps").mkdir(parents=True)
        (steamapps / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n'
            '  "1"\n  {\n'
            f'    "path" "{external}"\n'
            "  }\n}\n",
            encoding="utf-8",
        )
        shots = (
            external / "steamapps/compatdata/359320/pfx/drive_c/"
            "users/steamuser/My Pictures/Frontier Developments/Elite Dangerous"
        )
        shots.mkdir(parents=True)

        self.assertIn(shots, load._screenshot_candidates(self.home))

    def test_reads_heroic_and_lutris_configs(self):
        heroic_prefix = self._dir(
            "custom-heroic/drive_c/users/steamuser/"
            "Pictures/Frontier Developments/Elite Dangerous"
        )
        heroic_cfg = self._dir(".config/heroic/GamesConfig")
        (heroic_cfg / "elite.json").write_text(
            json.dumps({"winePrefix": str(heroic_prefix.parents[5])}),
            encoding="utf-8",
        )

        lutris_prefix = self._dir(
            "custom-lutris/drive_c/users/steamuser/"
            "My Pictures/Frontier Developments/Elite Dangerous"
        )
        lutris_cfg = self._dir(".config/lutris/games")
        (lutris_cfg / "elite.yml").write_text(
            f"game:\n  prefix: {lutris_prefix.parents[5]}\n",
            encoding="utf-8",
        )

        candidates = load._screenshot_candidates(self.home)

        self.assertIn(heroic_prefix, candidates)
        self.assertIn(lutris_prefix, candidates)

    def test_find_screenshot_searches_all_auto_dirs(self):
        first = self._dir("empty-install")
        second = self._dir("active-install")
        shots = second / "_Screenshots"
        shots.mkdir()
        bmp = shots / "Screenshot_0000.bmp"
        bmp.write_bytes(b"bmp")

        match = load._find_screenshot(
            "_Screenshots/Screenshot_0000.bmp", [first, second]
        )

        self.assertEqual(match, (second, bmp.resolve()))
        self.assertIsNone(
            load._find_screenshot("Screenshot_9999.bmp", [first, second])
        )

    def test_journal_derived_path_wins(self):
        journal = self._dir("base/Journal Files/Elite Dangerous")
        expected = self._dir("base/Screenshots/Frontier Developments/Elite Dangerous")
        self._dir("Pictures/Frontier Developments/Elite Dangerous")

        old_config = load.config
        load.config = FakeConfig({"journaldir": str(journal)})
        try:
            self.assertEqual(load._auto_screenshots_dir(self.home), expected)
        finally:
            load.config = old_config


class PathTests(unittest.TestCase):
    def test_screenshot_path_uses_journal_subdirectory(self):
        with TemporaryDirectory() as tmp:
            input_dir = Path(tmp)
            shots = input_dir / "_Screenshots"
            shots.mkdir()
            bmp = shots / "Screenshot_0001.bmp"
            bmp.write_bytes(b"bmp")

            self.assertEqual(
                load._resolve_screenshot_path(input_dir, "_Screenshots/Screenshot_0001.bmp"),
                bmp.resolve(),
            )
            self.assertEqual(
                load._resolve_screenshot_path(input_dir, "_Screenshots\\Screenshot_0001.bmp"),
                bmp.resolve(),
            )
            self.assertEqual(
                load._resolve_screenshot_path(shots, "_Screenshots/Screenshot_0001.bmp"),
                bmp.resolve(),
            )

    def test_screenshot_path_rejects_escape(self):
        with TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "input"
            input_dir.mkdir()
            outside = Path(tmp) / "outside.bmp"
            outside.write_bytes(b"bmp")

            self.assertIsNone(
                load._resolve_screenshot_path(input_dir, "../outside.bmp")
            )
            self.assertIsNone(
                load._resolve_screenshot_path(input_dir, str(outside))
            )
            self.assertIsNone(load._resolve_screenshot_path(input_dir, ""))

    def test_output_dir_is_created(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "new" / "pngs"
            old_config = load.config
            load.config = FakeConfig({load.CFG_OUTPUT_DIR: str(target)})
            try:
                self.assertEqual(load._get_output_dir(), target)
                self.assertTrue(target.is_dir())
            finally:
                load.config = old_config


if __name__ == "__main__":
    unittest.main()

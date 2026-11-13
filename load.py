"""
ED Screenshot Converter — EDMC Plugin (Linux only)
Converts Elite Dangerous BMP screenshots to PNG on Linux,
with configurable naming formats, separate input/output directories,
and a thumbnail preview in the EDMC main window.
"""

import json
import queue
import re
import logging
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from datetime import datetime, timezone
from pathlib import Path

import myNotebook as nb
from PIL import Image, ImageTk
from config import appname, config

plugin_name = "ED Screenshot Converter"
__version__ = "0.1.4"
logger = logging.getLogger(f'{appname}.{Path(__file__).parent.name}')

# Config keys
CFG_ENABLED    = "ed_sc_enabled"
CFG_INPUT_DIR  = "ed_sc_input_dir"
CFG_OUTPUT_DIR = "ed_sc_output_dir"
CFG_FORMAT     = "ed_sc_format"
CFG_DELETE_BMP = "ed_sc_delete_bmp"

STEAM_APP_ID = "359320"
SCREENSHOT_REL_PATHS = (
    Path("Pictures/Frontier Developments/Elite Dangerous"),
    Path("My Pictures/Frontier Developments/Elite Dangerous"),
)

# Available naming formats (label, example)
FORMATS = [
    ("Date + System + Body", "20260403-221800_Eta Carinae_A 1.png"),
    (
        "System (Body) + Counter  [classic ED style]",
        "Eta Carinae(A 1)_00001.png",
    ),
    (
        "CMDR + Date + System + Body",
        "F0RD42_20260403-221800_Eta Carinae_A 1.png",
    ),
    ("System + Body + Date", "Eta Carinae_A 1_20260403-221800.png"),
]

# Prefs widget references (kept alive between calls)
_prefs = {}

# Main-window widget references
_app_widgets = {}
# Keep a strong reference to the current thumbnail so it isn't GC'd
_thumb_ref = None

# Screenshots are converted on one background worker so EDMC's UI stays responsive.
_jobs = queue.Queue()
_results = queue.Queue()
_worker = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _steam_roots(home: Path) -> list[Path]:
    return [
        home / ".steam/steam",
        home / ".steam/root",
        home / ".steam/debian-installation",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.steam/steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]


def _steam_library_dirs(steam_root: Path) -> list[Path]:
    steamapps = steam_root / "steamapps"
    libraries = [steamapps] if steamapps.is_dir() else []
    vdf = steamapps / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return libraries
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            path = Path(match.group(1)).expanduser()
            lib = path if path.name == "steamapps" else path / "steamapps"
            if lib.is_dir() and lib not in libraries:
                libraries.append(lib)
    return libraries


def _steam_prefixes(home: Path):
    for root in _steam_roots(home):
        for steamapps in _steam_library_dirs(root):
            prefix = steamapps / "compatdata" / STEAM_APP_ID / "pfx"
            if prefix.is_dir():
                yield prefix


def _heroic_config_dirs(home: Path) -> list[Path]:
    return [
        home / ".config/heroic",
        home / ".var/app/com.heroicgameslauncher.hgl/config/heroic",
    ]


def _json_wine_prefixes(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("winePrefix", "defaultWinePrefix", "defaultWinePrefixDir"):
                if isinstance(item, str):
                    yield item
            else:
                yield from _json_wine_prefixes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_wine_prefixes(item)


def _heroic_prefixes(home: Path):
    for base in (
        home / "Games/Heroic/Prefixes",
        home / ".var/app/com.heroicgameslauncher.hgl/Games/Heroic/Prefixes",
        home / ".var/app/com.heroicgameslauncher.hgl/data/Games/Heroic/Prefixes",
    ):
        if base.is_dir():
            for child in base.iterdir():
                if child.is_dir():
                    yield child

    for cfg in _heroic_config_dirs(home):
        configs = [cfg / "config.json"]
        configs.extend((cfg / "GamesConfig").glob("*.json"))
        for path in configs:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for value in _json_wine_prefixes(data):
                if not value:
                    continue
                prefix = Path(value).expanduser()
                if not prefix.is_absolute():
                    prefix = home / prefix
                if prefix.is_dir():
                    yield prefix


def _lutris_prefixes(home: Path):
    for cfg in (
        home / ".config/lutris",
        home / ".var/app/net.lutris.Lutris/config/lutris",
    ):
        for path in (cfg / "games").glob("*.yml"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in re.finditer(
                r'^\s*prefix:\s*["\']?([^"\'\n]+)["\']?\s*$', text, re.MULTILINE
            ):
                prefix = Path(match.group(1).strip()).expanduser()
                if not prefix.is_absolute():
                    prefix = home / prefix
                if prefix.is_dir():
                    yield prefix

    for base in (
        home / "Games",
        home / ".var/app/net.lutris.Lutris/Games",
        home / ".var/app/net.lutris.Lutris/data/Games",
    ):
        if base.is_dir():
            for child in base.iterdir():
                if child.is_dir() and _has_drive_c(child):
                    yield child


def _wine_prefixes(home: Path):
    if (home / ".wine").is_dir():
        yield home / ".wine"
    base = home / ".local/share/wineprefixes"
    if base.is_dir():
        for child in base.iterdir():
            if child.is_dir():
                yield child


def _has_drive_c(path: Path) -> bool:
    if (path / "drive_c").is_dir():
        return True
    try:
        return any(
            child.is_dir() and (child / "drive_c").is_dir()
            for child in path.iterdir()
        )
    except OSError:
        return False


def _prefix_screenshot_dirs(prefix: Path):
    roots = [prefix]
    if prefix.is_dir():
        try:
            roots.extend(
                child for child in prefix.iterdir()
                if child.is_dir() and (child / "drive_c").is_dir()
            )
        except OSError:
            pass

    for root in roots:
        users = root / "drive_c/users"
        if not users.is_dir():
            continue
        try:
            user_dirs = list(users.iterdir())
        except OSError:
            continue
        for user in user_dirs:
            if user.is_dir():
                for rel in SCREENSHOT_REL_PATHS:
                    candidate = user / rel
                    if candidate.is_dir():
                        yield candidate


def _screenshot_candidates(home: Path, journal_dir: str = "") -> list[Path]:
    candidates: list[Path] = []

    def add(path: Path) -> None:
        if path not in candidates:
            candidates.append(path)

    if journal_dir:
        add(
            Path(journal_dir).expanduser().parent.parent
            / "Screenshots/Frontier Developments/Elite Dangerous"
        )

    for rel in SCREENSHOT_REL_PATHS:
        add(home / rel)

    for finder in (_steam_prefixes, _heroic_prefixes, _lutris_prefixes, _wine_prefixes):
        for prefix in finder(home):
            for candidate in _prefix_screenshot_dirs(prefix):
                add(candidate)

    return candidates


def _auto_screenshots_dir(home: Path | None = None) -> Path | None:
    journal_dir = config.get_str("journaldir") or ""
    for candidate in _screenshot_candidates(home or Path.home(), journal_dir):
        if candidate.is_dir():
            return candidate
    return None


def _get_input_dir() -> Path | None:
    custom = config.get_str(CFG_INPUT_DIR) or ""
    if custom:
        p = Path(custom).expanduser()
        return p if p.is_dir() else None
    return _auto_screenshots_dir()


def _get_output_dir(input_dir: Path | None = None) -> Path | None:
    custom = config.get_str(CFG_OUTPUT_DIR) or ""
    if custom:
        p = Path(custom).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return p if p.is_dir() else None
    # Default: same as input dir
    return input_dir or _get_input_dir()


def _resolve_screenshot_path(input_dir: Path, filename: str) -> Path | None:
    """Resolve the journal path (often `_Screenshots/...`) inside input_dir."""
    if not filename:
        return None

    raw = Path(filename.replace("\\", "/"))
    candidates = [raw if raw.is_absolute() else input_dir / raw]
    if raw.name:
        # Some users point the input directory at `_Screenshots` itself.
        candidates.append(input_dir / raw.name)

    root = input_dir.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _find_screenshot(
    filename: str, input_dirs: list[Path] | None = None
) -> tuple[Path, Path] | None:
    """Find a screenshot in the configured or auto-detected directories."""
    if input_dirs is None:
        custom = config.get_str(CFG_INPUT_DIR) or ""
        if custom:
            input_dirs = [Path(custom).expanduser()]
        else:
            input_dirs = _screenshot_candidates(
                Path.home(), config.get_str("journaldir") or ""
            )

    for directory in input_dirs:
        bmp_path = _resolve_screenshot_path(directory, filename)
        if bmp_path is not None:
            return directory, bmp_path
    return None


def _sanitize(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', s).strip()


def _body_label(system: str, body: str) -> str:
    """Drop the system prefix ED puts in front of journal body names."""
    if not body or body == system:
        return ""
    prefix = f"{system} "
    return body[len(prefix):] if body.startswith(prefix) else body


def _next_counter(directory: Path, system: str, body: str) -> int:
    """Find the next available counter for System(Body)_NNNNN.png format."""
    system_s = _sanitize(system)
    body_s = _sanitize(body) if body else ""
    prefix = f"{system_s}({body_s})_" if body_s else f"{system_s}_"
    existing = [
        f for f in directory.iterdir()
        if f.name.startswith(prefix) and f.suffix == ".png"
    ]
    if not existing:
        return 1
    nums = []
    for f in existing:
        stem = f.stem[len(prefix):]
        if stem.isdigit():
            nums.append(int(stem))
    return max(nums) + 1 if nums else 1


def _build_filename(
    fmt_index: int, dt: datetime, system: str, body: str, cmdr: str, directory: Path
) -> str:
    sys_s  = _sanitize(system)
    body_s = _sanitize(_body_label(system, body))
    stamp = dt.strftime("%Y%m%d-%H%M%S")

    if fmt_index == 0:   # Date + System + Body
        parts = [stamp, sys_s]
        if body_s:
            parts.append(body_s)
        return "_".join(parts) + ".png"

    elif fmt_index == 1: # System(Body) + Counter
        body_part = f"({body_s})" if body_s else ""
        counter = _next_counter(directory, system, body_s)
        return f"{sys_s}{body_part}_{counter:05d}.png"

    elif fmt_index == 2: # CMDR + Date + System + Body
        parts = [_sanitize(cmdr or "CMDR"), stamp, sys_s]
        if body_s:
            parts.append(body_s)
        return "_".join(parts) + ".png"

    elif fmt_index == 3: # System + Body + Date
        parts = [sys_s]
        if body_s:
            parts.append(body_s)
        parts.append(stamp)
        return "_".join(parts) + ".png"

    return f"{stamp}_screenshot.png"


def _make_dir_row(
    frame: tk.Frame, row: int, label: str, cfg_key: str, auto_text: str
) -> tuple[tk.StringVar, tk.StringVar, int]:
    """
    Render a labelled directory picker block into `frame`.
    Returns (dir_mode_var, dir_entry_var, next_row).
    """
    tk.Label(frame, text=label, font=("TkDefaultFont", 9, "bold")).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 0)
    )
    row += 1

    dir_mode = tk.StringVar(value="custom" if config.get_str(cfg_key) else "auto")

    tk.Radiobutton(
        frame, text=auto_text,
        variable=dir_mode, value="auto"
    ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20)
    row += 1

    tk.Radiobutton(frame, text="Custom:", variable=dir_mode, value="custom").grid(
        row=row, column=0, sticky="w", padx=20
    )
    dir_entry_var = tk.StringVar(value=config.get_str(cfg_key) or "")
    tk.Entry(frame, textvariable=dir_entry_var, width=50).grid(
        row=row, column=1, sticky="ew", padx=4
    )

    def browse():
        chosen = filedialog.askdirectory(title=f"Select {label.lower()}")
        if chosen:
            dir_entry_var.set(chosen)
            dir_mode.set("custom")

    tk.Button(frame, text="Browse…", command=browse).grid(row=row, column=2, padx=(0, 10))
    return dir_mode, dir_entry_var, row + 1


def _ensure_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(
            target=_convert_worker,
            name="ED Screenshot Converter",
            daemon=True,
        )
        _worker.start()


def _convert_worker() -> None:
    while True:
        job = _jobs.get()
        try:
            _convert(*job)
        except Exception as e:
            logger.error(f"Screenshot conversion failed: {e}")
        finally:
            _jobs.task_done()


def _convert(
    bmp_path: Path,
    bmp_name: str,
    output_dir: Path,
    fmt_index: int,
    dt: datetime,
    sys_name: str,
    body: str,
    cmdr: str,
    delete_bmp: bool,
) -> None:
    try:
        png_name = _build_filename(fmt_index, dt, sys_name, body, cmdr, output_dir)
        png_path = output_dir / png_name

        # Avoid overwriting
        stem, suffix = png_path.stem, png_path.suffix
        counter = 1
        while png_path.exists():
            png_path = output_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        with Image.open(bmp_path) as img:
            img.save(png_path, "PNG", optimize=True)
            logger.info(f"Converted: {bmp_name} → {png_path.name}")
            img.thumbnail((160, 90))   # 16:9, ~160px wide
            thumb = img.copy()

        if _app_widgets:
            _results.put((thumb, png_path.name, output_dir))

        if delete_bmp:
            bmp_path.unlink()
            logger.debug(f"Deleted BMP: {bmp_name}")
    except Exception as e:
        logger.error(f"Failed to convert {bmp_name}: {e}")


def _poll_results() -> None:
    """Apply finished conversions on the Tk main thread."""
    global _thumb_ref

    try:
        while True:
            thumb, name, output_dir = _results.get_nowait()
            _thumb_ref = ImageTk.PhotoImage(thumb)

            _app_widgets["image_label"].configure(image=_thumb_ref)
            _app_widgets["image_label"].grid()

            name_text = name
            if len(name_text) > 60:
                name_text = name_text[:57] + "..."
            _app_widgets["filename_label"].configure(text=name_text)
            _app_widgets["filename_label"].grid()
            _app_widgets["header"].grid()
            _app_widgets["last_output_dir"] = output_dir

    except queue.Empty:
        pass
    except tk.TclError:
        return

    root = _app_widgets.get("root")
    if root is not None:
        try:
            root.after(100, _poll_results)
        except tk.TclError:
            pass


# ── EDMC Plugin API ───────────────────────────────────────────────────────────

def plugin_start3(plugin_dir: str) -> str:
    _ensure_worker()
    logger.info(
        f"{plugin_name} loaded. "
        f"Input: {_get_input_dir() or 'NOT FOUND'}  "
        f"Output: {_get_output_dir() or 'NOT FOUND'}"
    )
    return plugin_name


def plugin_app(parent: tk.Frame) -> tk.Frame:
    """Add a thumbnail preview to the EDMC main window."""
    frame = tk.Frame(parent)
    frame.columnconfigure(1, weight=1)

    header = tk.Label(frame, text="Last screenshot")
    header.grid(row=0, column=0, columnspan=2, sticky="w")
    header.grid_remove()

    filename_label = tk.Label(frame, text="", anchor="w", foreground="gray",
                              font=("TkFixedFont", 8), cursor="hand2")
    filename_label.grid(row=1, column=0, columnspan=2, sticky="w")
    filename_label.grid_remove()

    image_label = tk.Label(frame, anchor="w")
    image_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
    image_label.grid_remove()

    def open_output(_event):
        path = _app_widgets.get("last_output_dir")
        if path:
            subprocess.Popen(["xdg-open", str(path)])

    filename_label.bind("<Button-1>", open_output)

    _app_widgets["header"]        = header
    _app_widgets["filename_label"] = filename_label
    _app_widgets["image_label"]   = image_label
    _app_widgets["root"]          = parent.winfo_toplevel()
    _app_widgets["root"].after(100, _poll_results)

    return frame


def plugin_prefs(parent: tk.Frame, cmdr: str, is_beta: bool) -> tk.Frame:
    """Build the plugin settings tab."""
    frame = nb.Frame(parent)
    frame.columnconfigure(1, weight=1)

    row = 0

    # ── Title ──
    tk.Label(frame, text=plugin_name, font=("TkDefaultFont", 11, "bold")).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 2)
    )
    row += 1
    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=4
    )
    row += 1

    # ── Enable ──
    enabled_var = tk.BooleanVar(
        value=bool(config.get_bool(CFG_ENABLED) if config.get_str(CFG_ENABLED) else True)
    )
    tk.Checkbutton(frame, text="Enable screenshot conversion", variable=enabled_var).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=2
    )
    _prefs["enabled"] = enabled_var
    row += 1

    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=6
    )
    row += 1

    # ── Input directory ──
    auto_detected = _auto_screenshots_dir()
    auto_label = str(auto_detected) if auto_detected else "(could not auto-detect)"

    in_mode, in_entry_var, row = _make_dir_row(
        frame, row, "Input Directory  (where ED saves BMPs)", CFG_INPUT_DIR,
        f"Auto-detect  ({auto_label})",
    )
    _prefs["in_mode"]  = in_mode
    _prefs["in_entry"] = in_entry_var

    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=6
    )
    row += 1

    # ── Output directory ──
    out_mode, out_entry_var, row = _make_dir_row(
        frame, row, "Output Directory  (where converted PNGs are saved)", CFG_OUTPUT_DIR,
        "Same as input directory",
    )
    _prefs["out_mode"]  = out_mode
    _prefs["out_entry"] = out_entry_var

    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=6
    )
    row += 1

    # ── Naming format ──
    tk.Label(frame, text="Filename Format", font=("TkDefaultFont", 9, "bold")).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 4)
    )
    row += 1

    fmt_var = tk.IntVar(value=config.get_int(CFG_FORMAT) or 0)

    for i, (label, example) in enumerate(FORMATS):
        tk.Radiobutton(frame, text=label, variable=fmt_var, value=i).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=20, pady=(2, 0)
        )
        row += 1
        tk.Label(frame, text=f"  → {example}", foreground="gray",
                 font=("TkFixedFont", 8)).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=34, pady=(0, 4)
        )
        row += 1

    _prefs["format"] = fmt_var

    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=6
    )
    row += 1

    # ── Delete BMP ──
    delete_var = tk.BooleanVar(
        value=bool(config.get_bool(CFG_DELETE_BMP) if config.get_str(CFG_DELETE_BMP) else True)
    )
    tk.Checkbutton(frame, text="Delete original BMP after conversion",
                   variable=delete_var).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=2
    )
    _prefs["delete_bmp"] = delete_var
    row += 1

    return frame


def prefs_changed(cmdr: str, is_beta: bool) -> None:
    """Save settings when the user clicks OK in EDMC preferences."""
    config.set(CFG_ENABLED,    int(_prefs["enabled"].get()))
    config.set(CFG_DELETE_BMP, int(_prefs["delete_bmp"].get()))
    config.set(CFG_FORMAT,     _prefs["format"].get())

    config.set(CFG_INPUT_DIR,
               _prefs["in_entry"].get() if _prefs["in_mode"].get() == "custom" else "")
    config.set(CFG_OUTPUT_DIR,
               _prefs["out_entry"].get() if _prefs["out_mode"].get() == "custom" else "")

    logger.info(
        f"{plugin_name} settings saved. "
        f"Input: {_get_input_dir()}  Output: {_get_output_dir()}  "
        f"Format: {_prefs['format'].get()}  Delete BMP: {_prefs['delete_bmp'].get()}"
    )


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if not config.get_bool(CFG_ENABLED) and config.get_str(CFG_ENABLED):
        return
    if entry.get("event") != "Screenshot":
        return

    filename  = entry.get("Filename", "")
    body      = entry.get("Body", "")
    sys_name  = entry.get("System", system or "Unknown")
    timestamp = entry.get(
        "timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    match = _find_screenshot(filename)
    if match is None:
        logger.warning(f"BMP not found: {filename}")
        return
    input_dir, bmp_path = match
    bmp_name = bmp_path.name

    output_dir = _get_output_dir(input_dir)
    if not output_dir:
        logger.error("Output directory not found — cannot convert.")
        return

    dt = datetime.strptime(
        timestamp, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc).astimezone()
    fmt_index = config.get_int(CFG_FORMAT) or 0
    delete_bmp = config.get_bool(CFG_DELETE_BMP) if config.get_str(CFG_DELETE_BMP) else True

    _ensure_worker()
    _jobs.put(
        (
            bmp_path, bmp_name, output_dir, fmt_index, dt,
            sys_name, body, cmdr or "", delete_bmp,
        )
    )

"""
ED Screenshot Converter — EDMC Plugin (Linux only)
Converts Elite Dangerous BMP screenshots to PNG on Linux,
with configurable naming formats, separate input/output directories,
and a thumbnail preview in the EDMC main window.
"""

import io
import re
import logging
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog
from datetime import datetime
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

# Available naming formats (label, description, example)
FORMATS = [
    (
        "Date + System + Body",
        "{date}_{time}_{system}_{body}.png",
        "2026-04-03_22-18-00_Eta Carinae_A 1.png",
    ),
    (
        "System (Body) + Counter  [classic ED style]",
        "{system}({body})_{counter:05d}.png",
        "Eta Carinae(A 1)_00001.png",
    ),
    (
        "CMDR + Date + System + Body",
        "{cmdr}_{date}_{system}_{body}.png",
        "F0RD42_2026-04-03_Eta Carinae_A 1.png",
    ),
    (
        "System + Body + Date",
        "{system}_{body}_{date}.png",
        "Eta Carinae_A 1_2026-04-03.png",
    ),
]

# Prefs widget references (kept alive between calls)
_prefs = {}

# Main-window widget references
_app_widgets = {}
# Keep a strong reference to the current thumbnail so it isn't GC'd
_thumb_ref = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _auto_screenshots_dir() -> Path | None:
    """Derive screenshots dir from EDMC's configured journal directory."""
    journal_dir = config.get_str("journaldir") or ""
    if journal_dir:
        candidate = (
            Path(journal_dir).parent.parent
            / "Screenshots"
            / "Frontier Developments"
            / "Elite Dangerous"
        )
        if candidate.exists():
            return candidate
    # Fallback: Steam symlink path
    fallback = (
        Path.home()
        / ".local/share/Steam/steamapps/compatdata/359320/pfx"
        / "drive_c/users/steamuser/Pictures"
        / "Frontier Developments/Elite Dangerous"
    )
    if fallback.exists():
        return fallback
    return None


def _get_input_dir() -> Path | None:
    custom = config.get_str(CFG_INPUT_DIR) or ""
    if custom:
        p = Path(custom)
        return p if p.exists() else None
    return _auto_screenshots_dir()


def _get_output_dir() -> Path | None:
    custom = config.get_str(CFG_OUTPUT_DIR) or ""
    if custom:
        p = Path(custom)
        return p if p.exists() else None
    # Default: same as input dir
    return _get_input_dir()


def _sanitize(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', s).strip()


def _next_counter(directory: Path, system: str, body: str) -> int:
    """Find the next available counter for System(Body)_NNNNN.png format."""
    prefix = f"{_sanitize(system)}({_sanitize(body)})_"
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
    body_s = _sanitize(body) if body and body != system else ""
    date_s = dt.strftime("%Y-%m-%d")
    time_s = dt.strftime("%H-%M-%S")

    if fmt_index == 0:   # Date + System + Body
        parts = [f"{date_s}_{time_s}", sys_s]
        if body_s:
            parts.append(body_s)
        return "_".join(parts) + ".png"

    elif fmt_index == 1: # System(Body) + Counter
        body_part = f"({body_s})" if body_s else ""
        counter = _next_counter(directory, system, body_s or system)
        return f"{sys_s}{body_part}_{counter:05d}.png"

    elif fmt_index == 2: # CMDR + Date + System + Body
        parts = [_sanitize(cmdr or "CMDR"), date_s, sys_s]
        if body_s:
            parts.append(body_s)
        return "_".join(parts) + ".png"

    elif fmt_index == 3: # System + Body + Date
        parts = [sys_s]
        if body_s:
            parts.append(body_s)
        parts.append(date_s)
        return "_".join(parts) + ".png"

    return f"{date_s}_{time_s}_screenshot.png"


def _make_dir_row(frame: tk.Frame, row: int, label: str, cfg_key: str, auto_label: str):
    """
    Render a labelled directory picker block (auto-detect + custom) into `frame`.
    Returns (dir_mode_var, dir_entry_var) for saving in prefs_changed.
    """
    tk.Label(frame, text=label, font=("TkDefaultFont", 9, "bold")).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 0)
    )
    row += 1

    dir_mode = tk.StringVar(value="custom" if config.get_str(cfg_key) else "auto")

    tk.Radiobutton(
        frame, text=f"Auto-detect  ({auto_label})",
        variable=dir_mode, value="auto"
    ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20)
    row += 1

    tk.Radiobutton(frame, text="Custom:", variable=dir_mode, value="custom").grid(
        row=row, column=0, sticky="w", padx=20
    )
    dir_entry_var = tk.StringVar(value=config.get_str(cfg_key) or "")
    dir_entry = tk.Entry(frame, textvariable=dir_entry_var, width=50)
    dir_entry.grid(row=row, column=1, sticky="ew", padx=4)

    def browse():
        chosen = filedialog.askdirectory(title=f"Select {label.lower()}")
        if chosen:
            dir_entry_var.set(chosen)
            dir_mode.set("custom")

    tk.Button(frame, text="Browse…", command=browse).grid(row=row, column=2, padx=(0, 10))
    return dir_mode, dir_entry_var


# ── EDMC Plugin API ───────────────────────────────────────────────────────────

def plugin_start3(plugin_dir: str) -> str:
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

    header = tk.Label(frame, text="Last screenshot:")
    header.grid(row=0, column=0, sticky="w", padx=(0, 6))
    header.grid_remove()

    filename_label = tk.Label(frame, text="", anchor="w", foreground="gray",
                              font=("TkFixedFont", 8), cursor="hand2")
    filename_label.grid(row=0, column=1, sticky="w")
    filename_label.grid_remove()

    image_label = tk.Label(frame, anchor="w")
    image_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
    image_label.grid_remove()

    def open_output(_event):
        path = _app_widgets.get("last_output_dir")
        if path:
            subprocess.Popen(["xdg-open", str(path)])

    filename_label.bind("<Button-1>", open_output)

    _app_widgets["header"]        = header
    _app_widgets["filename_label"] = filename_label
    _app_widgets["image_label"]   = image_label

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

    # We embed the rows directly so we can track `row` correctly
    tk.Label(frame, text="Input Directory  (where ED saves BMPs)",
             font=("TkDefaultFont", 9, "bold")).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 0)
    )
    row += 1

    in_mode = tk.StringVar(value="custom" if config.get_str(CFG_INPUT_DIR) else "auto")
    tk.Radiobutton(
        frame, text=f"Auto-detect  ({auto_label})",
        variable=in_mode, value="auto"
    ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20)
    row += 1

    tk.Radiobutton(frame, text="Custom:", variable=in_mode, value="custom").grid(
        row=row, column=0, sticky="w", padx=20
    )
    in_entry_var = tk.StringVar(value=config.get_str(CFG_INPUT_DIR) or "")
    tk.Entry(frame, textvariable=in_entry_var, width=50).grid(
        row=row, column=1, sticky="ew", padx=4
    )

    def browse_in():
        chosen = filedialog.askdirectory(title="Select input directory (ED BMP screenshots)")
        if chosen:
            in_entry_var.set(chosen)
            in_mode.set("custom")

    tk.Button(frame, text="Browse…", command=browse_in).grid(row=row, column=2, padx=(0, 10))
    _prefs["in_mode"]  = in_mode
    _prefs["in_entry"] = in_entry_var
    row += 1

    ttk.Separator(frame, orient="horizontal").grid(
        row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=6
    )
    row += 1

    # ── Output directory ──
    tk.Label(frame, text="Output Directory  (where converted PNGs are saved)",
             font=("TkDefaultFont", 9, "bold")).grid(
        row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 0)
    )
    row += 1

    out_mode = tk.StringVar(value="custom" if config.get_str(CFG_OUTPUT_DIR) else "auto")
    tk.Radiobutton(
        frame, text="Same as input directory",
        variable=out_mode, value="auto"
    ).grid(row=row, column=0, columnspan=3, sticky="w", padx=20)
    row += 1

    tk.Radiobutton(frame, text="Custom:", variable=out_mode, value="custom").grid(
        row=row, column=0, sticky="w", padx=20
    )
    out_entry_var = tk.StringVar(value=config.get_str(CFG_OUTPUT_DIR) or "")
    tk.Entry(frame, textvariable=out_entry_var, width=50).grid(
        row=row, column=1, sticky="ew", padx=4
    )

    def browse_out():
        chosen = filedialog.askdirectory(title="Select output directory (converted PNGs)")
        if chosen:
            out_entry_var.set(chosen)
            out_mode.set("custom")

    tk.Button(frame, text="Browse…", command=browse_out).grid(row=row, column=2, padx=(0, 10))
    _prefs["out_mode"]  = out_mode
    _prefs["out_entry"] = out_entry_var
    row += 1

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

    for i, (label, pattern, example) in enumerate(FORMATS):
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
    global _thumb_ref

    if not config.get_bool(CFG_ENABLED) and config.get_str(CFG_ENABLED):
        return
    if entry.get("event") != "Screenshot":
        return

    filename  = entry.get("Filename", "")
    body      = entry.get("Body", "")
    sys_name  = entry.get("System", system or "Unknown")
    timestamp = entry.get("timestamp", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))

    input_dir = _get_input_dir()
    if not input_dir:
        logger.error("Input directory not found — cannot convert.")
        return

    output_dir = _get_output_dir()
    if not output_dir:
        logger.error("Output directory not found — cannot convert.")
        return

    bmp_name = Path(filename.replace("\\", "/")).name
    bmp_path = input_dir / bmp_name

    if not bmp_path.exists():
        logger.warning(f"BMP not found: {bmp_path}")
        return

    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    fmt_index = config.get_int(CFG_FORMAT) or 0
    png_name  = _build_filename(fmt_index, dt, sys_name, body, cmdr or "", output_dir)
    png_path  = output_dir / png_name

    # Avoid overwriting
    stem, suffix = png_path.stem, png_path.suffix
    counter = 1
    while png_path.exists():
        png_path = output_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        with Image.open(bmp_path) as img:
            img.save(png_path, "PNG", optimize=True)
            logger.info(f"Converted: {bmp_name} → {png_path.name}")

            # ── Update main-window thumbnail ──────────────────────────────
            if _app_widgets:
                thumb = img.copy()
                thumb.thumbnail((160, 90))   # 16:9, ~160px wide
                _thumb_ref = ImageTk.PhotoImage(thumb)

                _app_widgets["image_label"].configure(image=_thumb_ref)
                _app_widgets["image_label"].grid()

                name_text = png_path.name
                if len(name_text) > 40:
                    name_text = name_text[:37] + "..."
                _app_widgets["filename_label"].configure(text=name_text)
                _app_widgets["filename_label"].grid()
                _app_widgets["header"].grid()
                _app_widgets["last_output_dir"] = output_dir

        delete_bmp = config.get_bool(CFG_DELETE_BMP) if config.get_str(CFG_DELETE_BMP) else True
        if delete_bmp:
            bmp_path.unlink()
            logger.debug(f"Deleted BMP: {bmp_name}")

    except Exception as e:
        logger.error(f"Failed to convert {bmp_name}: {e}")

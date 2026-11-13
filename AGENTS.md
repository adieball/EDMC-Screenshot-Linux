# AGENTS.md

## Overview

- Linux-only [EDMarketConnector](https://github.com/EDCD/EDMarketConnector) (EDMC) plugin. `load.py` is the entire plugin and must stay at the repository root; the release ships it in a plugin folder named `EDMC-Screenshot-Linux` (the registry's `pluginDirName`), and EDMC uses the installed folder name in the logger.
- No package, dependency manifest, linter, or build tooling. Tests use stdlib `unittest` and stub EDMC, Pillow, and tkinter for headless import: run `python -m unittest -v`. EDMC supplies `config` and `myNotebook`; EDMC's Python provides Pillow. Importing `load.py` outside EDMC fails unless those modules are stubbed.
- Target the Python 3.13 runtime bundled with EDMC 6.1.2+. Avoid deprecated stdlib calls such as `datetime.utcnow()`; use timezone-aware alternatives (`datetime.now(timezone.utc)`).

## Plugin wiring

- EDMC calls `plugin_start3`, `plugin_app`, `plugin_prefs`, `prefs_changed`, and `journal_entry`. Module globals (`_prefs`, `_app_widgets`, `_thumb_ref`) persist between callbacks; `_thumb_ref` must keep a strong reference or the Tk thumbnail is garbage-collected.
- `journal_entry` reacts only to `Screenshot` events. It validates paths and queues a job; one daemon worker (`_convert_worker`) builds the name, converts with Pillow, and optionally deletes the BMP. `_poll_results` applies results to the widgets on the Tk main thread via `root.after`. Do not touch Tk widgets from the worker. Journal timestamps are UTC; date-based filename formats are converted to local time and rendered as `YYYYMMDD-HHMMSS`.
- There is no scanning, retry, or backfill. The journal `Filename` is often `_Screenshots/Screenshot_...bmp`; `_resolve_screenshot_path` joins it to an input directory, resolves symlinks, and rejects paths that escape that directory. In auto mode `_find_screenshot` tries every detected candidate, not only the first one; a missing BMP is only logged. The journal `Body` includes the system prefix; `_body_label` removes it before naming.
- The filename label opens the output folder via `xdg-open`; do not introduce Windows/macOS paths.

## Configuration

- EDMC config keys: `ed_sc_enabled`, `ed_sc_input_dir`, `ed_sc_output_dir`, `ed_sc_format`, `ed_sc_delete_bmp`.
- An empty directory string means auto-detect; a non-empty string means custom. `prefs_changed` writes `""` when auto is selected. Custom paths are expanded with `~`; the input must exist, while the output directory is created automatically. A missing input or uncreatable output disables conversion and only logs an error.
- Missing boolean settings default to enabled/delete-BMP via `config.get_str(...)` presence checks. Preserve this sentinel logic when changing defaults.
- The integer in `ed_sc_format` indexes `FORMATS` and is persisted. Append new formats; never reorder or remove entries without migrating saved settings.
- Auto-detection tries the journal-derived `Screenshots/Frontier Developments/Elite Dangerous`, then common native/Flatpak Steam libraries (including `libraryfolders.vdf` paths), Heroic and Lutris configs/prefixes, direct Wine prefixes, and both `Pictures` and `My Pictures`. It checks `drive_c` both directly in a prefix and one game-name level down (e.g. Heroic `Prefixes/default/Elite Dangerous/drive_c`). Output auto mode reuses the detected input directory. A Flatpak EDMC can only see folders allowed by its sandbox; otherwise the user must grant access or set a custom path.

## Release

- `.github/workflows/release.yml` runs only when a GitHub release is **published**, not when a tag is pushed. It zips `load.py`, `LICENSE`, and `README.md` under `EDMC-Screenshot-Linux/` and uploads `EDMC-Screenshot-Linux.zip` to that release.
- Release checklist: bump `__version__` in `load.py` and `pluginVer` in `registry-submission.json`, tag, publish the release, then update `pluginLastUpdate`, `pluginZip`, and `pluginHash`. `pluginHash` is the SHA-256 of the uploaded zip, not of `load.py`, and cannot be computed until the asset exists (zip timestamps affect it).

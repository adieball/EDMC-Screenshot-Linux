# ED Screenshot Converter

> [!NOTE]
> This project is 95% vibe-coded. Some stuff I did on my own, but all bugs are 100% written by Claude.


> **Linux only.** This plugin is written for the native Linux installation of EDMarketConnector and will not work on Windows or macOS.

An [EDMarketConnector](https://github.com/EDCD/EDMarketConnector) plugin that automatically converts Elite Dangerous BMP screenshots to PNG, with configurable naming formats based on in-game context.

## Features

- Converts screenshots from BMP to PNG automatically the moment you take them in-game
- Names files using live journal data: current system, body, timestamp, and CMDR name
- 4 configurable naming formats
- Separate **Input** and **Output** directory settings for maximum flexibility
- Auto-detects your screenshots directory from EDMC's journal configuration and common Steam, Heroic, Lutris, and Wine prefixes
- Shows a thumbnail of the last converted screenshot in the EDMC main window (click to open output folder)
- Settings tab integrated directly into EDMC's File → Settings
- Optionally deletes the original BMP after conversion

## Why

Elite Dangerous saves screenshots as uncompressed BMP files. This plugin converts them to PNG (lossless, ~10× smaller) and gives them meaningful names instead of `Screenshot_0083.bmp`.

## Requirements

- **Linux** — this plugin uses the native Linux EDMC installation and is not compatible with Windows or macOS
- [EDMarketConnector](https://github.com/EDCD/EDMarketConnector) 6.1.2 or later (runs on its bundled Python 3.13)
- Python package: `Pillow` (included with EDMC on Linux)

## Installation

1. Download or clone this repository
2. Copy the `EDMC-Screenshot-Linux` folder into your EDMC plugins directory:
   ```
   ~/.local/share/EDMarketConnector/plugins/
   ```
3. Restart EDMC

## Configuration

Open **File → Settings** in EDMC and select the **ED Screenshot Converter** tab.

### Input Directory

Where Elite Dangerous saves its raw BMP screenshots.

| Option | Behaviour |
|---|---|
| **Auto-detect** | Searches the journal-derived folder plus common Steam, Flatpak Steam, Heroic, Lutris, and Wine/Proton prefixes for `Pictures` or `My Pictures/Frontier Developments/Elite Dangerous` |
| **Custom** | Pick any directory with the Browse button |

### Output Directory

Where converted PNG files are saved.

| Option | Behaviour |
|---|---|
| **Same as input directory** | Converted PNGs land in the same folder as the source BMPs |
| **Custom** | Pick a separate destination — useful if you want BMPs and PNGs in different locations |

### Filename Formats

| Format | Example |
|---|---|
| Date + System + Body | `20260403-221800_Eta Carinae_A 1.png` |
| System (Body) + Counter *(classic ED style)* | `Eta Carinae(A 1)_00001.png` |
| CMDR + Date + System + Body | `F0RD42_20260403-221800_Eta Carinae_A 1.png` |
| System + Body + Date | `Eta Carinae_A 1_20260403-221800.png` |

The date formats use the computer's local time as `YYYYMMDD-HHMMSS`. The counter format auto-increments per system+body combination, matching the naming style used by other ED screenshot tools. Body names are written without the system prefix that Elite Dangerous includes in the journal.

### Other Options

- **Delete original BMP after conversion** — enabled by default; disable if you want to keep both files

## Main Window

After each screenshot a small thumbnail appears in the EDMC main window below the plugin label. Click the filename to open the output folder in your file manager.

## How It Works

Elite Dangerous writes a `Screenshot` entry to its journal file every time you take a screenshot. This entry includes the current system name, body name, and timestamp. EDMC exposes these events to plugins via the `journal_entry` callback — so this plugin receives the metadata at the exact moment the screenshot is taken, with no polling or file watching needed.

Auto-detection first tries the folder derived from EDMC's configured `journaldir`:

```
<base>/Journal Files/Elite Dangerous   ← journaldir
<base>/Screenshots/Frontier Developments/Elite Dangerous   ← auto-detected input dir
```

That covers cloud-synced setups such as Nextcloud or Syncthing. It then checks common native and Flatpak installations of Steam, Heroic, Lutris, and direct Wine/Proton prefixes, looking for `Pictures` and `My Pictures/Frontier Developments/Elite Dangerous`. Steam libraries listed in `libraryfolders.vdf` are included, including libraries on other drives. Nested layouts such as Heroic's `Prefixes/default/Elite Dangerous/drive_c/...` are handled as well. If EDMC itself is installed as a Flatpak, grant it access to the screenshot folder (Flatseal → Filesystem) or use a custom input directory.

## License

MIT

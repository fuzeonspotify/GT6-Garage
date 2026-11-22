# Build and contribute

The viewer, converter, template authoring stages, native model readers/writers, catalogue editing, archive verification, desktop UI and Windows packaging are published here. A clean checkout can run the model workshop and its synthetic tests without game assets. A real game export uses a compatible personal template, game update and the append-only packer.

## Run from source

The public Windows build uses **64-bit Python 3.14**, **NumPy 2.5.5**, **Pillow 13.0.0**, **fast-simplification 0.2.0** and **PyInstaller 6.22.3**. Python’s standard Windows Tcl/Tk installation supplies the file pickers. For reproducing the eight historical template hashes use **Blender 5.2.0**. The general model importer requires Blender only for the formats indicated in the player guide.

```powershell
git clone https://github.com/fuzeonspotify/GT6-Garage.git
cd GT6-Garage
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -r build-requirements.txt -c release-constraints.txt
.\.venv\Scripts\python player.py
```

**player.py** starts the model tester and links to the workshop. **run.py** starts the full workshop directly. The browser connects to a local loopback server. Input models and projects stay on the player’s PC. **--workspace** selects an isolated profile for development. For a stock build, prepare the packer and supply your own template and files.

## Build the packer

The stock GTToolsSharp behavior can reuse an existing payload index. This project’s small append-only modification keeps an old header useful until the new payloads are installed. It is part of the source release.

- Upstream GTToolsSharp commit **8ad944caf9c1c9c113a070a20bbf668cddc91315**. [Pinned source](https://github.com/Nenkai/GTToolsSharp/tree/8ad944caf9c1c9c113a070a20bbf668cddc91315)
- PDTools submodule **2a9637d8c36100e4c211d73cd8140a9d690578d2**. [Pinned submodule](https://github.com/Nenkai/PDTools/tree/2a9637d8c36100e4c211d73cd8140a9d690578d2)
- The complete modification, runtime notices and dependency inventory are in this repository. [Packer patch and notices](../third_party/GTToolsSharp)

Install Git and the **.NET 9 SDK**. From the repository root, run the following. The script verifies both revisions, checks and applies the patch, runs a self-contained Windows x64 publish, places the result in **Support/GTToolsSharp/** and pins its SHA-256 in your checkout. This makes a locally compiled executable usable even if the SDK produces a different binary hash. Review changes to that pin when producing your own distribution.

```powershell
.\.venv\Scripts\python tools/build_packer.py
```

Alternatively, copy **Support/GTToolsSharp/** from the matching official Garage Windows release into the source checkout. Its reviewed executable SHA-256 is **7d89b679b48d26a56b23f3fb06de6271d1fb125970ee231ba7c4c7c20581f500**.

## Run the checks

```powershell
.\.venv\Scripts\python -B run.py --self-test-report "$env:TEMP\gt6-garage-tests.json"
node tools/ui_settings_regression.cjs
node tools/ui_template_shell_regression.cjs
```

The Python suite exercises synthetic geometry, file bounds, native structures, database/name preservation, export ordering, error recovery and the local app connection. The Node checks run the real UI code against controlled state transitions. OS-specific tests can report skips with their reasons. Model-specific visual fit and console gameplay require separate checks.

For a game-data integration test, start with a new workspace, build the template and prepare the stock update. The following is a local-disc example. For FTP use **--ftp-host** in place of **--disc**. Named FTP users are prompted for a password. If you already have the six original loose donor files, pass **--stock-car** instead of the disc, update and packer arguments on the first command.

```powershell
.\.venv\Scripts\python run.py --prepare-template --source-model "D:\Models\1997_toyota_supra_rz.glb" --disc "D:\MyGT6\GT.VOL" --update "D:\MyGT6\PDIPFS" --packer ".\Support\GTToolsSharp\GTToolsSharp.exe" --output "D:\MyGarage\Car template"
.\.venv\Scripts\python player.py --workspace "D:\MyGarage" --prepare-game "D:\MyGT6\PDIPFS" --template "D:\MyGarage\Car template"
.\.venv\Scripts\python player.py --workspace "D:\MyGarage" --test-files ".\web\examples\kenney_sedan.glb"
```

## Make a Windows release

```powershell
.\.venv\Scripts\python tools/build_windows.py
```

The ZIP and SHA-256 file appear under **dist/**. Packaging uses the player entry point and an explicit set of sources and notices. Its public distribution contains neither the starting mod pack nor the game-native donor. The previous friends ZIP has a different content and licensing boundary. Create release archives in a clean checkout and use a new player workspace for testing.

## Where to work

| Area | Start here |
|---|---|
| Input parsing and model fit | [GLB reader](../garagehub/glb.py), [source recognition](../garagehub/recognition.py), [fit setup](../garagehub/shell_setup.py) |
| Showroom/race mesh and driver | [Native builder](../garagehub/car_builder.py), [stream binding](../garagehub/body_streams.py), [animated driver](../garagehub/driver_fit.py) |
| Database, names and packing | [SpecDB](../garagehub/specdb.py), [NDB0](../garagehub/car_names.py), [archive builder](../garagehub/car_pack.py) |
| First-time setup | [FTP ranges](../garagehub/donor_source.py), [template recipe](../tools/template_recipe), [stock setup](../garagehub/stock_setup.py) |
| Desktop app | [Entry point](../player.py), [local server](../garagehub/server.py), [model tester](../web/tester.js), [workshop](../web/app.js) |

For an issue or pull request, include the exact reproduction steps, whether it affected the showroom, race, or both, and any regression check you ran. Synthetic fixtures or redistributable small models make a change easier to review. Game files, saves, console credentials and personal logs belong outside Git.

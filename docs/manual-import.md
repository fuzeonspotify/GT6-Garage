# Make a car without the GT6 Garage app

This walkthrough replaces the **Supra RZ ’97 exterior** with a mesh you control, using Blender, a small stand-alone Python example, and GTToolsSharp. You can then follow the separate-entry section to keep the original Supra and add a dealership car. The worked replacement requires no Garage installation, browser, AI, database editor or save editor.

The example is intentionally one paint material. Its Python file uses only the standard library. It reads your triangulated OBJ, writes real GT6 showroom and race body files, and checks the result after decompression. You can read or change every step. Start with the included practice shell, then substitute your own geometry. Detailed interiors, transparent windows and independent lamps require additional materials and model program branches. [Stand-alone encoder](examples/make_simple_body.py) · [Practice shell](examples/practice-shell.obj)

## 1. Gather a native donor

You need the eight-file **v029** template described in the engineering guide. If you already have it, use the folder directly containing **info**, **interior**, **hq** and **race**, and skip to section 2. The files’ fingerprints are embedded in the tutorial encoder. [Donor profile](../garagehub/car_profiles.py)

To make it independently from original inputs, use the code in **tools/template_recipe**. These scripts run in Blender or plain Python and do not import the Garage application. Make a new scratch directory and run the following commands from that directory. Use full paths for executables and for the recipe directory. Keep all scratch files away from the installed game.

1. Extract the original game’s **hq/body**, **hq/body_s**, **hq/wheel**, **interior** and **race/wheel** under **native/car/0004/0006/**. Extract the updated **info** from the **1.22 update** into the same tree. If you have the disc archive locally, the commands below extract just the five required disc entries. The local stock update also needs **9/JA/HN** for the updated **info**. If your only disc is on PS3, the small-range route is described immediately below.
2. Obtain the original OUTPISTON GLB linked in the player guide and copy it to **input/SupraRZ97/1997_toyota_supra_rz.glb**. Create **authoring** and **reports** in your scratch directory. The reference stages use Python with NumPy and Pillow. Blender 5.2 performs the decimation.
3. Extract the JSON chunk of the GLB for the texture reader, run the Blender geometry pass and then the six native-writing stages. The wheel-base copy below restores the stock race-wheel draw contexts before writing the replacement rims.

```powershell
# Set these variables to your actual paths. $python is the Python with NumPy and Pillow.
$tools = 'C:\modding\GTToolsSharp.exe'
$recipe = 'C:\modding\GT6-Garage\tools\template_recipe'
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$python = 'C:\modding\venv\Scripts\python.exe'
$disc = 'D:\GT6\PS3_GAME\USRDIR\GT.VOL'
$update = 'D:\work\PDIPFS'

& $tools unpack -i $disc -o native --indices 2439 2440 2441 2443 2446
& $tools unpack -i $update -o native --indices 37060
New-Item -ItemType Directory -Force authoring,reports | Out-Null
& $python -c "import pathlib,struct; p=pathlib.Path('input'); b=(p/'SupraRZ97/1997_toyota_supra_rz.glb').read_bytes(); (p/'gltf_metadata.json').write_bytes(b[20:20+struct.unpack_from('<I',b,12)[0]])"
& $blender --background --factory-startup --disable-autoexec --threads 4 --python-exit-code 1 --python "$recipe\prepare_neutral.py"
& $python "$recipe\build_supra_indexsafe_v002.py" --name supra_panel_batches_v014
& $python "$recipe\build_primary_paint_control.py"
& $python "$recipe\build_supra_functional_lights_v001.py"
Copy-Item candidates/supra_functional_lights_v017 candidates/supra_race_wheel_probe_v018 -Recurse
Copy-Item native/car/0004/0006/race/wheel candidates/supra_race_wheel_probe_v018/car/0004/0006/race/wheel -Force
& $python "$recipe\build_four_rims_v027.py"
& $python "$recipe\build_opaque_rim_uv_v028.py"
& $python "$recipe\repair_fvf_ownership_v029.py"
$template = (Resolve-Path candidates/supra_unique_fvf_v029/car/0004/0006).Path
```

**Small-range equivalent on PS3.** Start with local copies of the two stock index files in a temporary **PDIPFS**. For each donor manifest row with an **offset**, seek to that byte offset in **/dev_bdvd/PS3_GAME/USRDIR/GT.VOL**, read exactly **bytes** bytes in binary mode, verify **packed_sha256** and write it to the manifest’s **packed** path below the temporary PDIPFS. FTP clients can seek with **REST** followed by **RETR**. Verify the 2,048-byte disc header first. Copy **9/JA/HN** directly from the stock update. Then run **unpack** on the temporary PDIPFS with indexes **2439 2440 2441 2443 2446 37060**. Compare each loose file against **sha256**. The manifest supplies offsets for one specific disc pressing. [Numeric range manifest](../resources/donor_ranges.json) · [Readable FTP implementation](../garagehub/donor_source.py)

## 2. Prepare your own mesh in Blender

1. Import your model. Keep a separate copy of the authored original. For the one-material tutorial select just the exterior shell. Remove wheels, tyres, moving suspension, loose lenses and stand-alone interior geometry from that selection. GT6 supplies the wheels and animated mechanical parts.
2. Align the axles in metres. In the **exported OBJ** the front axle is at **Z −1.275**, rear at **Z +1.275**, with wheel centres at **X ±0.76826** and **Y 0.19565**. Match the centreline, correct the model’s facing direction, and place the body around the axle centres. Blender uses Z-up in the viewport. Use the OBJ exporter’s **−Z forward / Y up** setting when the vehicle is authored in the corresponding Blender orientation, then inspect the exported coordinates. The included OBJ is an example in final native axes.
3. Apply transforms, recalculate outward normals, triangulate, and export **Selection Only** with normals enabled. The example accepts OBJ position/normal pairs with optional UVs. Its one factory-paint slot fits **11,299 split vertices** and **20,000 triangles** in both detail levels. A UV seam or hard edge can split a vertex. A large source must be simplified or divided into more actual native draws. The example also checks that your shell fits the donor’s existing culling bounds.

## 3. Write and pack the eight native files

Run this from a new scratch folder. If you used section 1, **$template** already points to the correct folder. Set **$examples**, **$tools**, **$update** and **$python** for your machine. The example refuses an existing **Input** folder so you cannot mix the tutorial with another build.

```powershell
$examples = 'C:\modding\GT6-Garage\docs\examples'
& $python "$examples\make_simple_body.py" "$examples\practice-shell.obj" $template Input
# For your own car, use your OBJ and a different fresh output folder instead.
New-Item -ItemType Directory -Force Seed/K,Seed/9/H4 | Out-Null
Copy-Item "$update/K/4D" Seed/K/4D
Copy-Item "$update/9/H4/MS" Seed/9/H4/MS
& $tools listfiles -i Seed -o before.txt
& $tools pack -i Seed -p Input -o PatchOut
& $tools listfiles -i PatchOut -o after.txt
```

Use the append-only packer from the Windows release, or compile the same published change yourself. **Seed** contains the active archive header and table of contents. **Input** uses *logical names*, such as **car/0004/0006/race/body**. **PatchOut** holds the short, packed names the console actually loads. The manual replacement leaves the Supra’s dealership identity and database intact. [Packer source and build](build.md#build-the-packer)

### What the tutorial encoder writes

- It preserves distinct position/UV/normal combinations and writes big-endian vertices and 16-bit indices. It puts the new shell into the donor’s existing factory-paint slot in each level, **8 in HQ** and **7 in race**.
- It finds the main CPU spans through the separation table at native header offset **0xDC**. In the decoded CPU block the binding-table pointer is at **24**. Each binding has eight big-endian 32-bit words beginning with shape ID, vertex offset, index offset and bounds offset.
- It updates counts in the 48-byte shape record, writes eight shape bounds, recompresses the CPU spans, updates all span lengths and group offsets, and retains the original GPU and other groups. It emits a draw for the selected surface while retaining the two donor mechanical suffixes. The single-paint demonstration creates no custom lamp or glass branches.
- It reopens both serialized streams and compares the new vertex/index bytes against the OBJ conversion. The donor’s cockpit, driver, wheels and shadows are retained. For a production exterior, extend the material groups and update model culling bounds, lights and the driver for that vehicle. [Native model research](https://nenkai.github.io/gt-modding-hub/formats/models/mdl3_modelset3/) · [Multi-material implementation](../garagehub/car_builder.py)

## 4. Verify before installation

Read the **new** file indexes from **after.txt**. Re-extract all eight by index, then hash each extracted file against the corresponding loose input. The following PowerShell snippet checks the complete set and every original listing entry outside the replacement namespace.

```powershell
$prefix = '^car/0004/0006/(info|interior|(?:hq|race)/(?:body|body_s|wheel)) - Flags:'
$before = @(Get-Content before.txt | Where-Object { $_ -match ' - Flags: ' })
$after = @(Get-Content after.txt | Where-Object { $_ -match ' - Flags: ' })
$expected = @($before | Where-Object { $_ -notmatch $prefix })
$actual = @($after | Where-Object { $_ -notmatch $prefix })
if (Compare-Object $expected $actual) { throw 'An unrelated archive entry changed.' }
$rows = @($after | Where-Object { $_ -match $prefix })
if ($rows.Count -ne 8) { throw 'Expected eight native model files.' }
$indices = @($rows | ForEach-Object { [regex]::Match($_,'FileIndex: (\d+)').Groups[1].Value })
& $tools unpack -i PatchOut -o Verify --indices $indices
foreach ($line in $rows) {
    $name = ($line -split ' - Flags: ')[0]
    if ((Get-FileHash "Input/$name").Hash -ne (Get-FileHash "Verify/$name").Hash) {
        throw "Packed file differs from its loose source: $name"
    }
}
```

Close GT6, retain a restorable backup, then transfer the contents of **PatchOut** into your game update’s **PDIPFS**, all data first and **K/4D last**. Check the existing Supra in the garage and a single-player race. This tutorial targets a stock baseline. For an existing mod pack you must keep its complete independent inputs and routing when composing the next index. [FTP install and rollback](player-guide.md#4-install-and-test)

## 5. Optional: give the car its own identity

This changes the exercise from a body replacement to a catalogue edit. Work on copies. Use the update listing to extract **specdb/GT6/DB0106.dat** (index **64147**) and the JP and US **CarName.dat** files (**64148**, **64149**). Run GTToolsSharp’s **cryptsalsa** on a *copy* of the database with the GT6 SpecDB format key documented by the format author. The operation transforms the file in place. Open the decrypted copy with a SQLite editor and run **PRAGMA integrity_check**. [GT6 SpecDB and format key](https://nenkai.github.io/gt-modding-hub/ps3/specdb/)

| Edit in a transaction | What to verify |
|---|---|
| Pick a free car ID and model code | Check **GENERIC_CAR**, all **UseCar/CarCode/CarID** references, names in both NDB0 files and the archive namespaces. Format the eight-digit model code as **car/MMMM/NNNN**. Keep your label unique. |
| Copy car-specific parts | For every donor-owned **UseCar** row, allocate a fresh table-local ID, remap **UseCar**, and remember old → new part keys. Re-key applicable **LabelID** fields. Shared stock parts can keep their original IDs. |
| Copy **DEFAULT_PARTS** | Allocate a new row and update every reference to a cloned part, including aliased table names such as **FRONTTIRE** or intake/exhaust selections as applicable to the actual schema. |
| Copy **GENERIC_CAR** | Set **ID**, **CarID**, **CarLabel**, **ModelCode**, **Defaultparts**, and applicable dealer/arcade visibility fields. Preserve the donor settings you want to inherit. |
| Copy dependent records | Follow **MODEL_INFO**, **WHEEL**, **WHEEL_SP**, **CAR_CUSTOM_INFO**, **VARIATION**, **CAR_NAME_ALPHABET**, **CAR_NAME_JAPAN**, **CAR_VIEW**, **CAR_SEARCH** and **UC_WING** where present. Inspect their actual keys. Tables key by different combinations of numeric ID, **LabelID** or label. |
| Write both localized name maps | Decode NDB0 long and short names, retain locale ranking, append the new ID and strings, rebuild prefix encoding, and verify every previous name and rank. A SQLite-only rename is incomplete. |
| Re-encrypt and repack | Re-encrypt the database copy, decrypt another copy of the result and compare it byte-for-byte to your edited SQLite file. Put all three catalogue files and the eight native resources under their new logical names in **Input**. Repack together, re-extract and compare all eleven. |

For an auditable worked version of the database edits, the following Python fragment calls the open reference routines on loose files. It can also serve as a comparison for a fully independent SQL/NDB0 implementation. Run from the source checkout with **PYTHONPATH** pointing to it. Set **work** to your scratch directory. The expected inputs are **before.txt**, decrypted **stock.sqlite**, and the original name files under **Loose/specdb/GT6/**.

```python
from pathlib import Path
from garagehub.car_names import append_car_names, name_ids
from garagehub.car_pack import listing
from garagehub.specdb import build_database_copy, plan_install

work = Path(r"D:\manual-car")
names = {locale: (work / "Loose/specdb/GT6" / locale / "CarName.dat").read_bytes()
         for locale in ("JP", "US")}
archive = listing(work / "before.txt")
plan = plan_install(work / "stock.sqlite", mode="new_entry", donor_car_id=82,
                    name="My Practice Car", reserved_model_paths=list(archive),
                    reserved_car_ids=set().union(*(name_ids(raw) for raw in names.values())))
if not plan["ready"]:
    raise ValueError(plan)
build_database_copy(work / "stock.sqlite", work / "custom.sqlite", plan)
for locale, raw in names.items():
    output, receipt = append_car_names(raw, plan["car_id"], plan["display_name"],
                                       plan["display_name"], after_id=82)
    path = work / "NewInput/specdb/GT6" / locale / "CarName.dat"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)
print(plan["car_id"], plan["model_path"])
```

Copy **custom.sqlite** into **NewInput/specdb/GT6/DB0106.dat**, run **cryptsalsa** on that destination, and copy the eight replacement files into **NewInput/<the printed model path>/**. Use this **NewInput** in the pack command. The earlier stock replacement path belongs only in the replacement exercise. Check the resulting catalogue contains exactly one new **GENERIC_CAR** row, that the old contents are preserved, and that all eleven extracted logical files match. Then purchase, save, restart and race the new entry. [Exact cloning and preservation rules](../garagehub/specdb.py) · [NDB0 field layout](https://github.com/Nenkai/GT-File-Specifications-Documentation/blob/master/Formats/PS3/GT6/NDB0_GT6.bt)

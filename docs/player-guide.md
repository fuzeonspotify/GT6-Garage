# A first car on PS3 or RPCS3

You need a Windows x64 PC, a model you can use, and the European version of GT6 updated to **1.22**. The title ID is **BCES01893**. You can try the model tester before connecting anything. [Download the Windows ZIP](https://github.com/fuzeonspotify/GT6-Garage/releases/latest)

## 1. Copy five update files once

If you already have a local stock **PDIPFS**, use it. On PS3, open your usual FTP client, connect to your console, then open **/dev_hdd0/game/BCES01893/USRDIR/PDIPFS/**. Create a folder named **PDIPFS** on your PC. Keep the subfolders exactly as shown.

| Copy from the PS3 | Save on the PC | What it supplies |
|---|---|---|
| K/4D | PDIPFS/K/4D | Active archive header |
| 9/H4/MS | PDIPFS/9/H4/MS | Archive table of contents |
| 9/QE/0B | PDIPFS/9/QE/0B | Stock car database |
| 9/R2/Q0 | PDIPFS/9/R2/Q0 | Japanese car names |
| 9/EJ/EX | PDIPFS/9/EJ/EX | US car names used by this build |

Total **17,272,129 bytes**. On RPCS3 these files live under its **dev_hdd0/game/BCES01893/USRDIR/PDIPFS/**. A player can build on the stock roster without the three-car mod pack. Garage edits a local copy of the catalogue and exports the changed update files. Your garage save stays on the console.

## 2. Set up the car template

Extract the entire Windows download and open **GT6Garage.exe**. The first screen is **Test your cars**. Under **Starting build**, choose the path that applies to you.

**Used Garage before?** Choose **Find car template**. Select an extracted 0.4.15 installation folder, a prepared stock workspace, or the original 0.4.14 starting package folder. Garage checks all eight files. An existing stock workspace in the same player profile can already appear as ready. A newly generated install ZIP contains a finished car and serves a different purpose.

**Starting fresh?** Choose **Prepare car template**. This is a one-time step because the public download contains the software and players provide the source artwork and game resources locally.

1. Use the artist’s download page in the wizard to get OUTPISTON’s original **1997 Toyota Supra RZ**. Extract **1997_toyota_supra_rz.glb**. Select that GLB and **Blender 5.2** in the wizard. This particular model rebuilds the reference template. You can choose a different model for your player car in step 3 below. [Artist’s model](https://sketchfab.com/3d-models/1997-toyota-supra-rz-d6ed1376d91e4fd3b8e6fc061065c44f) · [Blender downloads](https://www.blender.org/download/)
2. On PS3 choose **PS3 over FTP**. Insert the supported European retail disc, close GT6 and enable your usual FTP server. Select your local **PDIPFS** folder and enter your PS3’s local IP address and FTP login. The wizard reads five ranges from the mounted disc, its header, and one small update file. The requested data totals **706,410 additional bytes**. The FTP server must support resuming a file transfer at a byte offset. The operation only reads from the console.
3. On a PC with the disc archive already present, **Disc archive on this PC** reads those same ranges locally. Include **9/JA/HN** from your update in your local PDIPFS for this route. **Six extracted donor files** is another option if you already have the original loose assets. [Required files and checksums](../resources/donor_ranges.json)
4. Wait for **Your template is ready**. Close the template window, choose **Use my stock PDIPFS**, select your local folder, and wait for **Stock GT6 1.22 is ready**. Future cars reuse this setup.

The new-player FTP route does not require copying GT.VOL, a disc image, or the full update to the PC. The prepared template lives in the player workspace, normally under **%LOCALAPPDATA%/GT6Garage_ModelTests**.

## 3. Fit your own model

1. Choose **Choose models**. A self-contained GLB is usually the easiest starting point. For OBJ or glTF, keep materials, textures and buffers beside the model, or ZIP the folder with one model inside. **Choose models** can gather local companion files. For drag-and-drop use a self-contained model or its ZIP.
2. Open its result in **Car workshop**. In the wheel check, identify the four *source* tyres if the automatic selection missed one. These identify the axle centres. Exclude imported rims, tyres and duplicate running gear from the body. Select the **GT6 factory wheels** and inspect every corner in the assembled preview.
3. Align the body to the chassis. Check which end points forward. Use the side and front/top views to place each axle under its wheel arch. Look for tyres showing through the fender, buried rims or a body sitting too low. A model with fused wheels or an unusual wheelbase may need to be separated or edited in Blender. Save the project as **.gt6car** to keep the adjustments.
4. Open **Fit the animated driver**. Mark the seat and steering wheel, choose the correct side and adjust the seat, hand and foot targets. Check from the side and above. GT6 animates its own driver. This changes placement. The original skeleton and its physical size are inherited.
5. Check paint, glass and lights. Pick **Add a new car** to keep the stock car. A compatible **Replace an existing car** build targets the slot you explicitly select. Review the car name and destination, approve the assembled preview, then build the game ZIP.
6. To add another car, select **Keep this combined build for the next car** after the successful export. Continue from that package each time. Starting again from stock produces a different branch of your roster.

## 4. Install and test

1. Close GT6. Keep a restorable copy of the current update or its complete known-good mod package, plus your save. The five setup inputs alone are insufficient to roll back an arbitrary existing mod pack.
2. Extract **your generated car ZIP**. Open its **Install** folder on the PC. Upload **the contents inside Install** into **/dev_hdd0/game/BCES01893/USRDIR/PDIPFS/**, preserving the directories. On RPCS3 copy to the equivalent folder while GT6 is stopped.
3. Send all payload files first. After every transfer succeeds, send **K/4D last**. If a transfer fails, leave the previous header active and finish verifying the payload before switching headers.
4. Start the game. Find the car by its chosen name, check it in the garage, then drive a **single-player race**. In a replay check four tyres and rims at low and high speed, the driver, glass and lights. Save, exit the game completely, reopen it, and check the car again.
5. For rollback, stop GT6 and restore the complete matching pre-install update or package, its data first and header last. Restore the matching backed-up save if required.

## When something looks wrong

| Symptom | Next check |
|---|---|
| “PDIPFS/… differs” | Recopy that **specific** stock file in binary mode. A different region, update version or modded index needs a compatible baseline. |
| Template setup reports a different disc or donor | Check the title/pressing, artist GLB and Blender version. An FTP server that ignores offsets will fail the hash check. A photo of the error and the named file helps diagnose it. |
| Wheels show in the garage and disappear in a race | Review race-wheel and tyre visibility separately, check all four source exclusions and hubs, then test the race/replay again. |
| Ready to build, but the seat or arches look wrong | Return to the assembled preview and correct the source fit. Passing a file check does not measure your particular cabin. |
| My previous custom car disappeared | The latest ZIP may have been built from stock. Reopen the last combined build containing the car and add the next model to it. |
| I expected 12–22-inch rims | The stock import carries the stock shop. The historical expanded shop is a separate modification whose choices depend on each car’s base rim size. [See the size rules](how-it-works.md#wheel-shop-sizes) |

When reporting an issue, include the Garage version, title ID/update, model format, the saved model test report and the view where the issue appears. Share the model only if you have redistribution rights.

# A first car on PS3 or RPCS3

You can try the model tester on a Windows x64 PC with your own model. To build a stock patch, use GT6 update **1.22**. Before installation, compare your game’s title ID with the ID in the generated ZIP’s `README_INSTALL.txt`. This release targets `BCES01893` in its export and its first-time PS3 FTP wizard. Other title IDs still need an installation check. [Download the Windows ZIP](https://github.com/fuzeonspotify/GT6-Garage/releases/latest)

## 1. Copy five update files once

In **RPCS3**, use the stock update folder already on your PC. Right-click **Gran Turismo 6**, choose **Open Folder → Open Game Data Folder**, and open `USRDIR/PDIPFS`. Its full location under RPCS3’s virtual drive is `dev_hdd0/game/<TITLE_ID>/USRDIR/PDIPFS/`. The game’s serial is its title ID. [RPCS3 folder menu](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/rpcs3qt/game_list_context_menu.cpp)

On **PS3**, connect your FTP client to your console and open `/dev_hdd0/game/<TITLE_ID>/USRDIR/PDIPFS/` for the installed GT6 update. If you already have a local stock **PDIPFS**, use it. Otherwise create a folder named **PDIPFS** on your PC and copy the five files below, keeping the subfolders exactly as shown.

| Copy from the PS3 | Save on the PC | What it supplies |
|---|---|---|
| K/4D | PDIPFS/K/4D | Active archive header |
| 9/H4/MS | PDIPFS/9/H4/MS | Archive table of contents |
| 9/QE/0B | PDIPFS/9/QE/0B | Stock car database |
| 9/R2/Q0 | PDIPFS/9/R2/Q0 | Japanese car names |
| 9/EJ/EX | PDIPFS/9/EJ/EX | US car names used by this build |

Total **17,272,129 bytes**. Garage edits a local copy of the catalogue and exports the changed update files. You can build directly on the stock roster.

## 2. Set up the car template

Extract the entire Windows download and open **GT6Garage.exe**. The first screen is **Test your cars**. Under **Starting build**, choose the path that applies to you.

**Used Garage before?** Choose **Find car template**. Select an extracted 0.4.15 installation folder, a prepared stock workspace, or the original 0.4.14 starting package folder. Garage checks all eight files. An existing stock workspace in the same player profile can already appear as ready. A newly generated install ZIP contains a finished car and serves a different purpose.

**Starting fresh?** Choose **Prepare car template**. This is a one-time step because the public download contains the software and players provide the source artwork and game resources locally.

1. Use the artist’s download page in the wizard to get OUTPISTON’s original **1997 Toyota Supra RZ**. Extract **1997_toyota_supra_rz.glb**. Select that GLB and **Blender 5.2** in the wizard. This particular model rebuilds the reference template. You can choose a different model for your player car in step 3 below. [Artist’s model](https://sketchfab.com/3d-models/1997-toyota-supra-rz-d6ed1376d91e4fd3b8e6fc061065c44f) · [Blender downloads](https://www.blender.org/download/)
2. On PS3 choose **PS3 over FTP**. Insert your GT6 retail disc, close GT6 and enable your usual FTP server. Select your local **PDIPFS** folder and enter your PS3’s local IP address and FTP login. The wizard checks the disc header and reads five ranges from the mounted disc plus one small update file under `BCES01893`. The requested data totals **706,410 additional bytes**. The FTP server must support resuming a file transfer at a byte offset. The operation only reads from the console.
3. On a PC with the disc archive already present, **Disc archive on this PC** reads those same ranges locally. Include **9/JA/HN** from your update in your local PDIPFS for this route. **Six extracted donor files** is another option if you already have the original loose assets. [Required files and checksums](../resources/donor_ranges.json)
4. Wait for **Your template is ready**. Close the template window, choose **Use my stock PDIPFS**, select your local folder, and wait for **Stock GT6 1.22 is ready**. Future cars reuse this setup.

The new-player FTP route does not require copying GT.VOL, a disc image, or the full update to the PC. The prepared template lives in the player workspace, normally under **%LOCALAPPDATA%/GT6Garage_ModelTests**.

## 3. Fit your own model

1. Click **Choose models**. A self-contained GLB is usually the easiest starting point. For OBJ or glTF, keep materials, textures and buffers beside the model, or ZIP the folder with one model inside. **Choose models** can gather local companion files. For drag-and-drop use a self-contained model or its ZIP.
2. Open its result in **Car workshop**. In the wheel check, identify the four *source* tyres if the automatic selection missed one. These identify the axle centres. Exclude imported rims, tyres and duplicate running gear from the body. Select the **GT6 factory wheels** and inspect every corner in the assembled preview.
3. Align the body to the chassis. Check which end points forward. Use the side and front/top views to place each axle under its wheel arch. Look for tyres showing through the fender, buried rims or a body sitting too low. A model with fused wheels or an unusual wheelbase may need to be separated or edited in Blender. Save the project as **.gt6car** to keep the adjustments.
4. Open **Fit the animated driver**. Mark the seat and steering wheel, choose the correct side and adjust the seat, hand and foot targets. Check from the side and above. GT6 animates its own driver. This changes placement. The original skeleton and its physical size are inherited.
5. Check paint, glass and lights. Pick **Add a new car entry** to keep the stock car. A compatible **Replace an existing car** build targets the slot you explicitly select. Review the car name and destination, approve the assembled preview, then build the game ZIP.
6. To add another car, select **Keep this combined build for the next car** after the successful export. Continue from that package each time. Starting again from stock produces a different branch of your roster.

## 4. Install and test

### Back up your GT6 save

We recommend making a separate copy of your game save before installing a custom car. That copy lets you restore your garage and career progress if your save is lost or corrupted.

- **RPCS3 on PC.** Stop GT6, open RPCS3’s save manager, right-click your Gran Turismo 6 save and choose **Open Save Directory**. Copy the entire save folder to your backup drive. [RPCS3 save manager](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/rpcs3qt/save_manager_dialog.cpp)
- **PS3.** Quit GT6 and connect a USB drive. On the PS3 home screen, open **Game → Saved Data Utility (PS3)**, highlight your Gran Turismo 6 save, press **Triangle**, and choose **Copy**. Select the USB drive and wait for the copy to finish. [PlayStation save backup instructions](https://manuals.playstation.net/document/en/ps3/current/game/copysavedata.html)

### Install the car

1. Close GT6. Alongside your save backup, keep a restorable copy of the current update or its complete known-good mod package. The five setup inputs alone are insufficient to roll back an arbitrary existing mod pack.
2. Extract **your generated car ZIP** and open its **Install** folder on the PC. On RPCS3, copy the contents into the local **PDIPFS** folder you found in step 1. On PS3, upload the contents into `/dev_hdd0/game/<TITLE_ID>/USRDIR/PDIPFS/` for the matching title ID. Preserve the directories on both platforms.
3. Send all payload files first. After every transfer succeeds, send **K/4D last**. If a transfer fails, leave the previous header active and finish verifying the payload before switching headers.
4. Start the game. Find the car by its chosen name, check it in the garage, then drive a **single-player race**. In a replay check four tyres and rims at low and high speed, the driver, glass and lights. Save, exit the game completely, reopen it, and check the car again.
5. For rollback, stop GT6 and restore the complete matching pre-install update or package, its data first and header last. Restore the matching backed-up save if required.

## When something looks wrong

### When a material stops the import

Look for **How to fix** under the material warning in your model test result or assembled-car check. Click **Show in Parts** to select the affected part and list the parts using its material. In the workshop you can also click **See how to fix it** at the top.

For example, a solid body panel may use an opaque police livery but have a material marked transparent. The panel explains how to keep the colour texture in Blender, export a new GLB, and assign the markings to **Body detail** in Garage. Actual windows and grilles with holes need different treatment. Follow the steps for the material named in your result.

If an older saved result only shows a warning, choose **Open in workshop** and check the assembled car again with the updated Garage.

### Other issues

| Symptom | Next check |
|---|---|
| “PDIPFS/… differs” | Recopy that **specific** stock file in binary mode. If it still differs, the file does not match the stock 1.22 data this build expects. |
| Template setup reports a different disc or donor | Check the title/pressing, artist GLB and Blender version. An FTP server that ignores offsets will fail the hash check. A photo of the error and the named file helps diagnose it. |
| Wheels show in the garage and disappear in a race | Review race-wheel and tyre visibility separately, check all four source exclusions and hubs, then test the race/replay again. |
| Ready to build, but the seat or arches look wrong | Return to the assembled preview and correct the source fit. Passing a file check does not measure your particular cabin. |
| My previous custom car disappeared | The latest ZIP may have been built from stock. Reopen the last combined build containing the car and add the next model to it. |
| I expected 12–22-inch rims | The stock import carries the stock shop. The historical expanded shop is a separate modification whose choices depend on each car’s base rim size. [See the size rules](how-it-works.md#wheel-shop-sizes) |

When reporting an issue, include the Garage version, title ID/update, model format, the saved model test report and the view where the issue appears. Share the model only if you have redistribution rights.

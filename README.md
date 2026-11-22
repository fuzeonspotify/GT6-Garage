# GT6 Garage

Bring a 3D car model into **Gran Turismo 6** as a driveable exterior. Fit the body to a GT6 chassis, choose factory wheels, place the animated driver and build an installation ZIP for PS3 or RPCS3. Model preparation runs locally on Windows. You can preview your own model before setting up any game files.

**[Download GT6 Garage for Windows](https://github.com/fuzeonspotify/GT6-Garage/releases/latest)** · **[RPCS3 on PC](#rpcs3-on-pc)** · **[PS3 over FTP](#ps3-over-ftp)**

Extract the whole Windows x64 ZIP and open **GT6Garage.exe**. The workshop opens in your browser. Both installation guides use GT6 update **1.22**. The first-time PS3 FTP wizard and car ZIPs use the tested title ID `BCES01893`. Before installing, check that the ID at the top of `README_INSTALL.txt` in your generated car ZIP matches your copy of GT6.

**We recommend backing up your GT6 game save before installing a custom car.** Keep a copy on a separate drive so you can restore your garage and career progress if your save is lost or corrupted. [How to back up your save on RPCS3 or PS3](docs/player-guide.md#back-up-your-gt6-save)

## Check your car in Blender

The Windows download includes an optional Blender add-on. Open Garage once, then in Blender use **Edit → Preferences → Add-ons → Install from Disk** and select **GT6-Garage-Blender-1.1.0.zip**. Press **N** in the 3D Viewport, open **GT6 Garage**, and click **Check & prepare car**. Review the findings and click **Save checked GLB**. Use **Copy checked file path** to paste the exact export into Garage’s **Choose models** dialog. [Blender add-on guide](docs/blender-addon.md)

## Check lights and automatic fixes

Before installing on either platform, **Choose models → Lights and automatic fixes** shows which headlights and rear lights Garage found, which it supplied, and which textures it repaired. In **Open in workshop → Build & export**, click **Check assembled car** and choose **Front** or **Rear**, then switch **Preview lights** between headlights and braking. [Lights, part detection and repair guide](docs/lights-and-repairs.md)

## RPCS3 on PC

1. **Find your update folder.** In RPCS3, right-click **Gran Turismo 6** and choose **Open Folder → Open Game Data Folder**. Open `USRDIR/PDIPFS`. This is the local folder you’ll use for setup and installation.
2. **Set up Garage once.** Under **Starting build**, choose **Find car template** if you have an earlier Garage kit or prepared workspace. For a new template, choose **Prepare car template → Disc archive on this PC**. Select your local `PDIPFS` and the `PS3_GAME/USRDIR/GT.VOL` from your disc copy. After the template is ready, choose **Use my stock PDIPFS** and select the folder from step 1. [First-time template instructions](docs/player-guide.md#2-set-up-the-car-template)
3. **Build your car.** Click **Choose models**, select your model, and click **Open in workshop**. Check all four wheels and the animated driver in the preview. Select **Add a new car entry**, confirm the fit, and build the game ZIP.
4. **Install locally.** Stop GT6 in RPCS3 and back up your current game update and save. Extract the car ZIP. Copy the contents of `Install/` into the `PDIPFS` folder from step 1, preserving the subfolders. Copy all other files first, then `K/4D` last. Start GT6 and check the car in a single-player race.

## PS3 over FTP

1. **Copy five update files to your PC.** Close GT6, connect your FTP client to the PS3, and open `/dev_hdd0/game/<TITLE_ID>/USRDIR/PDIPFS/`. For a disc copy, the title ID is the serial printed on your disc, with the hyphen removed. Copy the five paths below into a folder named `PDIPFS` on your PC, keeping their subfolders. Together they total **17.3 MB**. If you already have that stock folder on your PC, use it directly.

   ```text
   K/4D
   9/H4/MS
   9/QE/0B
   9/R2/Q0
   9/EJ/EX
   ```

2. **Set up Garage once.** Choose **Find car template** to reuse an earlier kit or workspace. For a new template, insert your GT6 disc, leave the FTP server running, and choose **Prepare car template → PS3 over FTP**. The wizard reads about **0.7 MB** of extra donor data and uses the artist’s source model to build the template locally. Then choose **Use my stock PDIPFS** and select your PC’s folder from step 1. [First-time template instructions](docs/player-guide.md#2-set-up-the-car-template)
3. **Build your car on the PC.** Click **Choose models**, select your model, and click **Open in workshop**. Fit the wheels and driver, select **Add a new car entry**, confirm the preview, and build the game ZIP.
4. **Upload the car.** With GT6 closed, back up your current game update and save. Extract the car ZIP on the PC. Upload the contents of `Install/` into the PS3’s `PDIPFS` folder from step 1, merging the subfolders. **Wait for all other transfers to finish, then upload `K/4D` last.** Start GT6 and check the car in a single-player race and replay.

To keep your first custom car when adding a second one, choose **Keep this combined build for the next car** after a successful build. [Model fitting, testing and rollback](docs/player-guide.md)

## Compatibility

Need help while importing? Click **Troubleshooting** at the top of **Model tester**, or open **Car workshop → How it works**. The guides cover missing model files, wheel fit, the animated driver, keeping earlier cars and recovering an installation. When Garage recognises an issue, it opens the matching steps beside the warning. [Player troubleshooting guide](docs/player-guide.md#when-something-looks-wrong)

| | Supported by this release |
|---|---|
| Starting game | Stock GT6 update **1.22**, or a compatible combined Garage build. Stock setup checks the contents of the five update files. Other title IDs have yet to be verified for installation. |
| Model inputs | GLB, glTF, OBJ, and a ZIP containing one model and its resources. FBX, Blender scenes and some compressed GLBs use a local Blender installation. |
| Inherited from GT6 | Chassis, handling, sound, tyre system and animated driver. The reviewed exterior profile uses the Toyota Supra RZ ’97. |
| What needs the player's judgment | The source model’s front, body dimensions, wheel arches, seat, glass and lights. Some models require manual fit adjustments or mesh editing in Blender. |
| Game changes | The exported ZIP overlays the update. Separate entries get their own identity, model namespace and names. Installing over unrelated mods requires a build made for that combination. |

The default stock export preserves the stock wheel shop. The optional extended wheel-shop implementation from the earlier three-car build is included as source with its exact prerequisites. [Wheel-shop details](docs/how-it-works.md#wheel-shop-sizes)

## Credits and community

I’m **FuzeXO**, the creator of GT6 Garage. I was the first to import a custom 3D car model into **Gran Turismo 6** as a driveable car.

Thank you to **Willyham**, **Diego**, and **Glitcher101GamingYT** for helping verify PS3 compatibility and testing custom cars on both PC and PS3.

**Discord: Coming soon.** We’d love your feedback and feature requests when the server opens.

## Learn or contribute

- The player guide covers the five-file transfer, first-time template, driver and wheel fit, adding a second car, FTP order and rollback. [Read the player guide](docs/player-guide.md)
- When a material stops your import, **How to fix** names the material and explains the next steps. **Show in Parts** selects it on your car. [Find material help](docs/player-guide.md#when-a-material-stops-the-import)
- The engineering guide follows a model through coordinate conversion, native geometry, showroom and race wheel paths, driver anchors, database allocation and pack verification. [Read how it works](docs/how-it-works.md)
- The manual describes a complete worked body replacement, an optional separate dealership entry, and independent file checks. [Read the manual import guide](docs/manual-import.md)
- The build guide covers running from source, compiling the pinned packer, tests and the Windows release. [Build or contribute](docs/build.md)

The Garage application and documentation are **MIT licensed**. The Blender extension is **GPL-3.0-or-later**. [Blender extension license](blender_addon/LICENSE) The sample sedan is Kenney’s CC0 work. A player supplies their game files and, when rebuilding the historical template, the separately licensed Supra model. Project and third-party license texts travel with the release. Unofficial community project. [License](LICENSE) · [Third-party credits](THIRD_PARTY.md)

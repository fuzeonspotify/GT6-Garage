# GT6 Garage

Bring a 3D car model into **Gran Turismo 6** as a driveable exterior. Fit the body to a GT6 chassis, choose factory wheels, place the animated driver and build an installation ZIP for PS3 or RPCS3. Model preparation runs locally on Windows. You can preview your own model before setting up any game files.

**[Download GT6 Garage for Windows](https://github.com/fuzeonspotify/GT6-Garage/releases/latest)** · **[Player guide and PS3 FTP installation](docs/player-guide.md)** · **[Make a car without the Garage app](docs/manual-import.md)**

## Your first car

1. Download the **Windows x64 ZIP** from Releases. Extract the whole ZIP into a writable folder and open **GT6Garage.exe**. Garage opens a local browser tab.
2. In **Starting build**, choose **Find car template** if you have a previous Garage kit or stock workspace. New players can choose **Prepare car template** to build one from their own game and the artist’s source model. The wizard can read the small set of donor parts directly over PS3 FTP.
3. Choose **Use my stock PDIPFS**. Select the folder containing **five files** from your own European GT6 update. Together they are **17.3 MB**. The player guide lists each file and the destination on your console. This setup is reusable.
4. Choose a model, open it in **Car workshop**, review the four wheels and driver, then choose **Add a new car**. Build the game ZIP after reviewing the assembled car.
5. With GT6 closed, upload the contents of the generated **Install** folder. Transfer **K/4D last**. Check the result in a single-player race and replay. Choose **Keep this combined build for the next car** to add another car alongside it.

## Compatibility

| | Supported by this release |
|---|---|
| Starting game | Stock European **BCES01893**, update **1.22**, or a compatible combined Garage build. The update files are checked by content. |
| Model inputs | GLB, glTF, OBJ, and a ZIP containing one model and its resources. FBX, Blender scenes and some compressed GLBs use a local Blender installation. |
| Inherited from GT6 | Chassis, handling, sound, tyre system and animated driver. The reviewed exterior profile uses the Toyota Supra RZ ’97. |
| What needs the player's judgment | The source model’s front, body dimensions, wheel arches, seat, glass and lights. Some models require manual fit adjustments or mesh editing in Blender. |
| Game changes | The exported ZIP overlays the update. Separate entries get their own identity, model namespace and names. Installing over unrelated mods requires a build made for that combination. |

The default stock export preserves the stock wheel shop. The optional extended wheel-shop implementation from the earlier three-car build is included as source with its exact prerequisites. [Wheel-shop details](docs/how-it-works.md#wheel-shop-sizes)

## Learn or contribute

- The player guide covers the five-file transfer, first-time template, driver and wheel fit, adding a second car, FTP order and rollback. [Read the player guide](docs/player-guide.md)
- The engineering guide follows a model through coordinate conversion, native geometry, showroom and race wheel paths, driver anchors, database allocation and pack verification. [Read how it works](docs/how-it-works.md)
- The manual describes a complete worked body replacement, an optional separate dealership entry, and independent file checks. [Read the manual import guide](docs/manual-import.md)
- The build guide covers running from source, compiling the pinned packer, tests and the Windows release. [Build or contribute](docs/build.md)

GT6 Garage code and documentation are **MIT licensed**. The sample sedan is Kenney’s CC0 work. A player supplies their game files and, when rebuilding the historical template, the separately licensed Supra model. Project and third-party license texts travel with the release. Unofficial community project. [License](LICENSE) · [Third-party credits](THIRD_PARTY.md)

# Prepare a car in Blender

The **GT6 Garage** sidebar checks the car you have open in Blender, prepares a separate GLB, and runs Garage’s model tester before you save that GLB. It works offline. The source and texture checks run before you set up game files. A configured Garage template also enables checks of the native garage and race bodies when the source fit passes.

## Install once

1. Extract **GT6 Garage 0.4.19** or newer for Windows. Double-click **GT6Garage.exe** once so Blender can find your copy.
2. In Blender 4.2 or newer, open **Edit → Preferences → Add-ons**. Open the menu in the upper right, choose **Install from Disk**, and select **GT6-Garage-Blender-1.0.0.zip** from the extracted Garage folder. Give Blender the add-on ZIP itself. Enable **GT6 Garage** if needed.
3. In the 3D Viewport, press **N** and click the **GT6 Garage** tab. If the panel asks for the application, use its folder picker to choose **GT6Garage.exe**.

## Check a car

1. Open or import your car in Blender and switch to **Object Mode**. Hide other meshes such as the default cube, or select the whole car and tick **Selected meshes only**.
2. Click **Check & prepare car**. The add-on exports a temporary static snapshot and checks the model Garage will actually read.
3. Click a **Choose** or **Fix in Blender** item for its instructions. **Select affected object** locates the source mesh. For example, choose **Solid body / markings** for a solid chassis, and **Glass** for a window that should keep its transparency. Click **Check & prepare car** again to apply the choices. A choice applies to all exported surfaces sharing that material. Split the material in Blender if different parts need different treatments.
4. Click **Save checked GLB**, choose a filename, then open it with **Choose models** in GT6 Garage. The saved file is the exact copy checked in step 2 or 3. Run another check after editing the car.

## What Garage can prepare automatically

| Source issue | What happens on the export copy |
|---|---|
| Normal map on a textured chassis | Garage bakes the bumps into base colour with a fixed side light. Markings and the resized image’s alpha channel are preserved. The painted highlights stay in place as track lighting changes. |
| Ambient occlusion | The red occlusion channel and the material’s strength darken the colour image. Baking requires matching UV coordinates and wrapping. |
| Oversized base colour | Garage reduces the image proportionally to fit its texture reader. A normal or occlusion bake uses a maximum edge of 2048 pixels. |
| Solid paint with a livery | The exported surface receives the **Body detail** role so the colour image carries the markings. |
| Transparent flag on solid art | Garage checks the image, colour factor and vertex alpha. Genuine transparency without a clear purpose gets a player choice. Cutout grilles that need physical holes require mesh editing. |
| Source reflections, glow and lenses | Garage uses supported native finishes and reports substitutions. It preserves evidence used to find working lights. For an ambiguous finish, choose the surface in the panel. |
| Scene or texture problems | The panel names detected missing images, missing UVs, unsupported procedural inputs, meshes without faces, and zero-scale objects. Other geometry and material checks run against the exported GLB. |

## Wheels, lamps and the animated driver

For a separate tyre mesh, select it, choose **Tyre** and its corner, and click **Mark selected mesh**. Left and right follow the seated driver’s view. This button intentionally adds a role tag to the selected Blender object and supports **Undo**. You can also tag a separate rim or lamp. Separate mixed moving and stationary surfaces before tagging them.

**Review in Garage** means the source can be saved, and the report identifies an assembly issue such as wheel fit or a supplied lamp mount. Open **Car workshop → Build & export** to adjust the assembled preview, seat and lights. Verify the finished model in a single-player race and replay. Installing by PS3 FTP or RPCS3 follows the regular player guide. [Player guide](player-guide.md)

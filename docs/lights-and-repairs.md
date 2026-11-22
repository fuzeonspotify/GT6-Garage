# Check your lights and fix an import

1. Open Garage and click **Choose models**. For a car you tested with an older release, choose the original model again to get a fresh report.
2. Expand **Lights and automatic fixes** under the car’s name. Read the front and rear results separately. Click a model lamp to open that part in the workshop. A material repair also has a **Show in Parts** button.
3. Open **Build & export**. Click **Check wheels, lights & parts**, read the proposed changes, then click **Apply to preview**. The **Lights** menu lets you keep both ends, supply both, or keep just the front or rear lights from your model.
4. Confirm the source parts, then click **Check assembled car** (shown as **Preview assembled car** on some profiles). Use **Front** and **Rear**. With **Headlights on**, look for lit front lenses and dim rear running lights. With **Braking**, look for bright rear lenses and any separate stop lamp. **Headlights + braking** shows both together. The preview highlights the assigned surfaces. Check beam direction, brightness and visibility in GT6 during a single-player race or replay.

## When a light is missing or the wrong part lights up

In **Car workshop**, select the emitting lens in **Parts**. Assign **Headlight**, **Taillight** (running and brake) or **Brake light** (a separate stop lamp). Leave a housing as **Body detail** and a clear cover as **Glass**. Run the setup and preview again. If several different functions share a single mesh and material, split their faces in Blender before assigning roles. Explicit Blender role tags and saved part choices take priority over suggestions.

Garage reads component and material names. It can also match separate left and right light clusters with similar size, position, material and colour. Police light bars, indicators, fog and reverse lights can remain visible as model details. The current game profile provides working headlamps, rear running lamps and brake lamps. A one-sided headlight or a brake-only rear assignment stays flagged so you can identify the missing lens.

When an end has no model lamps, Garage searches its bodywork for two mounting surfaces. If you see a mount warning, expand **Adjust supplied lights** and move that end’s **height** or the shared **distance from centre**. Each supplied lens needs solid bodywork underneath its centre and eight rim points. Garage checks those contacts again after making the garage and race versions, with a maximum movement of 1.5 mm.

## What Garage fixes in the working copy

| What Garage finds | What happens |
| --- | --- |
| A normal or ambient-occlusion map with compatible UVs | Garage bakes fixed shading into the base colour and keeps its alpha channel. Markings remain visible. The fixed shading stays in the texture when the game's light moves. |
| A large supported embedded image | Garage scales the working texture to the export budget. The report gives its old and new dimensions. |
| A labelled solid panel or seat marked transparent | Garage checks the colour factor, texture alpha, vertex alpha and transmission. When they establish a fully opaque surface, it corrects the material. Genuine transparency stays available for review. |
| Factory paint inferred on a textured livery | Garage keeps the markings on a **Body detail** surface. An explicit factory paint tag remains a player choice. |
| A textured body too large for the native stream | Garage can reduce opaque textured detail while taking UVs and normals into account. The tester lists the affected detail level. Lamp emitters and transparent optics keep their geometry. |
| A stationary wheel arch, steering wheel or lamp housing | Garage uses the part’s name and geometry to distinguish it from tyres, rims and working lamps. Review the four wheel positions before building. |

If Garage requests a choice, read the material’s steps. Different UV maps may require a bake in Blender. Grilles with real holes, mixed wheel/body meshes and genuinely transparent upholstery can need an artist’s decision. An exported Garage project carries its working model and the original normalized import input when preparation changed it. Your original model file on disk stays available.

The Blender sidebar uses the same Garage checker. See the [Blender add-on guide](blender-addon.md) for installation and material choices. For installation, follow the [PS3 FTP or RPCS3 guide](player-guide.md).

# How GT6 Garage builds a car

GT6 identifies a car in three places. A database row describes the car and its parts. Localized name maps supply display strings. A volume index maps names such as **car/0004/0006/race/body** to packed files. A separate dealership entry needs a coherent change to all three. A visual replacement for the same slot can reuse its existing identity.

## The donor and the source

The player profile was developed against Toyota Supra RZ ’97, car ID **82**, model path **car/0004/0006**. A template is a known arrangement of native model programs, draw calls, textures, shape records and animated mechanical parts. The eight SHA-256 fingerprints in the profile establish exactly which revision the writer understands. Other cars can have different layouts, even when their files have the same names. [Profile contract and hashes](../garagehub/car_profiles.py)

| Native asset | Purpose |
|---|---|
| info | CAR5 configuration, including driver targets and projector placement |
| interior | The donor’s native cockpit and associated resources |
| hq/body and hq/body_s | Showroom body metadata, draw programs and streamed geometry |
| race/body and race/body_s | On-track equivalents with their own geometry and detail budget |
| hq/wheel and race/wheel | Four corner assemblies under the rendering rules for each view |

The public template builder starts with six original game files and OUTPISTON’s separately obtained GLB. It imports the GLB in Blender, saves and reopens the working scene, then decimates HQ and race separately. Reloading the scene stabilizes vertex ordering for the reference hashes. Subsequent stages create the indexed body, correct the primary paint shader, route functional lamps, build four race rims, fix their texture coordinates and give mutable race-wheel vertex declarations distinct storage. The result must match *all eight* known native files before it can be used. [Template compiler](../garagehub/template_build.py) · [Individual authoring stages](../tools/template_recipe)

The disc-range reader uses the retail disc’s table of contents and the update’s original file indexes. Five unchanged donor payloads are located inside the mounted disc. The sixth, **info**, comes from update file **9/JA/HN**. Their packed names and hashes let the public builder assemble a tiny temporary update tree for GTToolsSharp. It verifies the six *unpacked* files as well. This is the only first-time step that needs access to the donor. Subsequent updates use the five setup files listed in the player guide. [Range manifest](../resources/donor_ranges.json) · [Reader](../garagehub/donor_source.py)

## From triangles to an assembled car

Garage resolves glTF scene transforms and material assignments before fitting. All final positions are in metres. Native **+X is right, +Y is up, −Z is forward**. Transform positions with the full matrix, normals with the inverse transpose, and reverse triangle winding when the transform reverses handedness. Preserve split normals and UV seams when two corners share a geometric position. The retained project stores inputs and editable choices for subsequent revisions. [Model reader](../garagehub/glb.py) · [Geometry conversion](../garagehub/car_geometry.py)

The default wheel workflow measures the source tyre envelopes, identifies attached running gear, and fits the retained body against four GT6 hubs. In the reviewed profile the axle centres are at **Z ±1.275 m**, the hub centres at **X ±0.7682615 m, Y 0.19565 m**. Front tyre radius is **0.32165 m**, rear radius **0.3179 m**. These describe this donor. They are starting points for judging the wheel arches of a different car. Fused tyres, a reversed model or a large wheelbase mismatch can make automatic fitting ambiguous. [Fit setup](../garagehub/shell_setup.py) · [Profile dimensions](../garagehub/car_profiles.py)

The workshop replaces the source model’s wheel assemblies with game wheels in the player workflow. The player can inspect source exclusions and choose the placement and available factory wheel settings. Surface materials map to native paint, trim, glass and lamp families. Normal maps, source PBR materials and custom liveries can differ perceptibly once rendered by GT6.

## Why the garage, track and driver need separate checks

A garage screenshot exercises **hq** assets. A race loads **race** assets, with additional tyre, rim, shadow and spin contexts. The template keeps the race tyre and fast-spin representations. In the historical race-wheel repair, giving several flexible vertex format (FVF) entries the same mutable field table let the loader rebase shared pointers more than once. The repair gave each owner its own table and verified that positions, indices, UVs, textures and draw programs survived. This explains how a wheel can look correct in one view and fail in another. [Race-wheel repair](../tools/template_recipe/repair_fvf_ownership_v029.py) · [Native structure checks](../garagehub/native.py)

The driver remains GT6’s animated character. The fitted values update the CAR5 steering target, both pedal targets and the seat/hip. Moving the seat alone can leave hands or feet floating. The player can review the side and top views together. The code checks the expected driver block before writing. The silhouette is a placement aid, and stature is inherited from GT6. [Driver record and targets](../garagehub/driver_fit.py)

## Native mesh layout

For ordinary generated body draws in this profile, each vertex occupies **24 bytes**. Offsets **0–11** contain big-endian XYZ floats, **12–15** the RSX packed normal, and **16–23** two big-endian UV floats. Indices are big-endian unsigned 16-bit triangle lists. Partition by material and draw budget before encoding. The profile caps a draw at **20,000 triangles**. Independently check the maximum vertex index and source-surface coverage. Draws using glass, paint variants or lamps need their corresponding shader, material keys and command branches. [Stream validator](../garagehub/ordinary_streams.py) · [Reference native model format](https://nenkai.github.io/gt-modding-hub/formats/models/mdl3_modelset3/)

The body writer places ordinary geometry in the main CPU stream of **body_s**, preserves unrelated groups, writes shape and stream bindings in **body**, and emits the donor’s mechanical draw suffix. It computes bounds for the new geometry. For this exact template the preserved CPU prefix is **108,160 bytes**. Mixing offsets from the disc donor with the prepared profile will address different structures. Final checks decode the emitted streams, compare vertex/index digests and validate the native file again. [Body and stream writer](../garagehub/body_streams.py) · [Native builder](../garagehub/car_builder.py)

## A separate dealership entry

The stock database is an encrypted SQLite file under **specdb/GT6/DB0106.dat**. The program decrypts a copy, identifies a compatible donor, checks schemas and builds a plan. It reserves car IDs from referenced tables and both name maps. For a model code it examines occupied database codes and packed **car/NNNN/NNNN** namespaces. Eight decimal digits **MMMMNNNN** map to **car/MMMM/NNNN**. A new car ID and a model code are different things. [Database planner](../garagehub/specdb.py) · [GT6 SpecDB reference](https://nenkai.github.io/gt-modding-hub/ps3/specdb/)

A new entry gets its own generic car, default-parts row, applicable car-owned part records, model references, wheel rows, search and dealer data. References inside the copied default parts are remapped to the new keys. Shared components retain the intended donor settings. Both locale-specific **NDB0** files receive the new long and short names while preserving the old entries, ordering and prefix encoding. The builder verifies the database after encrypting and decrypting it again. [Name-map writer](../garagehub/car_names.py) · [Identity pipeline](../garagehub/identity_pipeline.py) · [NDB0 layout](https://github.com/Nenkai/GT-File-Specifications-Documentation/blob/master/Formats/PS3/GT6/NDB0_GT6.bt)

## Packing and verifying the overlay

The packer starts from copies of the stock header/TOC and all loose inputs belonging to the selected combined build. Our small change to GTToolsSharp allocates fresh payload indexes when a logical path is written. The builder lists the result, checks that unrelated entries retain their previous routing, then re-extracts changed inputs by their new indexes and compares their hashes. The generated manifest puts the header **K/4D** at the end. This lets the previous index remain active until all new data is in place. [Append-only change](../third_party/GTToolsSharp/always_append.patch) · [Archive checks](../garagehub/car_pack.py) · [GTToolsSharp](https://github.com/Nenkai/GTToolsSharp)

## Wheel-shop sizes

The earlier three-car wheel-shop modification expands exchangeable wheels to offsets of up to **−4 to +4 inches** around each car’s factory wheel sizes. Nine choices on one car do not imply the absolute endpoints 12 and 22 on every car. Front and rear bases can also differ. The stock importer preserves the existing shop settings. Choosing factory wheels in the workshop and expanding the in-game shop are separate operations.

The shop code checks the database fingerprint, extends the permitted maximum values in **WHEEL** and eligible **WHEEL_EX** rows, and adds the **GT6GARAGE_RIM_LIMITS** view. The menu reads offsets and displays the corresponding front and rear sizes. Both the **gtauto.adc** script and the live **WheelRoot.adc / PaintRoot.adc** components in **gtauto.mpackage** need corresponding changes. The published patcher pins its original three-car database and menu inputs. Porting it to stock or to another mod combination requires validating those inputs and the save/reload behavior for that combination. [Wheel-shop source](../tools/wheel_shop.py)

## What the checks establish

An export that passes the archive checks contains the intended native bytes and a structurally consistent catalogue. A particular model can still clip, illuminate incorrectly or look wrong at speed. A useful acceptance run includes showroom, single-player race, replay, purchase, save, restart and a check that earlier cars are still available. Historical regression records describe specific revisions and their known gaps. [Engineering history](engineering_history.json)

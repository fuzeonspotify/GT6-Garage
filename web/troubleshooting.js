// The same offline guides appear next to a problem and in the help index.
const guides = {
  startingBuild: {
    title: 'Which folder goes in “Choose combined baseline”?',
    steps: [
      'Starting with stock GT6? Open **Model tester → Starting build → Prepare car template**. If you already prepared a template in an earlier Garage kit, use **Find car template** instead.',
      'When the template is ready, click **Use my stock PDIPFS** and select the update folder on your PC. Wait for **Stock GT6 1.22 is ready**. Garage fills in the starting package for you.',
      'Continuing a Garage build? Click **Choose combined baseline** and select the editable package folder containing **package_receipt.json**, **Input**, **PatchOut** and **BaselineSeed** together. For a previous car build, this is the **package** folder inside that build.',
      'Your custom model goes under **Choose models**. The **Install** folder in a car ZIP goes into the game after the build.'
    ]
  },
  stockFiles: {
    title: 'Where do I find the stock update files?',
    steps: [
      'In **RPCS3**, right-click Gran Turismo 6 → **Open Folder → Open Game Data Folder**. Open **USRDIR**, then **PDIPFS**. Use this folder in Garage. For a first-time template made with **Disc archive on this PC**, the wizard also needs your local disc’s GT.VOL and the update file 9/JA/HN.',
      'For **PS3 over FTP**, close GT6. On the console side of your FTP client, open **/dev_hdd0/game/<TITLE_ID>/USRDIR/PDIPFS/**. On the PC side, create a folder called **PDIPFS**. Keep the same subfolders when copying **K/4D**, **9/H4/MS**, **9/QE/0B**, **9/R2/Q0** and **9/EJ/EX** from the console. Together they are about 17.3 MB.',
      'For first-time setup on PS3, leave your GT6 disc inserted and choose **PS3 over FTP** in the template wizard. It reads about 0.7 MB more from the console. Your disc archive stays on the PS3.',
      'If Garage names a missing or changed file, recopy that file from your unmodified 1.22 update using binary transfer mode. If your update already contains Garage cars you want to keep, open their matching combined build. The first-time FTP wizard uses title ID **BCES01893**.'
    ]
  },
  modelFiles: {
    title: 'My model is missing textures, a .bin file or a material file',
    steps: [
      'Read the filename in the warning. For **OBJ**, keep the .obj, its .mtl and the images the .mtl references together. For **glTF**, keep the .gltf, its .bin and its images in their original folders.',
      'If the model refers to **textures/body.png**, keep **body.png** in a subfolder called **textures** beside the model. A missing file must come from the original model download or a fresh export.',
      'Click **Choose models** in Model tester, or **Open file** in Car workshop, and select the model on disk. For drag and drop, ZIP one .obj, .gltf or .glb together with its companion files, or use a self-contained GLB.',
      'If the warning names an unreadable image, export a PNG or JPEG in your editor and update the material to use it.'
    ]
  },
  exportModel: {
    title: 'My ZIP has several cars, or the model needs Blender',
    steps: [
      'Extract the download. Pick the one car you want. A Garage model ZIP accepts one .glb, .gltf or .obj plus its companion files. If the download contains several versions, give each version its own folder.',
      'For an **FBX** or **.blend** file, open it directly with **Choose models** on a PC with Blender installed. If conversion fails, open the model in Blender and select the complete car.',
      'In Blender choose **File → Export → glTF 2.0**. Choose **glTF Binary (.glb)**, include the car and its materials, and turn off mesh compression. Import that new GLB into Garage.'
    ]
  },
  materials: {
    title: 'A warning says a material needs a separate native recipe',
    steps: [
      'Look below the warning for **How to fix** and the material name. Click **Show in Parts** to find the panels that use it.',
      'If an old result says **Source texture exceeds the 16 MP decode budget**, run a fresh test in Garage 0.4.20 or newer. After saving from the Blender panel, use **Copy checked file path** and paste it into **Choose models → File name**. If the current result names an image over the source limit, resize that image to 8192 × 8192 or smaller in a copy.',
      'In Blender, press **N → GT6 Garage → Check & prepare car** to bake normal-map detail, resize textures and review transparent surfaces. Install the Blender add-on ZIP from the Garage download using **Edit → Preferences → Add-ons → Install from Disk**.',
      'Check what those parts should look like. Solid painted markings, see-through windows and a grille with holes each need a different repair. Follow the steps for the named material, export a copy from your editor and test it again.',
      'If an older result only shows the warning, click **Open in workshop → Build & export → Check assembled car** to get the current repair instructions.'
    ]
  },
  sourceTyres: {
    title: 'Garage can’t find four tyres',
    steps: [
      'In **Car workshop**, check **Front direction** and **Source up axis**. Left and right follow the seated driver’s view facing forward.',
      'Open **Build & export → Select source tyres by hand**. At each corner choose the complete outer rubber tyre or tread. Click **Check wheel fit**, inspect the four assignments, then **Apply to preview**.',
      'If all four tyres share one component containing only rubber, select it in **Parts**, set **Component type → Tyre**, then use **Find four tyre corners in this group**. Review the proposed corners before applying.',
      'For a body with its wheels already removed, choose **Toyota Supra RZ ’97 · place a wheel-free body** and line up the arches with the supplied wheels. If the rubber and body are fused together, separate them in a copy of the model before exporting.'
    ]
  },
  wheelFit: {
    title: 'The wheels sit outside the arches or the tyre sizes don’t match',
    steps: [
      'Read the failing corner in **Wheel fit**. Check the source tyre pick and **Front direction**. If the whole model looks tiny or huge, check **Metres per source unit**. Use **0.01** for centimetres or **0.001** for millimetres.',
      'If you know the distance between the front and rear axle centres, enter it in **Reference wheelbase (m)** and click **Set units from wheelbase** after reviewing the tyres.',
      'To use the game tyre diameter, choose **Toyota Supra RZ ’97 · GT6 stock size**. Choose **Find a compatible fit automatically → Check wheel fit**. Apply the placement, then check the assembled car from the side and top.',
      'If all four hubs cannot line up at the same body scale, check the model’s axle spacing and width in your 3D editor. Changing tyre diameter alone cannot move the GT6 hubs.'
    ]
  },
  raceWheels: {
    title: 'Wheels appear in the garage but disappear in a race',
    steps: [
      'In **Build & export**, run **Check assembled car**. In the wheel table, compare the **Garage** and **Race** visibility for each rim and tyre. Read the part names in the failing corner’s message.',
      'Open **Source components**. If a named part is an original wheel that the GT6 wheels replace, choose **Exclude this part**, enter the reason and click **Save component choices**. If the fender covers the wheel, adjust the body placement and check the arches again.',
      'Recheck the assembly, approve the corrected preview and build a fresh car ZIP. Install that ZIP, drive a **single-player race**, then inspect all four wheels in the replay.'
    ]
  },
  driverSeat: {
    title: 'The driver is in the wrong seat or sits too high',
    steps: [
      'Check the assembled car, then open **Fit the animated driver**. Choose **Left seat** or **Right seat** to start on the correct side.',
      'Click **Mark seat and steering wheel**. In the top view, click the middle of the driver’s seat cushion, then the centre of the steering wheel. Use the side view to set their heights.',
      'Choose the hands or a pedal under **Move** to adjust that target. Click **Apply and preview**, review the new position and approve the assembly again.',
      'GT6 keeps its animated driver at its original size. **Reach** moves the hand and foot targets. If the entire cabin looks too small, check the car’s units and wheel fit before moving the seat again.'
    ]
  },
  shopSizes: {
    title: 'Why don’t I see every rim size in the GT Auto shop?',
    steps: [
      'A build started from stock GT6 keeps the stock wheel shop. The **Wheel set** choice in Garage fits the wheels to your imported body. GT Auto uses the shop settings in your starting build.',
      'If you already use the Garage build with the expanded wheel shop, click **Choose combined baseline** and select its editable package before adding your next car.',
      'With the expanded shop, an eligible wheel can go up or down four inches from the car’s factory size. For example, a 17-inch base can offer 13 through 21 inches. Check the front and rear sizes shown for your car.'
    ]
  },
  keepCars: {
    title: 'How do I add a second car and keep my first one?',
    steps: [
      'After building the first car, click **Keep this combined build for the next car**. If the app has been closed, use **Choose combined baseline** and select the editable **package** folder from that completed build.',
      'Import the next model and choose **Add a new car entry**. Build and install the new combined ZIP. Repeat from the most recent combined build each time.',
      'If a car disappeared after installing a ZIP built from stock, reopen the last combined build that contained it and add the newer car there. The resulting ZIP contains both additions.'
    ]
  },
  installPC: {
    title: 'How do I install or restore a car on RPCS3?',
    steps: [
      'Stop GT6. Back up your game save and the current update or complete working mod package. Extract your generated **car ZIP** and compare the title ID in **README_INSTALL.txt** with your game.',
      'In RPCS3, right-click **Gran Turismo 6 → Open Folder → Open Game Data Folder**. Open **USRDIR/PDIPFS**. Copy the contents of the ZIP’s **Install** folder into that PDIPFS folder, keeping the subfolders.',
      'Copy every file except **K/4D** first. After those copies finish, copy **Install/K/4D** into **PDIPFS/K/** last. Start GT6 and test the car in a single-player race.',
      'To undo an install, stop GT6 and restore your complete matching update or mod package, data first and K/4D last. Restore the matching backed-up save if needed.'
    ]
  },
  installPS3: {
    title: 'How do I install by PS3 FTP or recover a failed transfer?',
    steps: [
      'Close GT6. Back up your save and complete working update or mod package. Extract your generated **car ZIP** on your PC and check the title ID in **README_INSTALL.txt** against your console’s game.',
      'On the PC side of your FTP client, open **Install**. On the PS3 side, open **/dev_hdd0/game/<TITLE_ID>/USRDIR/PDIPFS/**. Upload the contents from PC to PS3, merging their subfolders. Queue every file except **K/4D**.',
      'Wait until all queued files finish successfully. If any transfer fails, retry it first. Then upload **Install/K/4D** to **PDIPFS/K/** as the final file. Start GT6 and check the car in a single-player race.',
      'If you already sent K/4D and the game no longer loads, close GT6 and restore the complete matching backup, with its K/4D last. The five first-time setup files alone cannot restore an existing mod pack.'
    ]
  }
};

// Keep unknown failures on their own diagnosis. The full index is always available.
const errorTopics = new Map([
  ['missing_resource','modelFiles'], ['external_resource','modelFiles'], ['unsupported_texture','modelFiles'],
  ['ambiguous_model','exportModel'], ['missing_model','exportModel'], ['blender_needed','exportModel'], ['blender_conversion','exportModel'],
  ['baseline_identity','startingBuild'], ['car_template','startingBuild'], ['template_identity','startingBuild'], ['stock_setup','stockFiles'],
  ['shell_mechanical','sourceTyres'], ['wheel_fit','wheelFit'], ['shell_surface_realization','materials'], ['texture_size','materials']
]);

export function forError(code) {
  return errorTopics.get(code) || null;
}

export function forWheelCheck(check) {
  if (!check || check.passed === true) return null;
  if (check.status === 'unmeasured') return 'sourceTyres';
  if (check.status === 'incompatible' && check.corners?.some(row => row.issues?.length)) return 'wheelFit';
  if ((check.status === 'incompatible' && check.visibility?.race) ||
      (check.search?.matched === false && check.search.placements_sampled > 0)) return 'raceWheels';
  if (check.status === 'compatible' && check.wheel_file_error) return 'startingBuild';
  return check.status === 'incompatible' || check.search?.matched === false ? 'wheelFit' : null;
}

export function forTestResult(row) {
  if (row.build_attempt?.result === 'failed') return forError(row.build_attempt.code);
  if (row.build || row.material_help) return null;
  if (row.status === 'needs_game_files') return 'startingBuild';
  if (row.status !== 'needs_attention') return null;
  if (row.messages?.includes('Source texture exceeds the 16 MP decode budget.')) return 'materials';
  if (row.code) return forError(row.code);
  if (row.stage === 'wheel placement' && row.wheels) {
    if (row.wheels.status) return forWheelCheck({status: row.wheels.status});
    return row.wheels.corners < 4 || Object.values(row.wheels.diameter_ratio || {}).includes(null) ? 'sourceTyres' : 'wheelFit';
  }
  if (row.stage === 'garage and race geometry' && row.wheels?.visibility?.race) return 'raceWheels';
  return null;
}

export function panel(topic, open = false) {
  const guide = guides[topic];
  const details = document.createElement('details');
  details.className = 'issueGuide';
  details.open = open;
  const summary = document.createElement('summary');
  summary.textContent = guide.title;
  const steps = document.createElement('ol');
  for (const step of guide.steps) {
    const item = document.createElement('li');
    step.split('**').forEach((phrase, index) => {
      if (index % 2) {
        const label = document.createElement('strong');
        label.textContent = phrase;
        item.append(label);
      } else item.append(phrase);
    });
    steps.append(item);
  }
  details.append(summary, steps);
  return details;
}

export function mount(root) {
  for (const host of root.querySelectorAll('[data-how-to]')) {
    const topics = host.dataset.howTo === 'all' ? Object.keys(guides) : host.dataset.howTo.split(' ');
    host.replaceChildren(...topics.map(topic => panel(topic)));
  }
}

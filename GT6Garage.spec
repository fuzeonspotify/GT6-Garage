# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from importlib.metadata import distribution
import os
import sys
from pathlib import Path

datas = [('web', 'web'), ('docs', 'docs'), ('tools/ui_settings_regression.cjs', 'tools')]
datas += [('tools/ui_template_shell_regression.cjs', 'tools')]
datas += [('tools/chassis_diagnostics.py', 'tools'), ('tools/chassis_probe_session.py', 'tools')]
player = os.environ.get('GT6_GARAGE_PLAYER_BUILD') == '1'
if player:
    datas = [('web', 'web')]
datas += [('resources/template-credits.txt','resources'),('resources/donor_ranges.json','resources'),
          ('tools/template_recipe','tools/template_recipe'), ('LICENSE','licenses/garage')]
datas += [('resources/convert_static.py','resources')]
datas += [('third_party/meshoptimizer/LICENSE.md','licenses/meshoptimizer')]
for package in ('numpy','pillow','fast-simplification','pyinstaller'):
    dist = distribution(package)
    for item in dist.files or ():
        path = Path(item)
        if path.parts[0].endswith('.dist-info') and ('licenses' in path.parts or 'license' in path.name.lower() or 'copying' in path.name.lower()):
            datas.append((str(dist.locate_file(item)), str(Path('licenses') / package / Path(*path.parts[1:-1]))))
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    datas.append((str(python_license), 'licenses/python'))
binaries = []
hiddenimports = ['tkinter','tkinter.filedialog','tkinter.messagebox']
tmp_ret = collect_all('fast_simplification')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('meshoptimizer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['player.py' if player else 'run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GT6Garage',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['web/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GT6Garage',
)

"""Create the installable Blender extension archive, containing only its source and license."""
import argparse
import os
from pathlib import Path
import tempfile
import tomllib
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / 'blender_addon'
VERSION = tomllib.loads((SOURCE/'blender_manifest.toml').read_text(encoding='utf8'))['version']
FILENAME = f'GT6-Garage-Blender-{VERSION}.zip'


def package_addon(output):
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
        partial = Path(stream.name)
    try:
        with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in ('blender_manifest.toml','__init__.py','scene_check.py','LICENSE'):
                # Stable output also lets us compare the standalone and bundled downloads.
                info = zipfile.ZipInfo(name, (2026,1,1,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info,(SOURCE/name).read_bytes())
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip():
                raise ValueError('Blender extension ZIP could not be verified.')
        os.replace(partial,output)
    finally:
        partial.unlink(missing_ok=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=SOURCE.parent/'dist'/FILENAME)
    package_addon(parser.parse_args().output)

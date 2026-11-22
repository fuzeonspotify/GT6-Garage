"""Rebuild the original eight-file v029 template from separately obtained inputs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile

from .car_profiles import ASSETS, validate_template
from .donor_source import MODEL, check_stock, extract_donor, ftp_reader
from .errors import GarageError
from .stock_setup import RESOURCES, TEMPLATE_CREDITS
from .util import hash_file

ARTIST_MODEL = 'b05092a00374905e96d7c954dba702330d361a1a73eb0813044ef796ff2669b6'
RECIPE = RESOURCES.parent / 'tools/template_recipe'
STAGES = ('build_supra_indexsafe_v002', 'build_primary_paint_control',
          'build_supra_functional_lights_v001', 'build_four_rims_v027',
          'build_opaque_rim_uv_v028', 'repair_fvf_ownership_v029')


def run_stage(name, extra):
    if name not in STAGES:
        raise GarageError('Unknown template stage.')
    file = RECIPE / (name + '.py')
    sys.path.insert(0, str(RECIPE))
    sys.argv = [str(file), *extra]
    runpy.run_path(str(file), run_name='__main__')


def stage_command(name):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--profile-stage', name]
    return [sys.executable, '-B', str(RECIPE / (name + '.py'))]


def blender_path(value=None):
    if value and Path(value).is_file():
        return Path(value).resolve()
    if value:
        raise GarageError('Choose the Blender executable.')
    found = shutil.which('blender')
    if found:
        return Path(found)
    return next(iter(sorted((Path(os.environ.get('ProgramFiles', '')) / 'Blender Foundation')
                            .glob('Blender 5.2*/blender.exe'))), None)


def build_template(source, output, *, stock=None, update=None, disc=None, read_ftp=None,
                   packer=None, blender=None, progress=print):
    source, output = Path(source), Path(output).resolve()
    if output.exists():
        return Path(validate_template(output)['root'])
    if not source.is_file() or hash_file(source) != ARTIST_MODEL:
        raise GarageError('Choose the original 1997 Toyota Supra RZ GLB by OUTPISTON. This recipe checks its exact version.')
    blender = blender_path(blender)
    if not blender:
        raise GarageError('Install Blender 5.2 and select its executable.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.garage-template-', dir=output.parent) as temporary:
        work = Path(temporary)
        if stock:
            donor = check_stock(stock)
        elif update and packer and (disc or read_ftp):
            donor = extract_donor(update, work / 'read', packer, disc=disc, read_ftp=read_ftp, progress=progress)
        else:
            raise GarageError('Choose the six extracted donor files, or a stock update and a local disc or PS3 FTP connection.')
        for name in ASSETS:
            if (donor / name).is_file():
                dest = work / 'native' / MODEL / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(donor / name, dest)
        inputs = work / 'input'
        (inputs / 'SupraRZ97').mkdir(parents=True)
        artist = inputs / 'SupraRZ97/1997_toyota_supra_rz.glb'
        shutil.copyfile(source, artist)
        raw = artist.read_bytes()
        if hash_file(artist) != ARTIST_MODEL:
            raise GarageError('The source model changed while it was being copied.')
        length = struct.unpack_from('<I', raw, 12)[0]
        (inputs / 'gltf_metadata.json').write_text(json.dumps(json.loads(raw[20:20 + length])), encoding='utf-8')
        (work / 'reports').mkdir()
        (work / 'authoring').mkdir()

        def process(command, label):
            progress(label)
            log = work / 'stage.log'
            with log.open('wb') as stream:
                result = subprocess.run(command, cwd=work, stdout=stream, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                detail = log.read_bytes()[-1300:].decode('utf-8', errors='replace')
                raise GarageError(f'{label} failed. {detail}')

        process([str(blender), '--background', '--factory-startup', '--disable-autoexec', '--threads', '4',
                 '--python-exit-code', '1', '--python', str(RECIPE / 'prepare_neutral.py')],
                'Preparing the two levels of detail in Blender')
        candidates = work / 'candidates'
        for index, stage in enumerate(STAGES):
            if stage == 'build_four_rims_v027':
                wheel_base = candidates / 'supra_race_wheel_probe_v018'
                shutil.copytree(candidates / 'supra_functional_lights_v017', wheel_base)
                shutil.copyfile(donor / 'race/wheel', wheel_base / MODEL / 'race/wheel')
            args = ['--name', 'supra_panel_batches_v014'] if index == 0 else []
            process([*stage_command(stage), *args], f'Building template stage {index + 1} of {len(STAGES)}')
        result = candidates / 'supra_unique_fvf_v029' / MODEL
        validate_template(result)
        shutil.copyfile(TEMPLATE_CREDITS, result / 'MODEL_CREDITS.txt')
        try:
            result.rename(output)
        except OSError:
            if not output.is_dir():
                raise
            validate_template(output)
    progress('All eight template files verified.')
    return output


def cli(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-model', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--stock-car', type=Path)
    mode.add_argument('--disc', type=Path)
    mode.add_argument('--ftp-host')
    parser.add_argument('--ftp-port', type=int, default=21)
    parser.add_argument('--ftp-user', default='anonymous', help='A named user prompts for its password.')
    parser.add_argument('--update', type=Path)
    parser.add_argument('--packer', type=Path)
    parser.add_argument('--blender', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args(argv)
    read = None
    if a.ftp_host:
        from getpass import getpass
        password = 'anonymous@' if a.ftp_user == 'anonymous' else getpass('PS3 FTP password: ')
        read = ftp_reader(a.ftp_host, a.ftp_port, a.ftp_user, password)
    build_template(a.source_model, a.output, stock=a.stock_car, update=a.update, disc=a.disc,
                   read_ftp=read, packer=a.packer and a.packer.resolve(), blender=a.blender)


def dispatch():
    args = sys.argv[1:]
    if args[:1] == ['--profile-stage'] and len(args) >= 2:
        run_stage(args[1], args[2:])
    elif args[:1] == ['--prepare-template']:
        cli(args[1:])
    elif args[:1] == ['--template-wizard']:
        from .template_wizard import wizard
        parser = argparse.ArgumentParser()
        parser.add_argument('--workspace', type=Path, required=True)
        wizard(parser.parse_args(args[1:]).workspace)
    else:
        return
    raise SystemExit(0)

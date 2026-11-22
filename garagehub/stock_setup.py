"""Prepare a clean, local authoring workspace from the player's own GT6 files."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .car_geometry import check_cancel
from .car_pack import SEED, STOCK_CATALOGUE, baseline, execute, listing, trusted_packer
from .car_profiles import ASSETS, PROFILE, validate_template
from .errors import GarageError
from .util import atomic_json, hash_file, safe_rel

RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'resources'
TEMPLATE_CREDITS = RESOURCES / 'template-credits.txt'
# These are the three packed catalogue files named by the stock 1.22 index.
# Check the FTP copies before giving them to the native archive reader.
PACKED_CATALOGUE = {
    '9/QE/0B': 'bc0fdeb01d5c935c485962cee633659273365cc85414b8451dfc805c16c09abd',
    '9/R2/Q0': '85199e677b3e8df5779acaaa34e0dcc213b81c95c413ec7af6e96c200881c198',
    '9/EJ/EX': 'e677116385aa7c9706ef26d387d6ea65bcab2fd025aa46b82885c8f327ee0487',
}


def find_update(selected):
    root = Path(selected)
    candidates = (root, root/'PDIPFS', root/'USRDIR/PDIPFS', root/'BCES01893/USRDIR/PDIPFS',
                  root/'game/BCES01893/USRDIR/PDIPFS', root/'dev_hdd0/game/BCES01893/USRDIR/PDIPFS',
                  root/'portable/dev_hdd0/game/BCES01893/USRDIR/PDIPFS')
    for path in candidates:
        if path.name.casefold() == 'pdipfs' and (path/'K/4D').is_file():
            return path.resolve()
    raise GarageError('Choose your RPCS3 folder or the BCES01893 update folder containing USRDIR/PDIPFS/K/4D.', 'stock_setup')


def _unpack_owned(packer,location,indices,extracted,work,label,workspace,cancel):
    log=work/(label+'.log')
    for attempt in range(6):
        try:
            execute(packer,['unpack','-i',location,'-o',extracted,'--indices',*indices],work,log.name,cancel)
            return
        except GarageError as exc:
            if exc.code!='packer_failed' or not log.is_file():raise
            with log.open('rb') as f:
                f.seek(max(0,log.stat().st_size-16384))
                detail=f.read().decode('utf-8',errors='replace').casefold()
            locked='being used by another process' in detail or 'sharing violation' in detail
            if locked and attempt<5:
                check_cancel(cancel)
                time.sleep(.25*(attempt+1))
                continue
            shutil.copyfile(log,workspace/'stock-setup.log')
            if locked:
                raise GarageError('Your GT6 '+label+' archive is in use. Close GT6 or the other reader, then run setup again.', 'stock_setup') from exc
            if 'unauthorizedaccessexception' in detail or 'access to the path' in detail:
                raise GarageError('The packer needs access to open your '+label+' backup. Choose a local copy you can read and write, then run setup again.', 'stock_setup') from exc
            raise GarageError('The stock '+label+' could not be extracted. Check the game copy. Details are in stock-setup.log in the Garage workspace.', 'stock_setup') from exc


def prepare_stock(workspace, selected, template, packer, cancel=None, progress=None):
    update = find_update(selected)
    required_files = {**SEED, **PACKED_CATALOGUE}
    for rel, digest in required_files.items():
        check_cancel(cancel)
        if not (update / rel).is_file():
            raise GarageError(f'Copy PDIPFS/{rel} from your stock BCES01893 1.22 update and select the folder again.', 'stock_setup')
        if hash_file(update / rel) != digest:
            raise GarageError(f'PDIPFS/{rel} differs from stock BCES01893 1.22. Recopy that file from the unmodified update.', 'stock_setup')
    trusted_packer(packer)
    workspace = Path(workspace)
    workspace.mkdir(parents=True,exist_ok=True)
    destination = workspace/'stock-BCES01893-122'

    def existing():
        result = baseline(destination)
        validate_template(destination)
        return result

    if destination.is_dir():
        return existing()
    if not template:
        raise GarageError('Choose Find car template, or use Prepare car template to make one from your own files.', 'car_template')
    template = Path(validate_template(Path(template))['root'])
    notify = progress or (lambda message:None)
    # Only temporary output is removed on failure. Discs and game directories are inputs.
    with tempfile.TemporaryDirectory(prefix='stock-setup-',dir=workspace) as temp_name:
        temp = Path(temp_name)
        package = temp/'package'
        work = temp/'work'
        extracted = temp/'owned'
        package.mkdir()
        rows=[]
        for rel,digest in SEED.items():
            for folder in ('BaselineSeed','PatchOut'):
                output=package/folder/rel
                output.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(update/rel,output)
                if hash_file(output)!=digest:
                    raise GarageError('The game index changed during setup. Select a stable copy.', 'stock_setup')
            rows.append({'relative_path':rel,'bytes':(package/'PatchOut'/rel).stat().st_size,'sha256':digest})
        notify('Reading the GT6 1.22 archive index')
        entries_path=package/'packed_entries.txt'
        execute(packer,['listfiles','-i',package/'BaselineSeed','-o',entries_path],work,'index.log',cancel)
        entries=listing(entries_path)
        model=PROFILE['model_path']
        needed = list(STOCK_CATALOGUE)
        if any(name not in entries for name in needed):
            raise GarageError('The selected game index does not contain the supported car and name tables.', 'stock_setup')
        # Give the native packer its own writable copy, including when the update
        # was copied from a read-only volume. Read no unrelated update files.
        staged_update = temp / 'PDIPFS'
        shutil.copytree(package/'BaselineSeed', staged_update)
        updated = []
        for name in needed:
            row = entries[name]
            rel = safe_rel(row['packed'])
            if (update / rel).is_file():
                check_cancel(cancel)
                target = staged_update / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(update / rel, target)
                updated.append(str(row['index']))
            else:
                raise GarageError(f'Copy PDIPFS/{rel} from your stock BCES01893 1.22 update and select the folder again.', 'stock_setup')
        notify('Reading the car database and names from your GT6 update')
        _unpack_owned(packer,staged_update,updated,extracted,work,'update',workspace,cancel)
        for name,expected in STOCK_CATALOGUE.items():
            source=extracted/name
            if not source.is_file() or hash_file(source)!=expected:
                raise GarageError('The extracted GT6 car catalogue differs from BCES01893 1.22. Check the selected update.', 'stock_setup')
            target=package/'Input'/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
        notify('Preparing the car template for your workshop')
        authoring = package / 'Authoring' / model
        for name in ASSETS:
            check_cancel(cancel)
            target = authoring / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template / name, target)
        receipt = {'passed':True,'candidate_id':'GT6 1.22 stock','files':rows,
                    'header_sha256':SEED['K/4D'],'source_virtual_assets':STOCK_CATALOGUE,'source_roundtrip_match':True,
                    'allowed_base_headers':[SEED['K/4D']]}
        shutil.copyfile(TEMPLATE_CREDITS, package / 'MODEL_CREDITS.txt')
        receipt['model_credits_sha256'] = hash_file(package / 'MODEL_CREDITS.txt')
        atomic_json(package/'package_receipt.json',receipt)
        baseline(package)
        validate_template(package)
        check_cancel(cancel)
        try:
            os.rename(package,destination)
        except OSError:
            if not destination.is_dir():
                raise
        return existing()

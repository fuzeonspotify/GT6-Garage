"""Hash-verified package export and stopped-session local install/rollback.
No automatic game launch, no save access, no merge of incompatible headers.
"""
from __future__ import annotations
import json, os, re, shutil, struct, time, zipfile
from pathlib import Path
from .errors import GarageError, Cancelled
from .util import safe_rel, inside, hash_file, read_json, atomic_json, unique_path
from .native import MDL3

TITLE_RE=re.compile(r'^[A-Z]{4}[0-9]{5}$')
PFS_RE=re.compile(r'^[0-9A-Z]{1,4}/[0-9A-Z]{1,4}(?:/[0-9A-Z]{1,4})?$')

def read_sfo(data:bytes):
    if len(data)<20 or data[:4]!=b'\x00PSF':raise GarageError('Not a valid PARAM.SFO.', 'sfo')
    _,version,kp,dp,n=struct.unpack_from('<4sIIII',data)
    if n>256 or 20+n*16>len(data):raise GarageError('Invalid PARAM.SFO table.', 'sfo')
    out={}
    for i in range(n):
        ko,fmt,size,cap,do=struct.unpack_from('<HHIII',data,20+i*16)
        if kp+ko>=len(data) or dp+do+size>len(data) or size>cap:raise GarageError('Invalid PARAM.SFO offset.', 'sfo')
        end=data.find(b'\0',kp+ko)
        if end<0:raise GarageError('Invalid PARAM.SFO name.')
        key=data[kp+ko:end].decode('ascii');b=data[dp+do:dp+do+size]
        out[key]=b.rstrip(b'\0').decode('utf-8','replace') if fmt==0x0204 else b.hex()
    return out

def payload_order(files):return sorted(files,key=lambda r:(r['path']=='K/4D',r['path']))

def source_package(folder:Path, title='BCES01893', update='01.22', *, allow_diagnostic=False):
    folder=Path(folder).resolve()
    receipt=read_json(folder/'package_receipt.json')
    credits_hash=receipt.get('model_credits_sha256')
    if credits_hash is not None:
        credits=folder/'MODEL_CREDITS.txt'
        if (not credits.is_file() or credits.is_symlink() or credits.stat().st_size>256*1024
                or hash_file(credits)!=credits_hash):
            raise GarageError('The model credits differ from this package. Extract the complete starting build again.')
    if (receipt.get('developer_only') is True or receipt.get('player_installable') is False) and allow_diagnostic is not True:
        raise GarageError('This is a controlled diagnostic package, not a player car. Use the private experiment workflow.', 'diagnostic_package')
    if not receipt.get('passed') or not receipt.get('source_roundtrip_match'):raise GarageError('Package has not passed extraction/roundtrip validation.', 'package_unverified')
    root=folder/'PatchOut'
    if not root.is_dir() or root.is_symlink():raise GarageError('Choose the package folder containing PatchOut and package_receipt.json.')
    files=[];total=0;seen=set()
    for f in receipt.get('files',[]):
        rel=safe_rel(f['relative_path'])
        if rel in seen or not PFS_RE.fullmatch(rel):raise GarageError('Duplicate or invalid native payload path.')
        seen.add(rel);p=inside(root,rel)
        if p.is_symlink() or any(q.is_symlink() for q in p.parents if q!=root.parent):raise GarageError('Package links are not permitted.')
        if not p.is_file() or p.stat().st_size!=f['bytes'] or hash_file(p)!=f['sha256']:raise GarageError('Package file changed or is missing: '+rel,'package_hash')
        total+=f['bytes'];files.append(dict(path=rel,size=f['bytes'],sha256=f['sha256']))
        if total>1024**3 or len(files)>10000:raise GarageError('Package exceeds the export budget.')
    if 'K/4D' not in seen:raise GarageError('Package index K/4D is missing.')
    head=next(f for f in files if f['path']=='K/4D')
    if head['sha256']!=receipt.get('header_sha256'):raise GarageError('Package header identity does not match its receipt.')
    # Verify retained native source when available, including the map-load regression.
    native_checked=[]
    for rel,digest in receipt.get('source_virtual_assets',{}).items():
        rel=safe_rel(rel);p=folder/'Input'/rel
        if p.is_file():
            if hash_file(p)!=digest:raise GarageError('Native source does not match its packed receipt: '+rel)
            with p.open('rb') as f:magic=f.read(4)
            if magic==b'MDL3':
                if p.stat().st_size>64*1024**2:raise GarageError('Native asset exceeds the validation budget.')
                MDL3(p.read_bytes()).validate();native_checked.append(rel)
    if not TITLE_RE.fullmatch(title):raise GarageError('Invalid GT6 title ID.')
    manifest=dict(schema=1,kind='gt6-native-package',name=receipt['candidate_id'],title_id=title,game_update=update,files=payload_order(files),total_bytes=total,header_sha256=head['sha256'],native_assets_checked=native_checked,source_roundtrip_match=True,runtime_tested=bool(receipt.get('runtime_tested',False)),physical_ps3_tested=False,allowed_base_headers=receipt.get('allowed_base_headers',[]),notes=['One coherent archive index. Do not combine headers from different mods.','Static checks do not certify gameplay or physical-console compatibility.'])
    return root,manifest


def export_package(folder:Path,output:Path,cancel=None):
    root,manifest=source_package(folder)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise GarageError('The output ZIP already exists. Choose a new name.')
    temp=output.with_name(output.name+'.partial')
    if temp.exists():raise GarageError('A previous partial export exists; choose a new name.')
    instructions=f'''{manifest['name']} — GT6 1.22 / {manifest['title_id']}

Close GT6 and back up your save and previous COMPLETE mod package.
FTP to /dev_hdd0/game/{manifest['title_id']}/USRDIR/PDIPFS/
1. Upload every file in Install except K/4D, merging folders.
2. Wait for all data transfers to finish.
3. Upload Install/K/4D LAST.
4. Start GT6. To roll back, restore your previous COMPLETE package.

This ZIP contains native game data from your selected local package.
Do not install on another update/region without a separately validated build.
Other mods need a combined repack; do not mix archive headers.
Physical PS3 testing: not performed by this tool.
No emulator, settings, firmware, account files or save data are included.
'''
    try:
        with zipfile.ZipFile(temp,'x',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
            z.writestr('README_INSTALL.txt',instructions);z.writestr('manifest.json',json.dumps(manifest,indent=2))
            if read_json(Path(folder)/'package_receipt.json').get('model_credits_sha256'):
                z.write(Path(folder)/'MODEL_CREDITS.txt','MODEL_CREDITS.txt')
            for row in payload_order(manifest['files']):
                if cancel and cancel.is_set():raise Cancelled()
                z.write(inside(root,row['path']),'Install/'+row['path'])
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None:raise GarageError('ZIP CRC verification failed.')
            for row in manifest['files']:
                import hashlib
                h=hashlib.sha256()
                with z.open('Install/'+row['path']) as f:
                    for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
                if h.hexdigest()!=row['sha256']:raise GarageError('Exported file does not match its source.')
        os.replace(temp,output)
    finally:
        if temp.exists():temp.unlink()
    return dict(path=str(output),bytes=output.stat().st_size,sha256=hash_file(output),manifest=manifest)


def install_files(root:Path,manifest:dict,target:Path,backup:Path,*,expected_header:str,cancel=None,after_write=None):
    """Transactional PDIPFS install. Caller must verify no game is using target.
    The expected current header is mandatory: rejects stale/conflicting installs.
    Rollback includes overwritten/new files; the activation header is always last.
    """
    root=Path(root).resolve();target=Path(target).resolve();backup=Path(backup)
    if not target.is_dir() or target.name!='PDIPFS':raise GarageError('Select the exact PDIPFS folder.')
    if backup.exists():raise GarageError('Use a new backup folder for this transaction.')
    if backup.resolve()==target or target in backup.resolve().parents:raise GarageError('Backup must be outside the game-data folder.')
    if not re.fullmatch('[0-9a-f]{64}',expected_header):raise GarageError('A checked current header hash is required.')
    if hash_file(target/'K/4D')!=expected_header:raise GarageError('Installed mods changed since inspection. Inspect the target again.', 'conflict')
    sfo_path=target.parent.parent/'PARAM.SFO'
    meta=read_sfo(sfo_path.read_bytes())
    if meta.get('TITLE_ID')!=manifest['title_id'] or meta.get('APP_VER')!=manifest['game_update']:raise GarageError('Game region/update does not match the package.', 'compatibility')
    if manifest.get('allowed_base_headers') and expected_header not in manifest['allowed_base_headers']:raise GarageError('This package does not support the installed archive header.', 'compatibility')
    files=payload_order(manifest['files']);seen=set()
    for row in files:
        rel=safe_rel(row['path']);p=inside(root,rel)
        if rel in seen or not PFS_RE.fullmatch(rel):raise GarageError('Invalid package payload list.')
        seen.add(rel)
        if not p.is_file() or hash_file(p)!=row['sha256'] or p.stat().st_size!=row['size']:raise GarageError('Source changed before installation.')
        q=inside(target,rel)
        if q.exists() and q.stat().st_nlink!=1:raise GarageError('Target is shared/hard-linked. Install into an independent copy.')
        for parent in (q,*q.parents):
            if parent==target.parent:break
            if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():raise GarageError('Linked targets are not safe to modify.')
    if 'K/4D' not in seen:raise GarageError('Missing activation header.')
    required=sum(r['size'] for r in files)*2
    if shutil.disk_usage(target).free<required:raise GarageError('Not enough free space for staging and rollback.')
    backup.mkdir(parents=True,exist_ok=False);journal={'schema':1,'state':'preparing','target':str(target),'expected_header':expected_header,'entries':[]}
    staged=[]
    try:
        for row in files:
            if cancel and cancel.is_set():raise Cancelled()
            rel=row['path'];dst=inside(target,rel);entry={'path':rel,'existed':dst.exists(),'sha256':hash_file(dst) if dst.exists() else None}
            if dst.exists():
                b=inside(backup/'before',rel);b.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dst,b)
                if hash_file(b)!=entry['sha256']:raise GarageError('Backup verification failed.')
            journal['entries'].append(entry)
        journal['state']='backed_up';atomic_json(backup/'journal.json',journal)
        for row in files:
            if cancel and cancel.is_set():raise Cancelled()
            dst=inside(target,row['path']);dst.parent.mkdir(parents=True,exist_ok=True);tmp=dst.with_name(dst.name+'.garage-stage')
            if tmp.exists():raise GarageError('A previous staging file exists; recover before installing.')
            staged.append(tmp);shutil.copyfile(inside(root,row['path']),tmp)
            if hash_file(tmp)!=row['sha256']:raise GarageError('Staging verification failed.')
            os.replace(tmp,dst)
            if after_write:after_write(row['path'])
        for row in files:
            if hash_file(inside(target,row['path']))!=row['sha256']:raise GarageError('Installed file verification failed.')
        journal['state']='installed';journal['new_header']=manifest['header_sha256'];atomic_json(backup/'journal.json',journal)
    except BaseException:
        # Restore complete payload set before reactivating the old index.
        for e in sorted(journal['entries'],key=lambda e:(e['path']=='K/4D',e['path'])):
            dst=inside(target,e['path']);b=inside(backup/'before',e['path'])
            if e['existed']:
                if b.is_file() and hash_file(b)==e['sha256']:shutil.copyfile(b,dst)
                else:journal['state']='recovery_required';atomic_json(backup/'journal.json',journal);raise GarageError('Rollback backup missing; do not launch GT6.', 'recovery_required')
            elif dst.exists():dst.unlink()
        journal['state']='rolled_back';atomic_json(backup/'journal.json',journal)
        raise
    finally:
        for p in staged:
            if p.exists():p.unlink()
    return {'installed':True,'backup':str(backup),'header_sha256':manifest['header_sha256'],'game_launched':False}

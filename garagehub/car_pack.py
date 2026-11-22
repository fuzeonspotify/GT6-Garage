"""Coherent append-only PDIPFS build from an owned baseline, never in-place.

A loose native model is not an installable patch. This module copies the full
mod input set, repacks a fresh index, independently extracts every result, and
rejects unrelated archive changes. No FTP, emulator or installed title writes.
"""
from __future__ import annotations
from pathlib import Path
import subprocess,shutil,time,re,json,zipfile,os
from .errors import GarageError,Cancelled
from .util import hash_file,read_json,atomic_json,safe_rel
from .native import MDL3
from .car_profiles import PROFILE,PROFILE_ID,ASSETS
from .car_geometry import check_cancel
from .packages import source_package,export_package

from .packer_build_id import PACKER_SHA
BASE_HEADER='d9c6869e0d6d49c812c85bf55c653de89e4e992e8735b1fead94e4872c8d3418'
SEED={'K/4D':'e6d9903aa066e99ecfbc429d48f3a6c793dfe813e34a9d3eda2caee0af34674a','9/H4/MS':'2a19696eee18deca0cef790cab78306b3f45589305b1b23f8ad6b9d735a6c927'}
STOCK_CATALOGUE={'specdb/GT6/DB0106.dat':'92ef76c75416f8031b3a8983cf166aa7af3eb4279fcb5161927fe1adc9d47bae',
                 'specdb/GT6/JP/CarName.dat':'3889fccaa847a079776035b213998c596987722359b19267f64c5f270500e182',
                 'specdb/GT6/US/CarName.dat':'53a6a3373b00ae4750676785544d5773f6db13cdfdf28aa0b6b1f51a2b4d50f8'}


def trusted_packer(path:Path):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or path.suffix.lower()!='.exe' or path.stat().st_size>40*1024**2 or hash_file(path)!=PACKER_SHA:raise GarageError('Select the reviewed append-only GTToolsSharp executable. An unknown program will not be executed.','packer_identity')
    return {'path':str(path),'sha256':PACKER_SHA,'origin':'Nenkai GTToolsSharp, append-only build 8ad944','distributed_by_hub':path.parent.name=='GTToolsSharp' and path.parent.parent.name=='Support'}


def baseline(path:Path):
    path=Path(path)
    if not (path/'package_receipt.json').is_file():
        raise GarageError('Choose the v029 authoring package or a combined build from this importer. Open the folder containing package_receipt.json, Input, PatchOut and BaselineSeed. A disc, RPCS3 game directory or exported Install folder lacks the editable template.', 'baseline_identity')
    _,manifest=source_package(path)
    receipt=read_json(path/'package_receipt.json');expected=receipt['source_virtual_assets'];lineage=receipt.get('lineage',{})
    stock=manifest['header_sha256']==SEED['K/4D']
    if stock and (expected!=STOCK_CATALOGUE or {f['path']:f['sha256'] for f in manifest['files']}!=SEED):
        raise GarageError('The stock starter needs its verified BCES01893 1.22 catalogue and index.', 'baseline_identity')
    descendant=not stock and manifest['header_sha256']!=BASE_HEADER
    if descendant:
        if (receipt.get('profile_id')!=PROFILE_ID or not receipt.get('all_unrelated_archive_entries_preserved') or not receipt.get('existing_car_assets_preserved') or not isinstance(lineage,dict) or lineage.get('root_header') not in (BASE_HEADER,SEED['K/4D']) or type(lineage.get('generation')) is not int or lineage['generation']<1 or not re.fullmatch(r'[0-9a-f]{64}',str(lineage.get('parent_header','')))):
            raise GarageError('Choose the approved v029 baseline or a verified combined package produced by this importer. Unrelated mod stacks need a reviewed profile.','baseline_identity')
        from .car_profiles import validate_template
        validate_template(path)
    for rel,digest in expected.items():
        rel=safe_rel(rel);file=path/'Input'/rel
        if not file.is_file() or file.is_symlink() or hash_file(file)!=digest:raise GarageError('Baseline input differs from its receipt: '+rel)
    for rel,digest in SEED.items():
        file=path/'BaselineSeed'/rel
        if not file.is_file() or hash_file(file)!=digest:raise GarageError('The baseline seed does not match the reviewed game-update index.')
    root_header=lineage.get('root_header') if descendant else manifest['header_sha256']
    return {'folder':str(path),'header_sha256':manifest['header_sha256'],'file_count':len(manifest['files']),'source_count':len(expected),'region':'BCES01893','update':'01.22','preserved_cars':[] if root_header==SEED['K/4D'] else ['F50 2145','Cinque 2144'],'additional_entry':False,'generation':lineage.get('generation',0) if descendant else 0}


def listing(path:Path):
    out={}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if ' - Flags: ' not in line:continue
        name,rest=line.split(' - Flags: ',1);safe_rel(name);match=re.search(r'FileIndex: (\d+) \(([^)]+)\)',rest)
        if not match or name in out:raise GarageError('Packer produced an invalid file listing.')
        out[name]={'line':line,'index':int(match[1]),'packed':match[2]}
    if not out:raise GarageError('Packer produced an empty index.')
    return out


def execute(packer,args,work:Path,logname,cancel):
    check_cancel(cancel);work.mkdir(parents=True,exist_ok=True);log=work/logname
    # No shell. Only a hash-pinned local tool and explicit argument array.
    with log.open('wb') as file:
        process=subprocess.Popen([str(packer),*map(str,args)],cwd=work,stdout=file,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));start=time.monotonic()
        try:
            while process.poll() is None:
                if cancel and cancel.is_set():process.terminate();process.wait(timeout=10);raise Cancelled()
                if time.monotonic()-start>180:process.terminate();process.wait(timeout=10);raise GarageError('Native packer exceeded its bounded step timeout; the game is unchanged.')
                time.sleep(.05)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    if process.returncode:raise GarageError('Native packer stopped safely. Read the local build log: '+logname,'packer_failed')


def pack_archive(packer,seed,inputs,output,work,cancel):
    # Windows can briefly hold the newly written directory during the packer's
    # final rename. The index is produced afterwards, so retry the complete pack.
    if output.exists() or output.with_name(output.name+'_temp').exists():
        raise GarageError('Choose a fresh output folder for the car package.')
    for attempt in range(3):
        log='pack.log' if attempt==0 else f'pack_retry_{attempt}.log'
        try:
            execute(packer,['pack','-i',seed,'-p',inputs,'-o',output],work,log,cancel)
            return
        except GarageError as error:
            if error.code!='packer_failed' or not (work/log).is_file():
                raise
            with (work/log).open('rb') as stream:
                size=stream.seek(0,2)
                stream.seek(max(0,size-8192))
                detail=stream.read().decode('utf8','replace').casefold()
            transient=('system.io.filesystem.movedirectory' in detail
                       and (str(output)+'_temp').casefold() in detail
                       and ('denied' in detail or 'being used by another process' in detail))
            if not transient:
                raise
            if attempt==2:
                raise GarageError('Windows kept the build folder in use. Close any open view of that folder and build again. Your starting cars are safe.','packer_directory_busy') from error
            if cancel is None:
                time.sleep(attempt+1)
            elif cancel.wait(attempt+1):
                check_cancel(cancel)


def pack_candidate(candidate:Path,base:Path,packer:Path,out:Path,cancel=None,progress=None):
    trusted_packer(packer);baseline(base);candidate=Path(candidate);out=Path(out)
    build=read_json(candidate/'build_receipt.json')
    if not build.get('passed') or build.get('profile_id')!=PROFILE_ID or build.get('kind') not in ('profile-native-exterior-candidate','identity-native-exterior-candidate'):raise GarageError('Choose a complete native candidate from the reviewed profile.')
    routed=build['kind']=='identity-native-exterior-candidate'
    identity=build.get('installation',{})
    if routed:
        from .identity_pipeline import DATABASE_PATH
        if not identity.get('ready') or identity.get('mode') not in ('new_entry','replace') or not build.get('database_crypto_roundtrip'):raise GarageError('The candidate car identity has not passed its database checks.')
        model_path=safe_rel(build['model_path'])
        if not re.fullmatch(r'car/\d{4}/\d{4}',model_path) or identity.get('model_path')!=model_path:raise GarageError('The candidate model namespace does not match its identity plan.')
        original_receipt=read_json(base/'package_receipt.json')
        if identity.get('encrypted_source_sha256')!=original_receipt['source_virtual_assets'].get(DATABASE_PATH):raise GarageError('The database plan is stale for this baseline.')
        allowed={model_path+'/'+rel for rel in ASSETS}|{DATABASE_PATH}
        if identity['mode']=='new_entry':
            names=set(identity.get('name_files',{}))
            if not names or any(not re.fullmatch(r'specdb/GT6/[^/]+/CarName\.dat',p) for p in names):raise GarageError('New entries require all reviewed name maps.')
            allowed.update(names)
    else:model_path=PROFILE['model_path'];allowed={model_path+'/'+rel for rel in ASSETS}
    expected_new=build['source_virtual_assets']
    if set(expected_new)!=allowed:raise GarageError('Candidate asset set is incomplete or contains unexpected writes.')
    for rel,digest in expected_new.items():
        file=candidate/safe_rel(rel)
        if hash_file(file)!=digest:raise GarageError('Candidate changed after validation: '+rel)
        if file.name in ('body','wheel','interior'):MDL3(file.read_bytes()).validate()
    if out.exists():raise GarageError('Package output already exists.')
    if out.resolve()==base.resolve() or base.resolve() in out.resolve().parents or candidate.resolve() in out.resolve().parents:raise GarageError('Build output must be separate from source/template folders.')
    if shutil.disk_usage(out.parent).free<600*1024**2:raise GarageError('Keep at least 600 MiB free for verified native packaging.')
    out.mkdir();notify=lambda text:progress(text) if progress else None
    original=read_json(base/'package_receipt.json');original_sources=original['source_virtual_assets'];input_dir=out/'Input';input_dir.mkdir();seed=out/'BaselineSeed';seed.mkdir()
    try:
        notify('Copying the coherent baseline without changing the game')
        for rel,digest in original_sources.items():
            check_cancel(cancel);rel=safe_rel(rel);target=input_dir/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(base/'Input'/rel,target)
            if hash_file(target)!=digest:raise GarageError('Baseline copy verification failed.')
        for rel,digest in SEED.items():
            target=seed/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(base/'BaselineSeed'/rel,target)
            if hash_file(target)!=digest:raise GarageError('Seed copy failed verification.')
        for rel,digest in expected_new.items():
            target=input_dir/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(candidate/rel,target)
        expected={p.relative_to(input_dir).as_posix():hash_file(p) for p in input_dir.rglob('*') if p.is_file()}
        if any(expected.get(k)!=v for k,v in expected_new.items()):raise GarageError('A staged native asset differs from the validated candidate.')
        need_unchanged={k:v for k,v in original_sources.items() if k not in expected_new}
        if any(expected[k]!=v for k,v in need_unchanged.items()):raise GarageError('An unrelated car/database resource was modified.')
        work=out/'Work';patch=out/'PatchOut';before=listing(base/'packed_entries.txt')
        notify('Packing the native car and existing mod stack into a fresh index');pack_archive(packer,seed,input_dir,patch,work,cancel)
        execute(packer,['listfiles','-i',patch,'-o',out/'packed_entries.txt'],work,'listing.log',cancel);after=listing(out/'packed_entries.txt')
        if routed and any(name.startswith(model_path+'/') for name in before):raise GarageError('The new model namespace collides with an existing packed asset.')
        if set(after)!=set(before)|set(expected_new):raise GarageError('The build changed unexpected game namespace entries.')
        for name,record in before.items():
            if name not in expected and after[name]['line']!=record['line']:raise GarageError('An unrelated archive entry changed: '+name)
        verified=out/'Verified'
        for i,(name,digest) in enumerate(expected.items()):
            check_cancel(cancel);notify(f'Independent extraction check {i+1} / {len(expected)}');execute(packer,['unpack','-i',patch,'-o',verified,'--indices',after[name]['index']],work,f'extract_{after[name]["index"]}.log',cancel)
            file=verified/name
            if not file.is_file() or hash_file(file)!=digest:raise GarageError('Packed extraction differs from source: '+name)
        # The original source files and approved package must still be unchanged.
        baseline(base)
        for rel,digest in expected_new.items():
            if hash_file(candidate/rel)!=digest:raise GarageError('Candidate changed during packaging.')
        files=[{'relative_path':p.relative_to(patch).as_posix(),'bytes':p.stat().st_size,'sha256':hash_file(p)} for p in sorted(patch.rglob('*')) if p.is_file()]
        receipt={'passed':True,'candidate_id':out.name,'profile_id':PROFILE_ID,'title_id':'BCES01893','game_update':'01.22','files':files,'header_sha256':hash_file(patch/'K/4D'),'source_virtual_assets':expected,'source_roundtrip_match':True,'all_unrelated_archive_entries_preserved':True,'f50_cinque_and_database_byte_identical':not routed,'existing_car_assets_preserved':routed,'supra_physics_unchanged':True,'runtime_tested':False,'physical_ps3_tested':False,'source_license':build.get('source_license',{}),'build_receipt':str(candidate/'build_receipt.json'),'changed_virtual_assets':sorted(k for k in expected_new if expected_new[k]!=original_sources.get(k)),'additional_car_database_entry':bool(build.get('additional_database_entry')),'total_payload_bytes':sum(x['bytes'] for x in files),'limitations':build['limitations'],'installation':identity if routed else None,'database_validation':build.get('database')}
        if build.get('nominal_tyres') is not None:
            from .identity_pipeline import DATABASE_KEY
            from .new_entry_tyre_plan import verify_new_entry_tyres
            if (not routed or identity['mode'] != 'new_entry' or build.get('developer_only') is not True
                    or build.get('player_installable') is not False):
                raise GarageError('A nominal tyre experiment requires a separate diagnostic new entry.')
            directory = candidate/'DatabaseWork/tyre_composition'
            plan = read_json(directory/'PLAN.json')
            recorded = build['nominal_tyres']
            if (plan['plan_sha256'] != recorded['plan_sha256'] or plan['selected_mappings'] != recorded['selected_mappings']
                    or plan['installation_plan']['plan_sha256'] != identity['plan_sha256']):
                raise GarageError('The nominal tyre plan changed before package verification.')
            decoded = work/'extracted-tyres.sqlite'
            shutil.copyfile(verified/DATABASE_PATH, decoded)
            execute(packer,['cryptsalsa','-i',decoded,'--key',DATABASE_KEY],work,'extracted-tyres.log',cancel)
            if hash_file(decoded) != recorded['output_sha256']:
                raise GarageError('The extracted database differs from the verified tyre candidate.')
            proof = verify_new_entry_tyres(Path(plan['source']['path']), directory/'clone.sqlite', decoded, plan)
            atomic_json(out/'extracted_tyre_verification.json', proof)
            receipt.update(developer_only=True, player_installable=False,
                           extracted_tyre_database_verified=True, nominal_tyres=recorded,
                           extracted_tyre_validation=proof['tyre_validation'])
        if routed:
            lineage=original.get('lineage',{})
            root_header=lineage.get('root_header',original['header_sha256'])
            receipt['lineage']={'schema':1,'root_header':root_header,'parent_header':original['header_sha256'],'generation':lineage.get('generation',0)+1}
            if root_header==SEED['K/4D']:
                from .car_profiles import validate_template
                template=Path(validate_template(base)['root'])
                for rel in ASSETS:
                    target=out/'Authoring'/PROFILE['model_path']/rel
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(template/rel,target)
                validate_template(out)
                receipt['allowed_base_headers']=list(dict.fromkeys([root_header,original['header_sha256'],receipt['header_sha256']]))
            elif original.get('allowed_base_headers'):
                receipt['allowed_base_headers']=list(dict.fromkeys([*original['allowed_base_headers'],original['header_sha256'],receipt['header_sha256']]))
        credits = base/'MODEL_CREDITS.txt'
        credit_digest = original.get('model_credits_sha256')
        if not credit_digest and original.get('lineage',{}).get('root_header',original['header_sha256']) == SEED['K/4D']:
            # Older stock workspaces predate bundled-template credits.
            from .stock_setup import TEMPLATE_CREDITS
            credits = TEMPLATE_CREDITS
            if credits.is_file():
                credit_digest = hash_file(credits)
        if credit_digest:
            shutil.copyfile(credits,out/'MODEL_CREDITS.txt')
            receipt['model_credits_sha256']=hash_file(out/'MODEL_CREDITS.txt')
            if receipt['model_credits_sha256']!=credit_digest:
                raise GarageError('The starting package model credits changed during the build.')
        atomic_json(out/'package_receipt.json',receipt);shutil.copyfile(candidate/'build_receipt.json',out/'model_build_receipt.json');notify('Complete package verified; creating the installation ZIP')
        return receipt
    except Exception:
        atomic_json(out/'FAILED.json',{'release_ready':False,'installed_game_unchanged':True});raise


def finish_zip(package:Path,path:Path,cancel=None):
    check_cancel(cancel);result=export_package(package,path,cancel);receipt=read_json(package/'package_receipt.json');build=read_json(package/'model_build_receipt.json')
    # Append evidence and source attribution; existing packager remains responsible
    # for payload/index ordering. No executable, key, save or console ID included.
    with zipfile.ZipFile(path,'a',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
        z.writestr('CAR_BUILD.json',json.dumps({'profile_id':build['profile_id'],'name':build['name'],'source_sha256':build['source_sha256'],'license':build.get('source_license',{}),'inherited':build['inherited'],'limitations':build['limitations'],'runtime_tested':False,'physical_ps3_tested':False},indent=2))
        z.writestr('ATTRIBUTION.txt','Source model attribution, retained as supplied:\n'+json.dumps(build.get('source_license',{}),indent=2)+'\n')
        identity=build.get('installation')
        destination=(('Adds a new separate entry' if identity['mode']=='new_entry' else 'Replaces the selected entry')+' for '+identity['display_name']+' (ID '+str(identity['car_id'])+').') if identity else 'Replaces Supra RZ 1997 visuals using the owned v029 profile.'
        z.writestr('TEST_CANDIDATE.txt','EXPERIMENTAL NATIVE CAR PACKAGE\n'+destination+'\nExisting unrelated cars and the selected native driving setup are preserved.\nThe database, native model and packaged output passed structural checks.\nPurchase, save, restart and in-game appearance still require isolated testing.\nNew dealership thumbnails/descriptions are not generated by this build.\nPhysical PS3 compatibility has not been tested.\n')
        if identity:z.writestr('CAR_DESTINATION.json',json.dumps(identity,indent=2))
    with zipfile.ZipFile(path) as z:
        if z.testzip():raise GarageError('Output ZIP integrity check failed.')
    result.update(bytes=path.stat().st_size,sha256=hash_file(path),profile_id=PROFILE_ID,complete_native_package=True,game_ready=False,runtime_verified=False,experimental=True)
    return result

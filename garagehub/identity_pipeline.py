"""Stage a selected car identity alongside a native model, never in a game folder.

The owned coherent baseline supplies the encrypted database and all name maps.
The existing reviewed native packer also provides its reversible crypt command.
"""
from __future__ import annotations
import json
import re
import shutil
import uuid
from pathlib import Path
from .errors import GarageError
from .util import atomic_json, hash_file, read_json, safe_rel
from .car_profiles import PROFILE, ASSETS
from .car_geometry import check_cancel
from .install_targets import installation, selection_actions

DATABASE_PATH = 'specdb/GT6/DB0106.dat'
# Documented GT6 SpecDB file-format constant: https://nenkai.github.io/gt-modding-hub/ps3/specdb/
DATABASE_KEY = '2D9EE83E63120EB25DF4981EE73C3BE194D0F059DE50C8D4FCB66C10D3EDC549'


def database_context(base: Path, packer: Path, cache: Path, cancel=None):
    from .car_pack import baseline, execute, listing, trusted_packer
    from .car_names import name_ids
    from .specdb import catalogue
    base=Path(base);cache=Path(cache)
    checked=baseline(base);trusted_packer(packer)
    source=base/'Input'/DATABASE_PATH
    if not source.is_file():
        raise GarageError('The selected baseline is missing its complete car database.')
    digest=hash_file(source);folder=cache/digest;folder.mkdir(parents=True,exist_ok=True)
    plain=folder/'base.sqlite';proof=folder/'decoded.json'
    if not (plain.is_file() and proof.is_file() and read_json(proof).get('encrypted_sha256')==digest and read_json(proof).get('plain_sha256')==hash_file(plain)):
        check_cancel(cancel);temp=folder/('decode_'+uuid.uuid4().hex+'.sqlite')
        shutil.copyfile(source,temp)
        try:
            execute(packer,['cryptsalsa','-i',temp,'--key',DATABASE_KEY],folder/'Work','decode.log',cancel)
            with temp.open('rb') as stream:
                if stream.read(16)!=b'SQLite format 3\0':raise GarageError('Database decode did not produce a supported SQLite database.')
            catalogue(temp,donor_car_id=PROFILE['car_id'])
            temp.replace(plain)
            atomic_json(proof,{'encrypted_sha256':digest,'plain_sha256':hash_file(plain)})
        finally:
            if temp.exists():temp.unlink()
    if hash_file(source)!=digest:raise GarageError('The baseline database changed during inspection. Check the plan again.')
    names={};reserved=set()
    for file in sorted((base/'Input/specdb/GT6').glob('*/CarName.dat')):
        if file.is_symlink():raise GarageError('Choose regular baseline name files.')
        rel=file.relative_to(base/'Input').as_posix();raw=file.read_bytes()
        reserved.update(name_ids(raw));names[rel]=hash_file(file)
    if not names:raise GarageError('The baseline needs its original long and short car-name maps before creating identities.')
    entries=listing(base/'packed_entries.txt')
    listed_names={p for p in entries if re.fullmatch(r'specdb/GT6/[^/]+/CarName\.dat',p)}
    missing=sorted(listed_names-set(names))
    if missing:raise GarageError('Extract all target-language name maps into the owned baseline first: '+', '.join(missing))
    return {'database':str(plain),'encrypted_sha256':digest,'names':names,
            'reserved_car_ids':sorted(reserved),'reserved_model_paths':sorted(entries),
            'baseline_header':checked['header_sha256']}


def target_catalogue(base,packer,cache,cancel=None):
    from .specdb import catalogue
    context=database_context(base,packer,cache,cancel)
    result=catalogue(Path(context['database']),donor_car_id=PROFILE['car_id'])
    result['donor_profile']=PROFILE['label']
    return result


def identity_plan(project,base,packer,cache,cancel=None):
    from .specdb import plan_install
    from .target_geometry import target_geometry_report
    choice=installation(project.get('installation'));actions=selection_actions(choice)
    if actions:return {'ready':False,'blockers':actions,'mode':choice['mode']}
    context=database_context(base,packer,cache,cancel)
    result=plan_install(Path(context['database']),mode=choice['mode'],
                        donor_car_id=PROFILE['car_id'],name=project['name'],
                        target_car_id=choice['replace_car_id'],
                        reserved_model_paths=context['reserved_model_paths'],
                        reserved_car_ids=context['reserved_car_ids'])
    result.update(native_profile_id=PROFILE['id'],name_files=context['names'],
                  encrypted_source_sha256=context['encrypted_sha256'],
                  baseline_header=context['baseline_header'])
    geometry_car_id=PROFILE['car_id'] if choice['mode']=='new_entry' else choice['replace_car_id']
    result['target_geometry']=target_geometry_report(Path(context['database']),geometry_car_id)
    result['target_geometry']['identity_scope']='inherited_donor' if choice['mode']=='new_entry' else 'explicit_replacement_target'
    return result


def route_candidate(candidate,base,packer,cache,plan,output,cancel=None,progress=None,*,donor_tyre_selections=None):
    from .car_names import append_car_names
    from .specdb import build_database_copy
    from .car_pack import execute
    candidate=Path(candidate);base=Path(base);output=Path(output)
    if not plan.get('ready') or plan.get('mode') not in ('new_entry','replace'):
        raise GarageError('Check an explicit, supported car destination before building.')
    if output.exists() or candidate.resolve() in output.resolve().parents or base.resolve() in output.resolve().parents:
        raise GarageError('Use a fresh identity candidate folder separate from the model and baseline.')
    model=read_json(candidate/'build_receipt.json')
    model_receipt_hash=hash_file(candidate/'build_receipt.json')
    if model.get('profile_id')!=PROFILE['id'] or not model.get('passed'):
        raise GarageError('A complete native model from the selected donor is required.')
    model_path=safe_rel(plan.get('model_path',''))
    if not re.fullmatch(r'car/\d{4}/\d{4}',model_path):raise GarageError('Invalid allocated model namespace.')
    context=database_context(base,packer,cache,cancel)
    if (context['encrypted_sha256']!=plan.get('encrypted_source_sha256') or context['names']!=plan.get('name_files')
            or context.get('baseline_header')!=plan.get('baseline_header')):
        raise GarageError('The target database or names changed after planning. Check the plan again.')
    if any(p.startswith(model_path+'/') for p in context['reserved_model_paths']):
        raise GarageError('The allocated model folder is already occupied. Check the plan again.')
    notify=progress or (lambda text:None);output.mkdir(parents=True)
    try:
        check_cancel(cancel);notify('Creating the selected car identity in a separate database copy')
        dbplain=output/'DatabaseWork/updated.sqlite';dbplain.parent.mkdir()
        composition = None
        if donor_tyre_selections is None:
            database_receipt=build_database_copy(Path(context['database']),dbplain,plan)
        else:
            from .new_entry_tyre_plan import plan_new_entry_tyres, build_new_entry_tyres
            from .specdb import plan_install
            source_db = Path(context['database'])
            install = plan_install(source_db, **{**plan['request'], 'reserved_car_ids':context['reserved_car_ids'],
                                                 'reserved_model_paths':context['reserved_model_paths']})
            if install['mode'] != 'new_entry' or any(plan.get(k) != v for k,v in install.items()):
                raise GarageError('Plan a fresh separate entry on the current baseline for this tyre experiment.')
            tyre_plan = plan_new_entry_tyres(source_db, installation_plan=install, selections=donor_tyre_selections)
            folder = dbplain.parent/'tyre_composition'
            composition = build_new_entry_tyres(source_db, folder, tyre_plan)
            dbplain = folder/'nominal_tyres.sqlite'
            database_receipt = read_json(folder/'CLONE_BUILD.json')
            database_receipt.update(database_sha256=composition['output']['sha256'],
                                    clone_sha256=composition['clone']['sha256'],
                                    physics='Selected nominal tyre records on the new entry')
        dbtarget=output/DATABASE_PATH;dbtarget.parent.mkdir(parents=True);shutil.copyfile(dbplain,dbtarget)
        execute(packer,['cryptsalsa','-i',dbtarget,'--key',DATABASE_KEY],output/'DatabaseWork','encode.log',cancel)
        with dbtarget.open('rb') as stream:
            if stream.read(16)==b'SQLite format 3\0':raise GarageError('Database encryption did not complete.')
        # Check the actual encrypted output, not merely the tool exit code.
        roundtrip=output/'DatabaseWork/roundtrip.sqlite';shutil.copyfile(dbtarget,roundtrip)
        execute(packer,['cryptsalsa','-i',roundtrip,'--key',DATABASE_KEY],output/'DatabaseWork','roundtrip.log',cancel)
        if hash_file(roundtrip)!=hash_file(dbplain):raise GarageError('The encrypted database failed its independent decode check.')
        names_receipts={}
        if plan['mode']=='new_entry':
            target_id=plan['car_id'];donor_id=plan['donor']['car_id']
            for rel,digest in context['names'].items():
                check_cancel(cancel);source=base/'Input'/rel
                if hash_file(source)!=digest:raise GarageError('A car-name map changed during the build.')
                raw,receipt=append_car_names(source.read_bytes(),target_id,plan['display_name'],plan['display_name'],after_id=donor_id)
                dest=output/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);names_receipts[rel]=receipt
        notify('Routing the converted model to its allocated car folder')
        expected={PROFILE['model_path']+'/'+rel for rel in ASSETS}
        if set(model['source_virtual_assets'])!=expected:raise GarageError('The native model asset set is incomplete.')
        for rel,digest in model['source_virtual_assets'].items():
            check_cancel(cancel)
            if hash_file(candidate/rel)!=digest:raise GarageError('Native geometry changed after conversion.')
            suffix=rel[len(PROFILE['model_path'])+1:];dest=output/model_path/suffix
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(candidate/rel,dest)
            if hash_file(dest)!=digest:raise GarageError('A routed native asset differs from the validated model.')
        if hash_file(candidate/'build_receipt.json')!=model_receipt_hash or any(hash_file(candidate/rel)!=digest for rel,digest in model['source_virtual_assets'].items()):
            raise GarageError('Native model evidence changed during identity routing.')
        paths=[model_path+'/'+rel for rel in ASSETS]+[DATABASE_PATH]+list(names_receipts)
        hashes={rel:hash_file(output/rel) for rel in paths}
        limitations=[x for x in model['limitations'] if 'does not add a database entry' not in x]
        limitations += ['New dealership thumbnails and descriptions are not generated in this development build.',
                         'Purchase, save, reload and visual compatibility require an isolated game test.']
        receipt={**model,'kind':'identity-native-exterior-candidate','model_path':model_path,
                 'car_id':plan['car_id'],'installation':plan,'database':database_receipt,
                 'name_maps':names_receipts,'source_virtual_assets':hashes,
                 'additional_database_entry':plan['mode']=='new_entry','limitations':limitations,
                 'database_crypto_roundtrip':True,'runtime_tested':False,'game_installation_changed':False}
        if composition is not None:
            receipt.update(developer_only=True, player_installable=False,
                           nominal_tyres={'plan_sha256':composition['plan_sha256'],
                               'output_sha256':composition['output']['sha256'],
                               'selected_mappings':composition['selected_mappings']})
        atomic_json(output/'build_receipt.json',receipt)
        return receipt
    except Exception:
        atomic_json(output/'FAILED.json',{'release_ready':False,'installed_game_unchanged':True})
        raise

"""Expert-only packaged application wheel lab. Never touches game installs."""
from pathlib import Path
from .errors import GarageError
from .f50_wheel_adapter import F50_SOURCE_SHA256
from .util import hash_file,atomic_json


def validate_lab_paths(source,donor,output):
    source,donor,output=map(Path,(source,donor,output))
    if not source.is_file() or source.is_symlink() or source.stat().st_size>64*1024**2:
        raise GarageError('Choose a regular local F50 source GLB under 64 MiB.','lab_source')
    if source.suffix.lower()!='.glb' or hash_file(source)!=F50_SOURCE_SHA256:
        raise GarageError('F50 rim lab supports only the reviewed original 1995 Ferrari F50 source.','lab_source')
    if not donor.is_dir() or donor.is_symlink():
        raise GarageError('Choose a regular reviewed original donor directory.','donor_path')
    if output.exists() or not output.parent.is_dir() or output.parent.is_symlink():
        raise GarageError('Choose a new output folder inside an existing regular parent.','lab_output')
    resolved=output.resolve();orig=source.resolve();don=donor.resolve()
    if resolved==orig or orig in resolved.parents or resolved==don or don in resolved.parents:
        raise GarageError('Native wheel experiment cannot overwrite its source/donor.','lab_output')
    protected={'pdipfs','usrdir','dev_hdd0','ps3_game'}
    if protected & {component.lower() for component in (*output.parts,*resolved.parts)}:
        raise GarageError('Refusing to write native experiment into a game installation.','lab_output')
    return source,donor,output


def run_f50_wheel_lab(source,donor,output):
    source,donor,output=validate_lab_paths(source,donor,output)
    from .glb import GLB
    from .car_profiles import source_recipe
    from .recognition import infer
    from .rx8_wheel_adapter import experiment_from_verified_donor
    scene=GLB(source.read_bytes()).scene()
    if scene.digest!=F50_SOURCE_SHA256 or len(scene.parts)!=62:
        raise GarageError('F50 source inventory no longer matches reviewed preparation.','source_profile')
    inspection=infer(scene,overrides=source_recipe(scene))
    result=experiment_from_verified_donor(scene,inspection,donor,output)
    notes=('GT6 Garage F50 native wheel LAB - NOT AN INSTALLABLE CAR.\n'
           'These are prototype standalone MDL3 wheel assets ONLY for the REVIEWED original RX-8 donor.\n'
           'The source Ferrari F50 wheel spokes and textured centre emblems are included in HQ and race.\n'
           'Native shadow meshes, spin/blur branches and untouched wheel draw models are retained.\n'
           'The native ~0.250m reference envelope belongs to shadow geometry, not a measured tyre.\n'
           'Source rim dimensions are preserved using rigid rotation and axial translation only.\n'
           'The mount reference is one historical HQ capture; current HQ/race fit is unverified.\n'
           'Physical tyres, full body/axle alignment, suspension, lights and database entry\n'
           'are not implemented or verified here. Do NOT FTP or install loose wheel assets.\n'
           'No game data was installed, repacked, or written by this command.\n')
    (output/'README_NON_INSTALLABLE.txt').write_text(notes,encoding='utf-8')
    atomic_json(output/'LAB_RECEIPT.json',{**result,'runtime_verified':False,'original_F50_and_Cinque_identity_preserved':True,
          'wheel_source_parts':8,'source_rim_triangles_total':7036})
    return {**result,'receipt':str(output/'LAB_RECEIPT.json'),'not_an_installable_car':True}

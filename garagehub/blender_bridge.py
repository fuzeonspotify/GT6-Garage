"""Optional static scene conversion through the player's installed Blender."""
import os
import io
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .errors import GarageError, Cancelled
from .util import MAX_MODEL_BYTES


def opaque_fbx_materials(raw):
    """FBX importers can label a fully opaque colour atlas as alpha-blended."""
    from PIL import Image
    from .glb import GLB
    from .model_inputs import _pack_glb
    glb=GLB(raw)
    doc=glb.doc
    primitives=[prim for mesh in doc.get('meshes',[]) for prim in mesh['primitives']]
    image_alpha={}
    changed=False
    for index,material in enumerate(doc.get('materials',[])):
        if (material.get('alphaMode')!='BLEND' or material.get('extensions')
                or re.search(r'glass|windshield|window|lens',material.get('name',''),re.I)):
            continue
        pbr=material.get('pbrMetallicRoughness',{})
        if pbr.get('baseColorFactor',[1,1,1,1])[3]!=1:continue
        texture=pbr.get('baseColorTexture')
        if texture:
            source=doc['textures'][texture['index']].get('source')
            if source is None:continue
            if source not in image_alpha:
                blob=glb.view(doc['images'][source]['bufferView'])[0]
                with Image.open(io.BytesIO(blob)) as image:
                    image_alpha[source]=(image.width*image.height<=16*1024**2 and
                                         image.convert('RGBA').getchannel('A').getextrema()==(255,255))
            if not image_alpha[source]:continue
        colours=[prim['attributes']['COLOR_0'] for prim in primitives if prim.get('material')==index and 'COLOR_0' in prim['attributes']]
        if any(doc['accessors'][i]['type']=='VEC4' and (glb.accessor(i)[:,3]!=1).any() for i in colours):
            continue
        material['alphaMode']='OPAQUE'
        changed=True
    return _pack_glb(doc,bytes(glb.buffers[0])) if changed else raw


def blender_executable():
    foundation=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Blender Foundation'
    installs=sorted((p for p in foundation.glob('Blender */blender.exe') if p.is_file()),reverse=True)
    return str(installs[0]) if installs else shutil.which('blender')


def convert_model(raw,name,source_path=None,cancel=None):
    from .glb import GLB
    from .car_geometry import check_cancel
    executable=blender_executable()
    if not executable:
        raise GarageError('This format needs Blender. Install Blender locally, or export an uncompressed GLB 2.0 with textures embedded and try again.', 'blender_needed')
    if len(raw)>MAX_MODEL_BYTES:
        raise GarageError('The model exceeds the 256 MiB import limit.', 'model_limit')
    if cancel is not None:check_cancel(cancel)
    worker=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'resources/convert_static.py'
    with tempfile.TemporaryDirectory(prefix='gt6-model-') as folder:
        temp=Path(folder)
        source=Path(source_path).resolve() if source_path else temp/('source'+Path(name).suffix.lower())
        if source_path is None:source.write_bytes(raw)
        output=temp/'converted.glb'
        command=[executable,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
                 '--python',str(worker),'--',str(source),str(output)]
        with (temp/'blender.log').open('wb') as log:
            proc=subprocess.Popen(command,cwd=temp,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                start=time.monotonic()
                while proc.poll() is None:
                    check_cancel(cancel)
                    if time.monotonic()-start>240:
                        raise GarageError('Blender could not finish this model in four minutes. Export the car alone as a smaller GLB.', 'blender_conversion')
                    time.sleep(.12)
            finally:
                if proc.poll() is None:
                    proc.kill();proc.wait()
        if proc.returncode or not output.is_file():
            raise GarageError('Blender could not read this model and export a static GLB. Open the source in Blender and export the car with its textures.', 'blender_conversion')
        if output.stat().st_size>MAX_MODEL_BYTES:raise GarageError('The converted GLB exceeds 256 MiB. Reduce its geometry or textures.', 'model_limit')
        result=output.read_bytes()
        if Path(name).suffix.lower()=='.fbx':result=opaque_fbx_materials(result)
        GLB(result)
        return result,Path(name).stem+'.glb'

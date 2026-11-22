"""Player model tests. Report source, assembly, package and race evidence separately."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .car_geometry import check_cancel
from .errors import Cancelled, GarageError
from .template_shell import check_shell_body_streams,review_shell
from .template_shell_preview import build_template_shell_preview
from .util import MAX_MODEL_BYTES, atomic_json, clean_name, hash_file, read_json, sha256


def component_messages(review, project):
    names={p['id']:p['name'] for p in project['inspection']['parts']}
    return [re.sub(r'\bn\d+p\d+\b',lambda m:f"{names[m[0]]} ({m[0]})" if m[0] in names else m[0],b['message'])
            for b in review['blockers']]


class ModelTests:
    def __init__(self, app):
        self.app = app
        self.folder = app.root/'model-tests'

    def _path(self, test_id):
        if not isinstance(test_id,str) or not re.fullmatch(r'[a-f0-9]{32}',test_id):
            raise GarageError('Choose a model test from the results.')
        return self.folder/(test_id+'.json')

    def _save(self, row):
        with self.app.lock:
            self.folder.mkdir(exist_ok=True)
            atomic_json(self._path(row['id']),row)
        return row

    def results(self):
        with self.app.lock:
            rows = [read_json(path) for path in self.folder.glob('*.json')]
        return sorted(rows,key=lambda r:r['tested_at'],reverse=True)

    def run(self, file_name, get_upload, cancel, source_path=None):
        row = {'schema':1,'version':__version__,'id':uuid.uuid4().hex,
               'file':clean_name(Path(file_name.replace('\\','/')).name),
               'tested_at':datetime.now(timezone.utc).isoformat(timespec='microseconds'),
               'stage':'import','status':'needs_attention','messages':[],
               'race':{'result':'untested','notes':''}}
        try:
            check_cancel(cancel)
            self.app.progress('Reading '+row['file'])
            if source_path is not None and source_path.is_file():
                row['source_bytes']=source_path.stat().st_size
                if row['source_bytes']<=MAX_MODEL_BYTES:row['source_sha256']=hash_file(source_path)
            raw, name = get_upload()
            if source_path is None:
                row.update(source_bytes=len(raw),source_sha256=sha256(raw))
            if name.lower().endswith('.gt6car'):
                raise GarageError('Open a saved project in Car workshop. To start a model test, choose its 3D model file.')
            project = self.app.open_player_file(raw,name,cancel)
            row.update(project_id=project['id'],prepared_sha256=project['source_sha256'],parts=project['part_count'])
            row['material_repairs']=project.get('source_metadata',{}).get('material_repairs',[])
            row['stage']='wheel placement'
            self.app.progress('Measuring four hubs on '+row['file'])
            store=self.app.store
            template=self.app.config.get('car_baseline')
            sizing='saved_placement' if project.get('stock_placement_mode')=='saved_placement' else 'checked_fit'
            setup=store.shell_setup(project['id'],sizing,template_root=template)
            check=setup['wheel_check']
            row['lights']=setup['lights']
            row['wheels']={'set':check['wheel_set'],'status':check['status'],'corners':len(check['corners']),
                'excluded_parts':len(setup['exclusions']), 'diameter_ratio':{
                    w['corner']:w['diameter_ratio'] for w in check['corners']}}
            effects=sorted({effect for part in setup.get('finishes',[]) for effect in part['effects']})
            if effects:row['native_finish_substitutions']=effects
            if check['status']!='compatible':
                row['messages']=list(check['messages'])
                if check['corners'] or not row['messages']:
                    row['messages'].append('Open the project in Car workshop to review the source tyres and adjust placement against the GT6 chassis.')
                return self._save(row)
            # Applying a proposal to this new project preserves it for the tester to edit.
            store.shell_setup(project['id'],sizing,template_root=template,apply=True,token=setup['token'])
            check_cancel(cancel)
            scene,project=store.build_inputs(row['project_id'])
            row['triangles']=scene.triangles
            settings=project['template_shell_settings']
            # A private in-memory candidate tests the geometry a player would review.
            # It grants no saved component approval and cannot create a game package.
            draft={**project,'approved_roles':True,'template_inheritance_approved':True,
                   'installation':{'mode':'new_entry','replace_car_id':None}}
            row['stage']='body preparation'
            review=review_shell(scene,draft,settings['placement'],settings['dispositions'],settings['lighting'],settings['running_gear'])
            if review.get('lamp_mount_checks'):
                row['lamp_mount_checks']=review['lamp_mount_checks']
            row['detail_reductions']={lod:data['textured_reductions'] for lod,data in review.get('prepared_budgets',{}).items()
                                      if data.get('textured_reductions')}
            if not review['available']:
                row['messages']=component_messages(review,project)
                row['blocker_codes']=[b.get('code') for b in review['blockers']]
                for blocker in review['blockers']:
                    if 'material_help' in blocker:
                        row['material_help'] = blocker['material_help']
                        break
                if review['blockers'] and review['blockers'][0].get('code'):
                    row['code'] = review['blockers'][0]['code']
                return self._save(row)
            if not template:
                row.update(stage='source fit',status='needs_game_files',messages=['Source geometry fits the stock hubs. Set up your GT6 files to test the assembled model.'])
                return self._save(row)
            self.app.progress('Checking the garage and race body on '+row['file'])
            row['stage']='garage and race geometry'
            blob,_=build_template_shell_preview(scene,draft,review,template,limit=16000)
            size=int.from_bytes(blob[4:8],'little')
            native=json.loads(blob[16:16+size])['wheel_check']
            row['wheels']['status']=native['status']
            row['wheels']['visibility']={lod:{corner:{role:round(v['fraction'],3) for role,v in roles.items()}
                for corner,roles in corners.items()} for lod,corners in native['visibility'].items()}
            row['wheels']['assets']=native['wheel_files']
            row['messages']=native['messages']
            if native['passed'] and review['ready_for_candidate']:
                row['stage']='native garage and race body'
                self.app.progress('Writing and checking both native body detail levels for '+row['file'])
                row['native_body']=check_shell_body_streams(scene,draft,review,template,cancel)
                row['status']='ready_to_build'
            elif not row['messages']:
                row['messages']=component_messages(review,project)
                row['blocker_codes']=[b.get('code') for b in review['blockers']]
        except Cancelled:
            raise
        except GarageError as exc:
            row.update(code=exc.code,messages=[str(exc)])
        except OSError:
            row.update(code='unreadable',messages=['Garage could not read the selected model or a referenced file. Check that all files are available locally.'])
        except Exception:
            logging.exception('Model test stopped during %s',row['stage'])
            row.update(code='internal_error',messages=['Garage stopped while testing this model. Save the report so the importer can be checked.'])
        return self._save(row)

    def run_paths(self, paths, cancel):
        from .model_inputs import local_model_upload
        return {'tests':[self.run(Path(p).name,lambda p=p:local_model_upload(p,cancel),cancel,Path(p)) for p in paths]}

    def built(self,pid,receipt,zip_path):
        for row in self.results():
            if row.get('project_id')==pid:
                with self.app.lock:
                    row=read_json(self._path(row['id']))
                    row.pop('build_attempt',None)
                    row['build']={'zip':Path(zip_path).name,'car_id':receipt['installation']['car_id'],
                                  'resources_verified':len(receipt['source_virtual_assets']),
                                  'header_sha256':receipt['header_sha256'],'package_sha256':hash_file(Path(zip_path))}
                    row['race']={'result':'untested','notes':''}
                    self._save(row)
                break

    def build_failed(self,pid,code,message):
        with self.app.lock:
            for row in self.results():
                if row.get('project_id')==pid:
                    row['build_attempt']={'result':'failed','code':code,'message':message}
                    self._save(row)
                    break

    def record_race(self,test_id,result,notes,platform):
        if result not in ('untested','passed','needs_fix') or platform not in ('','RPCS3','PS3') or not isinstance(notes,str) or len(notes)>2000:
            raise GarageError('Choose a race result and keep the notes within 2,000 characters.')
        with self.app.lock:
            path=self._path(test_id)
            if not path.is_file():raise GarageError('This test result is no longer available.')
            row=read_json(path)
            row['race']={'result':result,'platform':platform,'notes':notes.strip()}
            return self._save(row)

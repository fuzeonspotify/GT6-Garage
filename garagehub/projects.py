from __future__ import annotations
import copy, json, os, uuid, zipfile, time, io, math, threading
from functools import wraps
from pathlib import Path
import numpy as np
from .errors import GarageError
from .glb import GLB,Scene,MAX_PARTS
from .recognition import infer,ROLES,CORNERS
from .preview import build_preview
from .car_profiles import source_recipe
from .install_targets import installation
from .import_readiness import import_readiness
from .assembly_settings import assembly_mode, saved_shell_decision, shell_status
from .scene_coordinates import coordinate_settings,coordinate_description,prepare_scene,COORDINATE_SYSTEM
from .util import atomic_json,atomic_bytes,read_json,clean_name,safe_rel,sha256,hash_file,MAX_MODEL_BYTES,MAX_PROJECT_BYTES,unique_path

PREPARATION_REVISION=4
MAX_PROJECT_METADATA_BYTES=16*1024**2

def _serial_preparation(method):
    """Keep proposal checks and saved preparation atomic across HTTP threads."""
    @wraps(method)
    def guarded(self,*args,**kwargs):
        with self._preparation_lock:
            return method(self,*args,**kwargs)
    return guarded

def validate_project_metadata(project):
    # Saved face membership can be larger than ordinary assignments. Reject
    # before writing a project that the existing reader/archive cannot reopen.
    try:raw=(json.dumps(project,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8')
    except (TypeError,ValueError) as exc:raise GarageError('Project metadata must contain finite JSON data.','wheel_partition') from exc
    if len(raw)>MAX_PROJECT_METADATA_BYTES:raise GarageError('Saved component selections exceed the project metadata limit. Split the source into separate objects in your modelling tool.','wheel_partition')

class Store:
    def __init__(self,root:Path):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.cache={};self._recipes={};self._preparation_lock=threading.RLock();self._body_fit_previews={};self._shell_previews={};self._shell_preview_served=set()
    def folder(self,pid):
        if not isinstance(pid,str) or len(pid)!=32 or any(c not in '0123456789abcdef' for c in pid):raise GarageError('Unknown project.')
        p=self.root/pid
        if not p.is_dir():raise GarageError('Project is not available.')
        return p
    @_serial_preparation
    def create(self,raw:bytes,filename='car.glb',cancel=None,*,checked_wheels=False,stock_replacement=False,wheel_defaults=None):
        from .model_inputs import prepare_model
        original=raw;raw,source_metadata=prepare_model(raw,filename)
        scene=GLB(raw).scene(cancel);pid=uuid.uuid4().hex;folder=self.root/pid
        # Finish parsing before making a persistent project; source remains unchanged.
        name=clean_name(Path(filename.replace('\\','/')).stem)
        extra=scene.document.get('asset',{}).get('extras',{});extra=extra if isinstance(extra,dict) else {}
        recipe=source_recipe(scene);self._recipes={scene.digest:recipe};inspection=infer(scene,overrides=recipe);project=dict(schema=2,preparation_revision=PREPARATION_REVISION,id=pid,name=name,created=time.time(),source_sha256=scene.digest,source_filename=clean_name(Path(filename.replace('\\','/')).name),forward=inspection['forward'],overrides=recipe,wheel_partitions=[],template_inheritance_approved=False,wheelbase_metres=inspection['wheelbase'],license={k:clean_name(extra.get(k,''),'') for k in ('author','license','source','title')},notes='',approved_roles=False,installation=installation(),source_metadata=source_metadata,**coordinate_settings())
        if stock_replacement:
            from .shell_setup import prepare_stock_replacement
            prepare_stock_replacement(scene,project,inspection,wheel_defaults)
            inspection=infer(scene,project['forward'],project['overrides'])
        elif checked_wheels:
            project.update(assembly_mode='template_shell',template_shell_settings={'running_gear':{'mode':'checked_stock'}})
        folder.mkdir()
        try:
            atomic_bytes(folder/'source.glb',raw);atomic_json(folder/'project.json',project);atomic_json(folder/'inspection.json',inspection);atomic_bytes(folder/'preview.bin',build_preview(scene));atomic_bytes(folder/'software_preview.bin',build_preview(scene,16000));self.cache={self._cache_key(pid,project):scene}
            if original!=raw:atomic_bytes(folder/'original_input.bin',original)
        except Exception:
            import shutil
            shutil.rmtree(folder);raise
        return self.summary(pid)
    @_serial_preparation
    def scene(self,pid):
        folder=self.folder(pid);project=read_json(folder/'project.json')
        return self._prepared_scene(pid,project)
    def _cache_key(self,pid,project):
        settings=coordinate_settings(project.get('source_up_axis','+Y'),project.get('metres_per_unit',1.0))
        partitions=project.get('wheel_partitions',[])
        if not isinstance(partitions,list) or len(partitions)>MAX_PARTS:raise GarageError('Saved wheel partitions are invalid.','wheel_partition')
        try:digest=sha256(json.dumps(partitions,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8'))
        except (TypeError,ValueError) as exc:raise GarageError('Saved wheel partitions must contain finite project data.','wheel_partition') from exc
        return pid,settings['source_up_axis'],settings['metres_per_unit'],digest
    def _source_scene(self,pid,project):
        source=GLB((self.folder(pid)/'source.glb').read_bytes()).scene()
        if source.digest!=project.get('source_sha256'):raise GarageError('The saved source fingerprint changed. Restore the original project.','source_changed')
        self._recipes={source.digest:source_recipe(source)}
        return source
    def _prepare_source(self,source,project):
        """Replay reviewed face membership after the existing axis/unit transform."""
        from .mechanical_parts import replay_wheel_partition
        self._cache_key(project.get('id',''),project)
        prepared=prepare_scene(source,**coordinate_settings(project.get('source_up_axis','+Y'),project.get('metres_per_unit',1.0)))
        decisions=project.get('wheel_partitions',[])
        if not decisions:return prepared
        canonical={part.id:part for part in source.parts};parts=list(prepared.parts);seen=set()
        for decision in decisions:
            if not isinstance(decision,dict) or not isinstance(decision.get('source_part'),dict):raise GarageError('A saved wheel partition is incomplete.','wheel_partition')
            pid=decision['source_part'].get('id')
            if not isinstance(pid,str) or pid in seen or pid not in canonical:raise GarageError('A wheel partition repeats or references an unknown original component.','wheel_partition')
            seen.add(pid);matches=[(i,p) for i,p in enumerate(parts) if p.id==pid]
            if len(matches)!=1:raise GarageError('A wheel partition no longer identifies one original component.','wheel_partition')
            index,part=matches[0]
            children=replay_wheel_partition(part,decision,known_ids=[p.id for p in parts],source_part=canonical[pid])
            parts[index:index+1]=children
        if sum(len(p.indices) for p in parts)!=source.triangles:raise GarageError('Prepared wheel partitions did not retain every source triangle.','wheel_partition')
        return Scene(parts,prepared.materials,prepared.document,prepared.raw,prepared.digest,
                     prepared.warnings+['Reviewed wheel-corner groups retain every original triangle and vertex attribute. Component motion still requires a compatible native profile.'])
    @_serial_preparation
    def known_recipe(self,pid):
        project=read_json(self.folder(pid)/'project.json');scene=self._prepared_scene(pid,project)
        if scene.digest not in self._recipes:self._source_scene(pid,project)
        valid={part.id for part in scene.parts}
        return copy.deepcopy({key:value for key,value in self._recipes[scene.digest].items() if key in valid})
    def _prepared_scene(self,pid,project):
        key=self._cache_key(pid,project)
        if key not in self.cache or self.cache[key].digest!=project.get('source_sha256'):
            source=self._source_scene(pid,project)
            self.cache={key:self._prepare_source(source,project)}
        elif hash_file(self.folder(pid)/'source.glb')!=project.get('source_sha256'):
            raise GarageError('The saved source fingerprint changed. Restore the original project.','source_changed')
        return self.cache[key]
    def _refresh_preparation(self,pid,project):
        revision=project.get('preparation_revision')
        if revision==PREPARATION_REVISION:return project
        if revision is not None and (type(revision) is not int or revision<0 or revision>PREPARATION_REVISION):raise GarageError('This project uses an unsupported preparation revision. Open it with a compatible Garage version.')
        folder=self.folder(pid);coordinates=coordinate_settings(project.get('source_up_axis','+Y'),project.get('metres_per_unit',1.0))
        scene=self._prepare_source(self._source_scene(pid,project),project)
        inspection=infer(scene,project.get('forward'),project.get('overrides',{}))
        preview=build_preview(scene);software_preview=build_preview(scene,16000)
        project.update(coordinates,preparation_revision=PREPARATION_REVISION,approved_roles=False)
        project.setdefault('wheel_partitions',[])
        if 'forward' not in project:project['forward']=inspection['forward']
        if 'wheelbase_metres' not in project:project['wheelbase_metres']=inspection['wheelbase']
        atomic_bytes(folder/'preview.bin',preview);atomic_bytes(folder/'software_preview.bin',software_preview)
        atomic_json(folder/'inspection.json',inspection);atomic_json(folder/'project.json',project)
        self.cache={self._cache_key(pid,project):scene}
        return project
    @_serial_preparation
    def summary(self,pid):
        f=self.folder(pid);p=self._refresh_preparation(pid,read_json(f/'project.json'));i=read_json(f/'inspection.json');s=self.scene(pid)
        coordinates=coordinate_description(p.get('source_up_axis','+Y'),p.get('metres_per_unit',1.0))
        result={**p,**coordinate_settings(coordinates['source_up_axis'],coordinates['metres_per_unit']),'coordinate_transform':coordinates,'coordinate_system':COORDINATE_SYSTEM,'installation':installation(p.get('installation')),'inspection':i,'triangles':s.triangles,'vertices':sum(len(x.positions) for x in s.parts),'part_count':len(s.parts),'source_warnings':s.warnings+p.get('source_metadata',{}).get('warnings',[]),'preview_label':'Prepared source geometry preview — not GT6 footage','native_car_conversion':'Check this model’s next steps and profile limits before preparing a native build.','known_recipe_available':bool(self.known_recipe(pid))}
        result['import_readiness']=import_readiness(s,result)
        result['body_fit_status']=result['import_readiness']['body_fit_status']
        from .template_shell import normalize_shell_settings
        result['assembly_mode']=assembly_mode(p.get('assembly_mode','authored'))
        result['template_shell_settings']=normalize_shell_settings(p.get('template_shell_settings'))
        result['template_shell_status']=shell_status(s,{**result,'inspection':infer(s,p.get('forward'),p.get('overrides',{}))}) if result['assembly_mode']=='template_shell' else {'approved':False,'review_digest':None,'reason':'Choose supplied wheels to review this assembly.','runtime_verified':False}
        return result
    def list(self):
        out=[]
        for f in self.root.iterdir():
            if f.is_dir() and (f/'project.json').is_file():
                try:p=read_json(f/'project.json');out.append({k:p[k] for k in ('id','name','created','source_filename','source_sha256')})
                except (ValueError,KeyError,OSError):continue
        return sorted(out,key=lambda p:p['created'],reverse=True)

    @_serial_preparation
    def player_project(self, pid):
        current = self.summary(pid)
        if current['assembly_mode'] == 'template_shell':
            settings = current['template_shell_settings']
            if settings['running_gear'].get('wheel_set') == 'supra_factory':
                return current
            settings['running_gear']['wheel_set'] = 'supra_factory'
            return self.update(pid, {'template_shell_settings':settings})
        from .shell_setup import prepare_stock_replacement
        scene, project = self.build_inputs(pid)
        prepare_stock_replacement(scene, project, project['inspection'])
        keys = ('assembly_mode', 'template_shell_settings', 'overrides', 'approved_roles')
        return self.update(pid, {key: project[key] for key in keys}, stock_setup_pending=True)
    @_serial_preparation
    def update(self,pid,changes,*,refresh_previews=False,wheel_partitions=None,stock_setup_pending=None):
        f=self.folder(pid);p=self._refresh_preparation(pid,read_json(f/'project.json'))
        if not isinstance(changes,dict):raise GarageError('Project settings must be an object.')
        if stock_setup_pending is not None:
            p['stock_setup_pending'] = stock_setup_pending
        if 'body_fit' in changes:raise GarageError('Use the body-fit review and explicit confirmation to save a fit decision.','body_fit')
        if 'template_shell_decision' in changes:raise GarageError('Use the assembled preview and explicit confirmation to save an assembly decision.','shell_confirmation')
        if 'assembly_mode' in changes:p['assembly_mode']=assembly_mode(changes['assembly_mode'])
        if 'template_shell_settings' in changes:
            from .template_shell import normalize_shell_settings
            settings=normalize_shell_settings(changes['template_shell_settings'])
            if settings!=p.get('template_shell_settings'):p.pop('template_shell_decision',None)
            p['template_shell_settings']=settings
        old_coordinates=coordinate_settings(p.get('source_up_axis','+Y'),p.get('metres_per_unit',1.0))
        coordinates=coordinate_settings(changes.get('source_up_axis',old_coordinates['source_up_axis']),changes.get('metres_per_unit',old_coordinates['metres_per_unit']))
        coordinates_changed=coordinates!=old_coordinates;p.update(coordinates)
        partitions_changed=wheel_partitions is not None and wheel_partitions!=p.get('wheel_partitions',[])
        if wheel_partitions is not None:p['wheel_partitions']=copy.deepcopy(wheel_partitions)
        if 'name' in changes:p['name']=clean_name(changes['name'])
        if 'installation' in changes:p['installation']=installation(changes['installation'])
        if 'forward' in changes:
            if changes['forward'] not in ('+Z','-Z','+X','-X'):raise GarageError('Invalid front direction.')
            p['forward']=changes['forward']
        if 'wheelbase_metres' in changes:
            v=changes['wheelbase_metres']
            if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not .3<=v<=12):raise GarageError('Wheelbase must be between 0.3 and 12 metres, or left unassigned.')
            p['wheelbase_metres']=v
        if 'notes' in changes:p['notes']=str(changes['notes'])[:5000]
        if 'approved_roles' in changes:p['approved_roles']=bool(changes['approved_roles'])
        if 'template_inheritance_approved' in changes:p['template_inheritance_approved']=bool(changes['template_inheritance_approved'])
        if 'overrides' in changes:
            valid={x.id for x in self._prepared_scene(pid,p).parts}
            overrides=changes['overrides']
            if not isinstance(overrides,dict):raise GarageError('Invalid assignments.')
            for key,value in overrides.items():
                if key not in valid or not isinstance(value,dict) or value.get('role') not in ROLES or value.get('corner') not in (*CORNERS,None,''):raise GarageError('Invalid part assignment.')
            p['overrides']=overrides
        scene=self._prepared_scene(pid,p)
        i=infer(scene,None if coordinates_changed and 'forward' not in changes else p['forward'],p['overrides'])
        if coordinates_changed:
            if 'forward' not in changes:p['forward']=i['forward']
            if 'wheelbase_metres' not in changes:p['wheelbase_metres']=i['wheelbase']
            p['approved_roles']=False
        if partitions_changed:p['approved_roles']=False
        validate_project_metadata(p)
        if coordinates_changed or partitions_changed or refresh_previews:
            # Finish both potentially expensive previews before changing any
            # saved metadata or buffers. Every preview uses the same scene.
            preview=build_preview(scene);software_preview=build_preview(scene,16000)
            atomic_bytes(f/'preview.bin',preview);atomic_bytes(f/'software_preview.bin',software_preview)
        atomic_json(f/'inspection.json',i);atomic_json(f/'project.json',p)
        self._body_fit_previews={}
        self._shell_previews={};self._shell_preview_served=set()
        return self.summary(pid)

    def _body_fit_inputs(self,pid):
        """Re-read the original bytes and infer the exact current prepared scene."""
        folder=self.folder(pid);project=self._refresh_preparation(pid,read_json(folder/'project.json'))
        scene=self._prepare_source(self._source_scene(pid,project),project)
        inspection=infer(scene,project.get('forward'),project.get('overrides',{}))
        return project,scene,{**project,'inspection':inspection}

    @_serial_preparation
    def build_inputs(self,pid):
        """Take one fresh source/settings/inspection snapshot for plan and build."""
        _,scene,current=self._body_fit_inputs(pid)
        return scene,current

    @_serial_preparation
    def review_body_fit(self,pid,policy):
        from .body_fit import body_fit_review
        from .body_fit_preview import build_body_fit_preview
        project,scene,current=self._body_fit_inputs(pid)
        review=body_fit_review(scene,current,policy)
        review['id']=pid;review['preview_parts']=[]
        if review['available']:
            binary,parts=build_body_fit_preview(scene,current['inspection'],review)
            self._body_fit_previews={(pid,review['review_digest'],'full'):(binary,parts)}
            review['preview_parts']=parts
        return review

    @_serial_preparation
    def body_fit_preview(self,pid,policy,review_digest,software=False):
        from .body_fit import body_fit_review
        from .body_fit_preview import build_body_fit_preview
        project,scene,current=self._body_fit_inputs(pid)
        review=body_fit_review(scene,current,policy)
        if (not isinstance(review_digest,str) or not review['available']
                or review.get('review_digest')!=review_digest):
            raise GarageError('This body-fit preview is out of date. Review the current model again.','body_fit_stale')
        key=(pid,review_digest,'software' if software else 'full')
        if key not in self._body_fit_previews:
            binary,parts=build_body_fit_preview(scene,current['inspection'],review,limit=16000 if software else 800000)
            self._body_fit_previews={key:(binary,parts)}
        return self._body_fit_previews[key][0]

    @_serial_preparation
    def approve_body_fit(self,pid,policy,review_digest,confirmed):
        from .body_fit import approve_body_fit
        project,scene,current=self._body_fit_inputs(pid)
        decision=approve_body_fit(scene,current,policy,review_digest,confirmed)
        proposed={**project,'body_fit':decision}
        validate_project_metadata(proposed)
        atomic_json(self.folder(pid)/'project.json',proposed)
        return self.summary(pid)

    @_serial_preparation
    def shell_setup(self, pid, sizing, *, apply=False, token=None, template_root=None):
        from .shell_setup import suggest_setup
        _, scene, current = self._body_fit_inputs(pid)
        suggestion = suggest_setup(scene, current, sizing, template_root=template_root)
        if not apply:
            return suggestion
        if not isinstance(token, str) or token != suggestion['token']:
            raise GarageError('The project changed. Find the setup again before applying it.', 'shell_stale')
        changes = suggestion['changes']
        if changes['overrides'] != current.get('overrides', {}):
            changes['approved_roles'] = False
        return self.update(pid, changes, stock_setup_pending=False)

    def _shell_review_inputs(self,pid):
        from .template_shell import normalize_shell_settings,review_shell
        project,scene,current=self._body_fit_inputs(pid)
        if assembly_mode(project.get('assembly_mode','authored'))!='template_shell':
            raise GarageError('Choose supplied wheels before reviewing this assembly.','assembly_mode')
        settings=normalize_shell_settings(project.get('template_shell_settings'))
        review=review_shell(scene,current,settings['placement'],settings['dispositions'],settings['lighting'],settings['running_gear'])
        review.update(id=pid,preview_parts=[],preview_available=False)
        return project,scene,current,settings,review

    def _shell_preview_data(self,pid,scene,current,review,template_root,software=False):
        from .car_profiles import validate_template
        from .template_shell_preview import build_template_shell_preview
        if not template_root:raise GarageError('Choose the owned car package in Setup to preview the supplied wheels.','shell_template')
        # Authenticate even on a binary cache hit; an old preview cannot stand
        # in for a changed template folder.
        checked=validate_template(Path(template_root))
        key=(pid,review['review_digest'],'software' if software else 'full',str(Path(checked['root']).resolve()))
        if key not in self._shell_previews:
            binary,parts=build_template_shell_preview(scene,current,review,Path(template_root),limit=16000 if software else 800000)
            header_size=int.from_bytes(binary[4:8],'little')
            check=json.loads(binary[16:16+header_size])['wheel_check']
            self._shell_previews={key:(binary,parts,check)}
        return self._shell_previews[key]

    @_serial_preparation
    def review_template_shell(self,pid,template_root=None):
        _,scene,current,_,review=self._shell_review_inputs(pid)
        from .wheel_check import needs_native_check
        if review.get('available'):
            try:
                _,parts,check=self._shell_preview_data(pid,scene,current,review,template_root)
                review.update(preview_parts=parts,preview_available=True,actual_native_wheel_geometry=True,wheel_check=check)
                if needs_native_check(review) and not check.get('passed'):
                    review['ready_for_candidate']=False
                    review['blockers']=[row for row in review['blockers'] if row['code']!='wheel_fit']
                    review['blockers'].append({'code':'wheel_fit','message':'Resolve the wheel fit before exporting. '+' '.join(check['messages'])})
            except GarageError as exc:
                if needs_native_check(review):
                    review['ready_for_candidate']=False
                review['blockers'].append({'code':exc.code,'message':str(exc)})
        return review

    @_serial_preparation
    def template_shell_preview(self,pid,review_digest,template_root=None,software=False):
        _,scene,current,_,review=self._shell_review_inputs(pid)
        if (not isinstance(review_digest,str) or not review.get('available')
                or review.get('review_digest')!=review_digest):
            raise GarageError('This assembled preview is out of date. Review the current settings again.','shell_stale')
        binary,_,_=self._shell_preview_data(pid,scene,current,review,template_root,software)
        self._shell_preview_served.add((pid,review_digest))
        return binary

    @_serial_preparation
    def approve_template_shell(self,pid,review_digest,confirmed,template_root=None):
        from .template_shell import approve_shell
        project,scene,current,settings,review=self._shell_review_inputs(pid)
        if confirmed is not True:raise GarageError('Confirm the assembled car and supplied components before saving.','shell_confirmation')
        if (pid,review_digest) not in self._shell_preview_served:
            raise GarageError('Load the current assembled preview before confirming it.','shell_preview_required')
        if review.get('review_digest')!=review_digest:
            raise GarageError('The assembly changed after its preview. Review it again.','shell_stale')
        from .wheel_check import needs_native_check, require_wheel_check
        _,_,check=self._shell_preview_data(pid,scene,current,review,template_root)
        if needs_native_check(review):
            require_wheel_check(check)
        decision=approve_shell(scene,current,settings['placement'],settings['dispositions'],review_digest,confirmed,settings['lighting'],settings['running_gear'])
        proposed={**project,'template_shell_settings':settings,'template_shell_decision':decision}
        validate_project_metadata(proposed)
        atomic_json(self.folder(pid)/'project.json',proposed)
        return self.summary(pid)
    @_serial_preparation
    def partition_wheel_group(self,pid,part_id,role):
        """Explicitly split a reviewed same-role group, retaining original bytes."""
        from .mechanical_parts import partition_wheel_corners
        folder=self.folder(pid);project=read_json(folder/'project.json')
        revision=project.get('preparation_revision')
        if revision is not None and (type(revision) is not int or revision<0 or revision>PREPARATION_REVISION):raise GarageError('Open this project with a compatible Garage version.')
        source=self._source_scene(pid,project);prepared=self._prepare_source(source,project)
        canonical=next((p for p in source.parts if p.id==part_id),None)
        part=next((p for p in prepared.parts if p.id==part_id),None)
        if canonical is None or part is None:raise GarageError('Choose an original unsplit component to split by wheel corner.','wheel_partition')
        inspection=infer(prepared,project.get('forward'),project.get('overrides',{}))
        children,decision=partition_wheel_corners(part,inspection,role,
            known_ids=[p.id for p in prepared.parts],source_part=canonical)
        return self._save_wheel_partition(pid,project,source,part,role,children,decision)

    def _save_wheel_partition(self,pid,project,source,part,role,children,decision):
        """Commit only after saved membership and both previews are verified."""
        folder=self.folder(pid);part_id=part.id
        proposed=copy.deepcopy(project);proposed['wheel_partitions']=proposed.get('wheel_partitions',[])+[decision]
        proposed['overrides']={key:value for key,value in proposed.get('overrides',{}).items() if key!=part_id}
        proposed['overrides'].update({child.id:{'role':role,'corner':child.extras['gt6_corner']} for child in children})
        proposed.update(preparation_revision=PREPARATION_REVISION,approved_roles=False)
        validate_project_metadata(proposed)
        final_scene=self._prepare_source(source,proposed)
        final_inspection=infer(final_scene,proposed.get('forward'),proposed['overrides'])
        preview=build_preview(final_scene);software=build_preview(final_scene,16000)
        atomic_bytes(folder/'preview.bin',preview);atomic_bytes(folder/'software_preview.bin',software)
        atomic_json(folder/'inspection.json',final_inspection);atomic_json(folder/'project.json',proposed)
        self.cache={self._cache_key(pid,proposed):final_scene}
        result=self.summary(pid);result['partition_result']={'source_part':part_id,'role':role,
            'children':[p.id for p in children],'triangles_preserved':len(part.indices),
            'source_bytes_unchanged':True,'runtime_verified':False}
        return result

    def _tyre_discovery(self,pid,part_id):
        """Recompute from original geometry; never trust browser face groups."""
        from .wheel_discovery import discover_tyre_corners
        project=read_json(self.folder(pid)/'project.json')
        revision=project.get('preparation_revision')
        if revision is not None and (type(revision) is not int or revision<0 or revision>PREPARATION_REVISION):
            raise GarageError('Open this project with a compatible Garage version.','tyre_discovery')
        forward=project.get('forward')
        if forward not in ('+Z','-Z','+X','-X'):
            raise GarageError('Review the model’s up axis and front direction before finding the four tyres.','tyre_discovery')
        source=self._source_scene(pid,project);prepared=self._prepare_source(source,project)
        canonical=next((p for p in source.parts if p.id==part_id),None)
        part=next((p for p in prepared.parts if p.id==part_id),None)
        if canonical is None or part is None:
            raise GarageError('Choose an original unsplit mesh containing only the four tyres.','tyre_discovery')
        inspection=infer(prepared,forward,project.get('overrides',{}))
        row=next((p for p in inspection['parts'] if p['id']==part_id),None)
        if row is None or row['role']!='tyre':
            raise GarageError('Assign the selected mesh the Tyre role, then review whether it contains only the four tyres.','tyre_discovery')
        children,decision,report=discover_tyre_corners(part,forward=forward,
            known_ids=[p.id for p in prepared.parts],source_part=canonical,
            **coordinate_settings(project.get('source_up_axis','+Y'),project.get('metres_per_unit',1.0)))
        # The entire saved setup participates: even a role, direction, target,
        # or approval changed in another tab invalidates the displayed proposal.
        proof={'project':project,'decision':decision}
        token=sha256(json.dumps(proof,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8'))
        groups={g['corner']:g for g in decision['groups']}
        proposal={'schema_version':1,'id':pid,'part_id':part_id,'proposal_token':token,
            'forward':forward,**coordinate_settings(project.get('source_up_axis','+Y'),project.get('metres_per_unit',1.0)),
            'corners':[{'corner':corner,'triangles':groups[corner]['triangles'],
                        'vertices':groups[corner]['vertices'],
                        'center_metres':decision['wheel_centers_at_partition'][corner]} for corner in CORNERS],
            'triangles_preserved':len(part.indices),'source_bytes_unchanged':True,
            'runtime_verified':False,'review_required':True}
        return project,source,part,children,decision,report,proposal

    @_serial_preparation
    def discover_tyres(self,pid,part_id):
        """Read-only proposal; no assignments, approvals or files are changed."""
        return self._tyre_discovery(pid,part_id)[-1]

    @_serial_preparation
    def apply_tyre_discovery(self,pid,part_id,proposal_token,confirmed_tyre_only=False):
        """Apply only the current, explicitly reviewed homogeneous-tyre proposal."""
        if confirmed_tyre_only is not True:
            raise GarageError('Confirm that the selected mesh contains only the four tyres before separating it.','tyre_discovery')
        if not isinstance(proposal_token,str) or len(proposal_token)!=64:
            raise GarageError('Find and review the four tyre groups again before applying them.','stale_proposal')
        project,source,part,children,decision,report,proposal=self._tyre_discovery(pid,part_id)
        if proposal_token!=proposal['proposal_token']:
            raise GarageError('The model setup changed after this proposal. Find and review the four tyres again.','stale_proposal')
        result=self._save_wheel_partition(pid,project,source,part,'tyre',children,decision)
        result['tyre_discovery_result']={**proposal,'review_required':False,'applied':True,
            'source_part':part_id,'children':[p.id for p in children],
            'approved_roles':False,'method':report.get('method','projected_triangle_bands')}
        return result

    @_serial_preparation
    def calibrate_units(self,pid):
        """Explicitly calibrate source units against a saved reference wheelbase.

        Validate fresh geometry before any migration or persistent update. The
        reference is not applied by typing it, and neither a native target nor
        a guessed tyre height participates in this source-unit correction.
        """
        from .assembly_fit import assembly_fit_report
        folder=self.folder(pid);project=read_json(folder/'project.json')
        reference=project.get('wheelbase_metres')
        if (isinstance(reference,bool) or not isinstance(reference,(int,float))
                or not .3<=reference<=12 or not math.isfinite(reference)):
            raise GarageError('Enter a finite reference wheelbase between 0.3 and 12 metres before calibrating units.','unit_calibration')
        forward=project.get('forward')
        if forward not in ('+Z','-Z','+X','-X'):
            raise GarageError('Review the front direction before calibrating units.','unit_calibration')
        settings=coordinate_settings(project.get('source_up_axis','+Y'),project.get('metres_per_unit',1.0))
        source=GLB((folder/'source.glb').read_bytes()).scene()
        if source.digest!=project.get('source_sha256'):
            raise GarageError('The saved source fingerprint changed. Restore the original project before calibrating units.','unit_calibration')
        overrides=project.get('overrides',{})

        def measure(scene):
            inspection=infer(scene,forward,overrides)
            # Self-comparison uses the actual prepared XYZ centres only to
            # validate labels, finite dimensions and rank. Its rigid result is
            # never applied and is not a native mount/fit claim.
            report=assembly_fit_report(inspection,{'kind':'explicit_centres','forward':forward,
                'wheel_centers_metres':inspection['wheel_centers'],
                'provenance':{'basis':'same prepared source centres; unit-calibration validation only'}})
            return inspection,report['source']['dimensions']['wheelbase_metres']

        before,measured=measure(self._prepare_source(source,project))
        absolute_scale=settings['metres_per_unit']*reference/measured
        proposed=coordinate_settings(settings['source_up_axis'],absolute_scale)
        prepared=self._prepare_source(source,{**project,**proposed})
        after,measured_after=measure(prepared)

        def tyre_groups(inspection):
            return {(row['id'],row['corner']) for row in inspection['parts']
                    if row['role']=='tyre' and row['corner'] in CORNERS}

        if tyre_groups(before)!=tyre_groups(after):
            raise GarageError('Changing units changed automatic tyre grouping. Assign the four tyre groups explicitly before calibrating.','unit_calibration')
        # Use the just-validated prepared scene even if a previous preview had
        # populated the same absolute-coordinate cache key.
        self.cache={self._cache_key(pid,{**project,**proposed}):prepared}
        result=self.update(pid,{**proposed,'forward':forward,'wheelbase_metres':reference,
                                'approved_roles':False},refresh_previews=True)
        result['unit_calibration']={'schema':1,'method':'explicit_reference_wheelbase',
            'reference_wheelbase_metres':reference,'measured_wheelbase_before_metres':measured,
            'measured_wheelbase_after_metres':measured_after,
            'metres_per_unit_before':settings['metres_per_unit'],'metres_per_unit_after':proposed['metres_per_unit'],
            'source_sha256':source.digest,'source_bytes_unchanged':True,'runtime_verified':False}
        return result
    @_serial_preparation
    def export(self,pid,output:Path):
        f=self.folder(pid);p=self._refresh_preparation(pid,read_json(f/'project.json'));names=['source.glb','project.json','inspection.json']
        # A new archive manifest must not bless changed source bytes. Verify
        # both normalized and retained original input before creating output.
        self._source_scene(pid,p)
        metadata=p.get('source_metadata')
        if metadata is not None:
            original=f/'original_input.bin' if (f/'original_input.bin').is_file() else f/'source.glb'
            if (not isinstance(metadata,dict) or metadata.get('normalized_sha256')!=p['source_sha256']
                    or hash_file(original)!=metadata.get('source_sha256')):
                raise GarageError('The saved original input changed. Restore it before exporting the project.','source_changed')
        if (f/'original_input.bin').is_file():names.append('original_input.bin')
        manifest={'schema':2,'kind':'gt6-preparation-project','source_sha256':p['source_sha256'],'game_ready':False,'files':{name:sha256((f/name).read_bytes()) for name in names}}
        if output.exists():raise GarageError('Output already exists.')
        with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=4) as z:
            for name in names:z.write(f/name,name)
            z.writestr('manifest.json',json.dumps(manifest,indent=2));z.writestr('README.txt','Editable GT6 Garage preparation project. Not an installable game mod. Contains your source model, assignments, dimensions and attribution.\n')
        return {'path':str(output),'bytes':output.stat().st_size,'game_ready':False}
    @_serial_preparation
    def import_archive(self,raw:bytes):
        if len(raw)>MAX_PROJECT_BYTES:raise GarageError('Project archive exceeds its original-plus-normalized source limit.')
        try:z=zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as e:raise GarageError('Not a valid project archive.') from e
        with z:
            names=z.namelist()
            if len(names)!=len(set(names)) or len(names)>10:raise GarageError('Duplicate/excessive project entries.')
            for n in names:safe_rel(n)
            if not {'source.glb','project.json','manifest.json'}<=set(names):raise GarageError('Project is incomplete.')
            if set(names)-{'source.glb','project.json','inspection.json','manifest.json','original_input.bin','README.txt'}:raise GarageError('Project contains unknown files.')
            for info in z.infolist():
                cap=MAX_MODEL_BYTES if info.filename in ('source.glb','original_input.bin') else 16*1024**2
                if info.file_size>cap:raise GarageError('A project member exceeds its size limit: '+info.filename)
            if sum(i.file_size for i in z.infolist())>2*MAX_MODEL_BYTES+20*1024**2:raise GarageError('Expanded project is too large.')
            try:p=json.loads(z.read('project.json'));m=json.loads(z.read('manifest.json'));source=z.read('source.glb')
            except (ValueError,KeyError) as e:raise GarageError('Project metadata is invalid.') from e
            if not isinstance(p,dict) or not isinstance(m,dict) or m.get('schema') not in (1,2):raise GarageError('Unsupported project metadata schema.')
            if m.get('kind')!='gt6-preparation-project' or sha256(source)!=m.get('source_sha256') or p.get('source_sha256')!=m['source_sha256']:raise GarageError('Project source hash does not match.')
            if m.get('schema')==2:
                hashes=m.get('files',{})
                if not isinstance(hashes,dict) or set(hashes)!=set(names)-{'manifest.json','README.txt'} or not {'source.glb','project.json','inspection.json'}<=set(hashes):raise GarageError('Project file manifest is invalid.')
                if any(name not in names or sha256(z.read(name))!=digest for name,digest in hashes.items()):raise GarageError('A saved project file changed after export.')
            metadata=p.get('source_metadata');original=None
            if metadata is not None:
                if (not isinstance(metadata,dict) or metadata.get('schema')!=1 or metadata.get('input_format') not in ('glb','gltf','obj','zip') or metadata.get('normalized_format')!='glb' or not isinstance(metadata.get('source_filename'),str) or not metadata['source_filename'] or not isinstance(metadata.get('warnings'),list) or any(not isinstance(x,str) for x in metadata['warnings'])):raise GarageError('Original source metadata is invalid.')
                if metadata.get('normalized_sha256')!=sha256(source):raise GarageError('Normalized source provenance does not match.')
                original=z.read('original_input.bin') if 'original_input.bin' in names else source
                if metadata.get('source_sha256')!=sha256(original):raise GarageError('Original source provenance does not match.')
                from .model_inputs import prepare_model
                rebuilt,_=prepare_model(original,metadata['source_filename'])
                if sha256(rebuilt)!=sha256(source):raise GarageError('The archived original does not reproduce its normalized model.')
            elif 'original_input.bin' in names:raise GarageError('Original input has no source provenance metadata.')
            new=self.create(source,'normalized.glb')
            try:
                self.update(new['id'],{k:v for k,v in p.items() if k in ('name','forward','wheelbase_metres','notes','overrides','approved_roles','template_inheritance_approved','installation','source_up_axis','metres_per_unit','assembly_mode','template_shell_settings')},wheel_partitions=p.get('wheel_partitions',[]))
                folder=self.folder(new['id']);updated=read_json(folder/'project.json')
                updated['source_filename']=clean_name(p.get('source_filename','car.glb'))
                # An archived, verified setup already includes its explicit
                # axis/scale and review state. Loading that same setup is not a
                # new interactive coordinate edit that invalidates the review.
                updated['approved_roles']=bool(p.get('approved_roles',False)) if p.get('preparation_revision')==PREPARATION_REVISION else False
                if p.get('body_fit') is not None:
                    decision=p['body_fit']
                    if (not isinstance(decision,dict) or set(decision)!={'schema','policy','review_digest'}
                            or type(decision.get('schema')) is not int or decision['schema']!=1
                            or decision.get('policy') not in ('keep_proportions','adapt_body')
                            or not isinstance(decision.get('review_digest'),str) or len(decision['review_digest'])!=64
                            or any(c not in '0123456789abcdef' for c in decision['review_digest'])):
                        raise GarageError('Saved body-fit review metadata is invalid.','body_fit')
                    # The resolver recomputes the proof against the new project.
                    # Stale choices remain editable, but cannot approve a build.
                    updated['body_fit']=copy.deepcopy(decision)
                if p.get('template_shell_decision') is not None:
                    # Structural/settings validation only. Current source and
                    # backend proof are recomputed before status or building.
                    updated['template_shell_decision']=copy.deepcopy(p['template_shell_decision'])
                    # Old choices remain editable and exportable, but their
                    # schema1 approval cannot authorize the schema2 resolver.
                    saved_shell_decision(updated,allow_legacy=True)
                if metadata is not None:
                    if original!=source:atomic_bytes(folder/'original_input.bin',original)
                    updated['source_metadata']=metadata
                if p.get('stock_placement_mode')=='saved_placement':
                    updated['stock_placement_mode']='saved_placement'
                if type(p.get('stock_setup_pending')) is bool:
                    updated['stock_setup_pending']=p['stock_setup_pending']
                atomic_json(folder/'project.json',updated)
                return self.summary(new['id'])
            except Exception:
                import shutil
                shutil.rmtree(self.folder(new['id']));self.cache={};raise
    @_serial_preparation
    def neutral(self,pid,output:Path):
        p=self.summary(pid);s=self.scene(pid);assign={v['id']:v for v in p['inspection']['parts']};binary=bytearray();parts=[]
        def put(a):
            binary.extend(b'\0'*(-len(binary)%16));offset=len(binary);b=a.tobytes();binary.extend(b);return {'offset':offset,'bytes':len(b),'count':len(a)}
        for src in s.parts:
            a=assign[src.id]
            if a['role']=='ignore':continue
            parts.append({'id':src.id,'name':src.name,'role':a['role'],'corner':a['corner'],'motion_intent':a.get('motion_intent'),'role_review_required':a.get('role_review_required',False),'mechanical_role_proposals':a.get('mechanical_role_proposals',[]),'material':src.material,'buffers':{'positions':put(src.positions.astype('<f4')),'normals':put(src.normals.astype('<f4')),'uv':put(src.uv.astype('<f4')),'triangles':put(src.indices.astype('<u4'))}})
        doc={'schema':1,'kind':'gt6-neutral-car','name':p['name'],'forward':p['forward'],'wheelbase_metres':p['wheelbase_metres'],'source_sha256':s.digest,'geometry_sha256':sha256(binary),'materials':s.materials,'parts':parts,'license':p['license'],'game_ready':False,'coordinate_system':COORDINATE_SYSTEM,'source_up_axis':p['source_up_axis'],'metres_per_unit':p['metres_per_unit'],'coordinate_transform':p['coordinate_transform']}
        if output.exists():raise GarageError('Output already exists.')
        with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=4) as z:z.writestr('car.json',json.dumps(doc,indent=2));z.writestr('geometry.bin',binary)
        return {'path':str(output),'parts':len(parts),'triangles':sum(len(x.indices) for x in s.parts if assign[x.id]['role']!='ignore'),'game_ready':False}

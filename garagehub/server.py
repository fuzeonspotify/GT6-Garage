"""Loopback-only desktop web UI. No AI, telemetry, external requests or game launch.
Browser session is protected by a per-process token, Origin and Host checks.
"""
from __future__ import annotations
import concurrent.futures, http.server, json, logging, mimetypes, os, secrets, subprocess, sys, threading, time, traceback, urllib.parse, uuid
from pathlib import Path
from . import __version__
from .errors import GarageError,Cancelled
from .projects import Store
from .packages import source_package,export_package
from .car_profiles import validate_template
from .car_builder import plan as car_plan,build_car
from .car_pack import baseline as check_baseline,trusted_packer,pack_candidate,finish_zip
from .identity_pipeline import identity_plan, target_catalogue, route_candidate
from .fit_review import selected_target_assembly_review
from .assembly_settings import assembly_mode,saved_shell_decision
from .util import atomic_json,read_json,unique_path,safe_slug,MAX_MODEL_BYTES,MAX_PROJECT_BYTES

class App:
    def __init__(self,root:Path,web:Path,*,allow_dialogs=True,development_authored=False):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.web=web;self.store=Store(self.root/'projects');self.exports=self.root/'exports';self.exports.mkdir(exist_ok=True)
        self.token=secrets.token_urlsafe(32);self.jobs={};self.downloads={};self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=1);self.busy=None;self.lock=threading.RLock();self.last_seen=time.monotonic();self.allow_dialogs=allow_dialogs
        self.config=read_json(self.root/'settings.json') if (self.root/'settings.json').exists() else {}
        self.server=None
        self.included_baseline = None
        self.development_authored=development_authored
        from .model_test import ModelTests
        self.model_tests=ModelTests(self)

    def open_player_file(self, raw, name, cancel):
        if Path(name).suffix.lower() == '.gt6car':
            project = self.store.import_archive(raw)
            return self.store.player_project(project['id'])
        def create(data,filename):
            return self.store.create(data,filename,cancel,stock_replacement=True,wheel_defaults=self.config.get('wheel_defaults'))
        from .blender_bridge import convert_model
        if Path(name).suffix.lower() in ('.fbx','.blend'):
            return create(*convert_model(raw,name,cancel=cancel))
        try:
            return create(raw,name)
        except GarageError as exc:
            draco=exc.code=='unsupported_extension' and 'KHR_draco_mesh_compression' in str(exc)
            if Path(name).suffix.lower()!='.glb' or not (draco or exc.code=='compressed_mesh'):
                raise
            return create(*convert_model(raw,name,cancel=cancel))

    def wheel_preferences(self, project_id=None):
        from .shell_setup import stock_defaults
        with self.lock:
            settings=self.store.player_project(project_id)['template_shell_settings'] if project_id is not None else None
            defaults=stock_defaults(settings)
            self.config['wheel_defaults']=defaults
            self.save_config()
        return defaults

    def detect_companions(self, installation):
        support = Path(installation)/'Support'
        if not support.is_dir():
            return
        included = support/'Starting package'
        if (included/'package_receipt.json').is_file():
            try:
                check_baseline(included)
                validate_template(included)
                self.included_baseline = included.resolve()
            except (GarageError, OSError, ValueError):
                pass
        changed = False
        for key, path, check in (
                ('car_baseline', support/'Starting package', check_baseline),
                ('stock_template', support/'Car template', validate_template),
                ('car_packer', support/'GTToolsSharp'/'GTToolsSharp.exe', trusted_packer)):
            current=self.config.get(key)
            if key == 'car_baseline' and self.included_baseline is None:
                continue
            if (current and Path(current).exists()) or not path.exists():
                continue
            try:
                if key != 'car_baseline':
                    check(path)
            except (GarageError, OSError, ValueError):
                continue
            self.config[key] = str(path.resolve())
            changed = True
        if changed:
            self.save_config()

    def included_setup(self):
        selected = self.config.get('car_baseline')
        return {'available': self.included_baseline is not None,
                'active': bool(self.included_baseline and selected and
                               Path(selected).resolve() == self.included_baseline)}

    def use_included_baseline(self):
        with self.lock:
            if self.busy and self.jobs[self.busy]['state'] in ('queued', 'running'):
                raise GarageError('Wait for the current operation to finish.')
            if self.included_baseline is None:
                raise GarageError('Extract the complete friends kit to use its starting build.')
            check_baseline(self.included_baseline)
            validate_template(self.included_baseline)
            self.config['car_baseline'] = str(self.included_baseline)
            self.save_config()
        return self.included_setup()
    def submit(self,label,func):
        with self.lock:
            if self.busy and self.jobs[self.busy]['state'] in ('queued','running'):raise GarageError('Another operation is running. Wait for it or cancel it.', 'busy')
            jid=uuid.uuid4().hex;cancel=threading.Event();self.jobs[jid]={'id':jid,'label':label,'state':'queued','cancel':cancel,'started':time.time()};self.busy=jid
        def run():
            job=self.jobs[jid];job['state']='running'
            try:
                result=func(cancel)
                job['result']=result;job['state']='complete'
                if isinstance(result,dict) and 'path' in result:
                    path=Path(result['path']);key=secrets.token_urlsafe(20);self.downloads[key]=path;result['download']='/download/'+key
            except GarageError as e:job.update(state='cancelled' if isinstance(e,Cancelled) else 'error',error=str(e),code=e.code)
            except Exception as e:
                logging.exception('Operation failed: %s',label);job.update(state='error',error='The operation stopped safely. See the local diagnostic log for details.',code='internal_error')
            finally:job['finished']=time.time()
        self.executor.submit(run);return self.job(jid)
    def job(self,jid):
        if jid not in self.jobs:raise GarageError('Unknown operation.')
        return {k:v for k,v in self.jobs[jid].items() if k!='cancel'}
    def save_config(self):atomic_json(self.root/'settings.json',self.config)
    def state(self):
        selected = self.config.get('car_baseline')
        stock_active = bool(selected and Path(selected).resolve() == (self.root/'stock-BCES01893-122').resolve())
        return dict(version=__version__,projects=self.store.list(),workspace=str(self.root),exports=str(self.exports),blender_required=False,ai_required=False,native_template=self.config.get('native_template'),package_folder=self.config.get('package_folder'),car_baseline=selected,stock_setup_active=stock_active,stock_template=self.config.get('stock_template'),car_packer=self.config.get('car_packer'),included_setup=self.included_setup(),capabilities={'glb_import':True,'source_formats':['glb','obj','gltf','zip'],'optional_blender_formats':['fbx','blend','draco-glb'],'editable_projects':True,'neutral_export':True,'wheel_source':'GT6 stock showroom and race wheel set','full_car_conversion':False,'profile_exterior_conversion':'experimental reviewed-profile native conversion','additional_car_database':'experimental isolated new-entry and compatible replacement builds','checked_package_export':True,'game_launch':False,'direct_ftp':False})
    def path_out(self,pid,extension,suffix=''):
        name=self.store.summary(pid)['name'];return unique_path(self.exports,safe_slug(name)+suffix+extension)
    def choose(self,kind):
        if not self.allow_dialogs:raise GarageError('Native file dialogs are disabled for this test server.')
        if kind not in ('template','package','model','test-models','game-data','profile-folder','profile-builder','car-baseline','car-packer'):raise GarageError('Unknown file dialog.')
        if kind=='profile-builder':
            command = [sys.executable] if getattr(sys,'frozen',False) else [sys.executable, str(Path(__file__).resolve().parent.parent/'run.py')]
            subprocess.Popen([*command,'--template-wizard','--workspace',str(self.root)], creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return {'opened':True}
        result=self.root/('dialog_'+uuid.uuid4().hex+'.json')
        command=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve().parent.parent/'run.py')]
        command+=['--dialog',kind,'--dialog-result',str(result)]
        try:
            proc=subprocess.run(command,timeout=180,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            data=read_json(result) if result.exists() else {'path':''}
            if data.get('error'):raise GarageError(data['error'])
            if kind=='test-models':
                paths=data.get('paths',[])
                return self.submit('Test selected car models',lambda c:self.model_tests.run_paths(paths,c)) if paths else {'cancelled':True}
            value=data.get('path','')
            if not value:return {'cancelled':True}
            p=Path(value)
            if kind=='game-data':
                return self.submit('Prepare from your GT6 files',lambda c:self.prepare_from_game(p,None,c))
            if kind=='profile-folder':
                profile=validate_template(p);self.config['stock_template']=profile['root'];self.save_config();return {'path':profile['root'],'kind':kind}
            if kind=='car-baseline':
                checked=check_baseline(p);validate_template(p);self.config['car_baseline']=str(p);self.save_config();return {'path':str(p),'kind':kind,'profile':checked}
            if kind=='car-packer':
                checked=trusted_packer(p);self.config['car_packer']=str(p);self.save_config();return checked
            if kind=='template':
                from .wheels import TEMPLATES
                from .util import hash_file
                if not p.is_file() or p.stat().st_size>8*1024**2 or hash_file(p) not in TEMPLATES:raise GarageError('Choose the stock Supra race/wheel file or reviewed v029 wheel asset.')
                self.config['native_template']=str(p);self.save_config();return {'path':str(p),'kind':kind}
            if kind=='package':
                _,manifest=source_package(p);self.config['package_folder']=str(p);self.save_config();return {'path':str(p),'manifest':manifest}
            if p.suffix.lower() not in ('.glb','.obj','.gltf','.fbx','.blend','.zip','.gt6car'):raise GarageError('Choose GLB, glTF, OBJ, FBX, Blend, a model ZIP or a saved project.')
            if p.stat().st_size>(MAX_PROJECT_BYTES if p.suffix.lower()=='.gt6car' else MAX_MODEL_BYTES):raise GarageError('File exceeds the import size limit for its type.')
            from .model_inputs import local_model_upload
            return self.submit('Import model',lambda c:self.open_player_file(*local_model_upload(p,c),c))
        finally:
            if result.exists():result.unlink()
    def progress(self,text):
        with self.lock:
            if self.busy and self.jobs[self.busy]['state']=='running':self.jobs[self.busy]['progress']=str(text)[:300]
    def prepare_from_game(self,update,template,cancel):
        from .stock_setup import prepare_stock
        packer=self.config.get('car_packer')
        if not packer:raise GarageError('Extract the full tester ZIP so Garage can find its included packer.')
        template=template or self.config.get('stock_template') or (self.root/'Car template' if (self.root/'Car template').is_dir() else None)
        result=prepare_stock(self.root,update,template,Path(packer),cancel,self.progress)
        with self.lock:
            self.config['car_baseline']=result['folder'];self.save_config()
        return {'prepared':True,'region':result['region'],'update':result['update']}
    def car_targets(self,cancel=None):
        base=self.config.get('car_baseline');packer=self.config.get('car_packer')
        if not base or not packer:raise GarageError('Choose the owned baseline and native packer first.')
        return target_catalogue(Path(base),Path(packer),self.root/'database_cache',cancel)
    def use_built_baseline(self,jid):
        job=self.job(jid);result=job.get('result',{})
        if job['state']!='complete' or not result.get('complete_native_package') or not result.get('build_folder'):
            raise GarageError('Choose a completed car build from this application session.')
        package=Path(result['build_folder'])/'package';check_baseline(package);validate_template(package)
        self.config['car_baseline']=str(package);self.save_config()
        return {'path':str(package),'combined_baseline':True,'runtime_verified':False}
    def conversion_plan(self,pid,*,inputs=None):
        base=self.config.get('car_baseline');packer=self.config.get('car_packer')
        if not base:raise GarageError('Set up your GT6 files in Model tester or choose a compatible starting package in Setup.')
        scene,project=self.store.build_inputs(pid) if inputs is None else inputs
        shell=assembly_mode(project.get('assembly_mode','authored'))=='template_shell'
        if not shell and not self.development_authored:
            raise GarageError('Open this project and review its replacement GT6 wheels before building.', 'game_wheels_required')
        if not self.development_authored and (project.get('template_shell_settings') or {}).get('running_gear',{}).get('wheel_set') != 'supra_factory':
            raise GarageError('Reopen the project to load and review the factory GT6 wheel set.', 'game_wheels_required')
        if shell:
            from .template_shell import plan_shell
            from .car_profiles import PROFILE
            result={'profile':PROFILE['id'],'label':'Supplied-wheel assembly','ready':False,'actions':[],
                    'limits':list(PROFILE['limits'])+['Development evaluation: reference-pose wheel preview, lighting and driving still require game validation.'],
                    'inherited':list(PROFILE['inherited'])+['Complete native wheels'],
                    'source_sha256':scene.digest,'parts':len(scene.parts),'source_triangles':scene.triangles,
                    'output_kind':'Separate supplied-wheel car candidate','runtime_verified':False,'assembly_mode':'template_shell'}
            try:
                decision=saved_shell_decision(project)
                native=plan_shell(scene,project,Path(base),decision)
                result.update(ready=native['ready'],template=native['template'],assembly_review=native['review'],fit=native['review']['fit'])
            except GarageError as exc:result['actions'].append(str(exc))
        else:result=car_plan(scene,project,Path(base))
        check_baseline(Path(base))
        if not packer:result['actions'].append('Choose the reviewed append-only packer once.');result['ready']=False
        else:
            result['packer']=trusted_packer(Path(packer))
            try:destination=identity_plan(project,Path(base),Path(packer),self.root/'database_cache')
            except GarageError as exc:destination={'ready':False,'blockers':[str(exc)],'mode':project.get('installation',{}).get('mode')}
            result['installation']=destination
            if destination.get('target_geometry') and not shell:
                result['selected_target_fit']=selected_target_assembly_review(project['inspection'],destination['target_geometry'],project.get('wheelbase_metres'))
            result['actions'].extend(destination.get('blockers',[]))
            result['ready']=result['ready'] and destination['ready']
            if destination.get('ready'):
                result['output_kind']=('New separate car entry' if destination['mode']=='new_entry' else 'Replacement of selected car')+' · '+destination['display_name']+' · ID '+str(destination['car_id'])
        result['limits']=[x for x in result['limits'] if 'does not add a database entry' not in x]
        return result
    def convert_car(self,pid,cancel):
        try:
            return self._build_player_car(pid,cancel)
        except Cancelled:
            raise
        except GarageError as exc:
            self.model_tests.build_failed(pid,exc.code,str(exc))
            raise
        except Exception:
            self.model_tests.build_failed(pid,'build_error','The build stopped. See the local diagnostics for this attempt.')
            raise
    def _build_player_car(self,pid,cancel):
        inputs=self.store.build_inputs(pid);scene,project=inputs
        review=self.conversion_plan(pid,inputs=inputs)
        if not review['ready']:raise GarageError(' '.join(review['actions']))
        base=Path(self.config['car_baseline']);packer=Path(self.config['car_packer'])
        runs=self.root/'builds';runs.mkdir(exist_ok=True);run=runs/(safe_slug(project['name'])+'_'+uuid.uuid4().hex[:12]);run.mkdir()
        atomic_json(run/'plan.json',review)
        if assembly_mode(project.get('assembly_mode','authored'))=='template_shell':
            from .template_shell import build_shell
            model=build_shell(scene,project,base,run/'model',saved_shell_decision(project),cancel,self.progress)
        else:model=build_car(scene,project,base,run/'model',cancel,self.progress)
        route_candidate(run/'model',base,packer,self.root/'database_cache',review['installation'],run/'candidate',cancel,self.progress)
        receipt=pack_candidate(run/'candidate',base,packer,run/'package',cancel,self.progress)
        export_path=self.path_out(pid,'.zip','_CAR_TEST')
        result=finish_zip(run/'package',export_path,cancel)
        result['body_texture_loading']={lod:body['texture_loading'] for lod,body in (model or {}).get('body',{}).items() if 'texture_loading' in body}
        if model and 'driver_fit' in model and model['driver_fit'] is not None:
            result['driver_fit']=model['driver_fit']
        result['build_folder']=str(run);result['native_files']=len(receipt['files']);result['scope']=review['output_kind'];result['installation']=review['installation']
        if project.get('assembly_mode')=='template_shell':
            result.update(assembly_mode='template_shell',development_evaluation=True,runtime_verified=False)
        atomic_json(run/'result.json',result)
        self.model_tests.built(pid,receipt,export_path)
        return result
    def close(self):
        for job in self.jobs.values():job['cancel'].set()
        if self.server:threading.Thread(target=self.server.shutdown,daemon=True).start()


def handler_type(app:App):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass # Never log URL tokens, file paths or user names.
        def send(self,code,data,ctype='application/json',filename=None):
            if isinstance(data,dict) or isinstance(data,list):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
            elif isinstance(data,str):data=data.encode('utf-8')
            self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Cross-Origin-Resource-Policy','same-origin');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if filename:self.send_header('Content-Disposition','attachment; filename="'+safe_slug(Path(filename).stem)+Path(filename).suffix+'"')
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def authorize(self,required=True):
            host=self.headers.get('Host','');expected=f'127.0.0.1:{self.server.server_port}'
            if host!=expected:raise GarageError('Invalid host.', 'unauthorized')
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+expected:raise GarageError('Cross-origin request rejected.', 'unauthorized')
            if required:
                token=self.headers.get('X-Garage-Token') or urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('token',[''])[0]
                if not secrets.compare_digest(token,app.token):raise GarageError('This desktop session has expired. Reopen GT6 Garage.', 'unauthorized')
                app.last_seen=time.monotonic()
        def do_GET(self):
            try:
                path=urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                protected=path.startswith(('/api/','/preview/','/download/'));self.authorize(protected)
                if path=='/api/state':
                    from .shell_setup import stock_defaults
                    return self.send(200,{**app.state(),'wheel_defaults':app.config.get('wheel_defaults',stock_defaults())})
                if path.startswith('/api/project/'):return self.send(200,app.store.player_project(path.rsplit('/',1)[1]))
                if path.startswith('/api/job/'):return self.send(200,app.job(path.rsplit('/',1)[1]))
                if path=='/api/model-tests':return self.send(200,{'tests':app.model_tests.results()})
                if path.startswith('/api/template-shell-preview/'):
                    query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    binary=app.store.template_shell_preview(path.rsplit('/',1)[1],query.get('review_digest',[None])[0],app.config.get('car_baseline'),
                        query.get('software',[''])[0]=='1' or query.get('quality',[''])[0]=='software')
                    return self.send(200,binary,'application/octet-stream')
                if path.startswith('/api/body-fit-preview/'):
                    query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    binary=app.store.body_fit_preview(path.rsplit('/',1)[1],query.get('policy',[None])[0],
                        query.get('review_digest',[None])[0],query.get('quality',[''])[0]=='software')
                    return self.send(200,binary,'application/octet-stream')
                if path.startswith('/preview/'):
                    software=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('quality',[''])[0]=='software'
                    p=app.store.folder(path.rsplit('/',1)[1])/('software_preview.bin' if software else 'preview.bin');return self.send(200,p.read_bytes(),'application/octet-stream')
                if path.startswith('/download/'):
                    key=path.rsplit('/',1)[1]
                    if key not in app.downloads:raise GarageError('Export no longer available. Export it again.')
                    p=app.downloads[key];return self.send(200,p.read_bytes(),'application/octet-stream',p.name)
                static={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/viewer.js':'viewer.js','/driver-seat.js':'driver-seat.js','/style.css':'style.css','/icon.svg':'icon.svg','/example-sedan.glb':'examples/kenney_sedan.glb','/tester.html':'tester.html','/tester.js':'tester.js','/tester.css':'tester.css'}
                if path not in static:return self.send(404,{'error':'Not found'})
                p=app.web/static[path];return self.send(200,p.read_bytes(),mimetypes.guess_type(p)[0] or 'application/octet-stream')
            except GarageError as e:return self.send(403 if e.code=='unauthorized' else 400,{'error':str(e),'code':e.code})
            except Exception:logging.exception('GET failed');return self.send(500,{'error':'Request failed. Consult the local diagnostic log.'})
        def do_POST(self):
            try:
                self.authorize();path=urllib.parse.urlsplit(self.path).path
                try:n=int(self.headers.get('Content-Length','0'))
                except ValueError:raise GarageError('Invalid request length.')
                limit=MAX_PROJECT_BYTES if path=='/api/import-project' else MAX_MODEL_BYTES if path in ('/api/import','/api/test-model') else 1024**2
                if n<0 or n>limit:raise GarageError('Request exceeds the allowed size.', 'file_limit')
                self.connection.settimeout(60);raw=self.rfile.read(n)
                if len(raw)!=n:raise GarageError('Incomplete request.')
                if path=='/api/import':
                    name=urllib.parse.unquote(self.headers.get('X-Filename','car.glb'))
                    return self.send(202,app.submit('Read and prepare model',lambda c:app.open_player_file(raw,name,c)))
                if path=='/api/test-model':
                    name=urllib.parse.unquote(self.headers.get('X-Filename','car.glb'))
                    return self.send(202,app.submit('Test your model',lambda c:app.model_tests.run(name,lambda:(raw,name),c)))
                if path=='/api/import-project':return self.send(202,app.submit('Open saved project',lambda c:app.open_player_file(raw,'saved.gt6car',c)))
                data=json.loads(raw or b'{}')
                if not isinstance(data,dict):raise GarageError('Expected a JSON object.')
                if path=='/api/heartbeat':return self.send(200,{'alive':True})
                if path=='/api/quit':self.send(200,{'closed':True});app.close();return
                if path=='/api/cancel':
                    jid=data.get('id');app.job(jid);app.jobs[jid]['cancel'].set();return self.send(200,{'requested':True})
                if path=='/api/pick':return self.send(200,app.choose(data.get('kind')))
                if path=='/api/test-race':return self.send(200,app.model_tests.record_race(data.get('id'),data.get('result'),data.get('notes',''),data.get('platform','')))
                if path=='/api/wheel-defaults':
                    if data.get('mode')=='automatic':
                        return self.send(200,app.wheel_preferences())
                    if data.get('mode')=='from_project' and isinstance(data.get('id'),str):
                        return self.send(200,app.wheel_preferences(data['id']))
                    raise GarageError('Choose automatic placement or save the current car’s placement.')
                if path=='/api/settings':
                    changes=data.get('changes',{})
                    if isinstance(changes,dict) and changes.get('assembly_mode','template_shell')!='template_shell':
                        raise GarageError('Choose the default GT6 wheels for this car.', 'game_wheels_required')
                    if isinstance(changes,dict) and isinstance(changes.get('template_shell_settings'),dict):
                        gear=changes['template_shell_settings'].setdefault('running_gear',{})
                        if isinstance(gear,dict):
                            if gear.get('wheel_set','supra_factory')!='supra_factory':
                                raise GarageError('Choose the available GT6 wheel set.', 'game_wheels_required')
                            gear['wheel_set']='supra_factory'
                    return self.send(200,app.store.update(data['id'],changes))
                if path=='/api/body-fit-review':return self.send(200,app.store.review_body_fit(data['id'],data.get('policy')))
                if path=='/api/approve-body-fit':return self.send(200,app.store.approve_body_fit(data['id'],data.get('policy'),data.get('review_digest'),data.get('confirmed',False)))
                if path=='/api/template-shell-review':return self.send(200,app.store.review_template_shell(data['id'],app.config.get('car_baseline')))
                if path=='/api/suggest-shell-setup':
                    return self.send(200, app.store.shell_setup(data['id'], data.get('sizing'), template_root=app.config.get('car_baseline')))
                if path=='/api/apply-shell-setup':
                    return self.send(200, app.store.shell_setup(data['id'], data.get('sizing'), apply=True, token=data.get('token'), template_root=app.config.get('car_baseline')))
                if path=='/api/approve-template-shell':return self.send(200,app.store.approve_template_shell(data['id'],data.get('review_digest'),data.get('confirmed',False),app.config.get('car_baseline')))
                if path=='/api/calibrate-units':return self.send(200,app.store.calibrate_units(data['id']))
                if path=='/api/partition-wheel-group':return self.send(200,app.store.partition_wheel_group(data['id'],data['part_id'],data['role']))
                if path=='/api/discover-tyres':return self.send(200,app.store.discover_tyres(data['id'],data['part_id']))
                if path=='/api/apply-tyre-discovery':return self.send(200,app.store.apply_tyre_discovery(data['id'],data['part_id'],data.get('proposal_token'),data.get('confirmed_tyre_only',False)))
                if path=='/api/apply-known-recipe':
                    pid=data['id'];p=app.store.summary(pid);recipe=app.store.known_recipe(pid)
                    if not recipe:raise GarageError('There is no fingerprint-matched recipe for this source. Assign the lamps visually.')
                    # Explicit action; saved manual assignments win.
                    return self.send(200,app.store.update(pid,{'overrides':{**recipe,**p['overrides']},'approved_roles':False}))
                if path=='/api/car-plan':
                    pid=data['id'];return self.send(202,app.submit('Check complete-car build plan',lambda c:app.conversion_plan(pid)))
                if path=='/api/car-targets':return self.send(202,app.submit('Read available car slots',lambda c:app.car_targets(c)))
                if path=='/api/use-built-baseline':return self.send(200,app.use_built_baseline(data['job_id']))
                if path=='/api/use-included-baseline':return self.send(200,app.use_included_baseline())
                if path=='/api/build-car':
                    pid=data['id'];return self.send(202,app.submit('Convert and repack the complete exterior',lambda c:app.convert_car(pid,c)))
                if path=='/api/export-project':
                    pid=data['id'];return self.send(202,app.submit('Save editable project',lambda c:app.store.export(pid,app.path_out(pid,'.gt6car'))))
                if path=='/api/export-neutral':
                    pid=data['id'];return self.send(202,app.submit('Export neutral car data',lambda c:app.store.neutral(pid,app.path_out(pid,'.zip','_neutral'))))
                if path=='/api/export-rims':
                    raise GarageError('The player importer supplies the rims and tyres from GT6.', 'game_wheels_required')
                if path=='/api/inspect-package':
                    folder=app.config.get('package_folder')
                    if not folder:raise GarageError('Choose a built package folder first.')
                    return self.send(202,app.submit('Validate native package',lambda c:source_package(Path(folder))[1]))
                if path=='/api/export-package':
                    folder=app.config.get('package_folder')
                    if not folder:raise GarageError('Choose a built package folder first.')
                    return self.send(202,app.submit('Create checked FTP ZIP',lambda c:export_package(Path(folder),unique_path(app.exports,safe_slug(Path(folder).name)+'_FTP.zip'),c)))
                if path=='/api/open-exports':
                    if os.name=='nt':os.startfile(app.exports)
                    return self.send(200,{'path':str(app.exports)})
                return self.send(404,{'error':'Unknown action'})
            except GarageError as e:return self.send(403 if e.code=='unauthorized' else 400,{'error':str(e),'code':e.code})
            except (ValueError,KeyError,TypeError) as e:return self.send(400,{'error':'Invalid request. Check the selected project and inputs.'})
            except Exception:logging.exception('POST failed');return self.send(500,{'error':'Operation stopped. See the local diagnostic log.'})
    return Handler


def serve(app,port=0):
    server=http.server.ThreadingHTTPServer(('127.0.0.1',port),handler_type(app));server.daemon_threads=True;app.server=server
    return server

"""One explicitly requested private GT6 chassis measurement session; never a scheduler.

Every invocation checks the current project lease. Launches are one-shot, input
is brief and cooperative, and close/restore are bound to our recorded process.
"""
from pathlib import Path
import argparse, contextlib, ctypes, datetime, hashlib, io, json, os, shutil, subprocess, sys, uuid
from ctypes import wintypes as T

if not __debug__:
    raise RuntimeError('The private experiment harness requires Python validation checks enabled.')

bootstrap=argparse.ArgumentParser(add_help=False)
bootstrap.add_argument('--run',type=Path,required=True)
boot,remaining=bootstrap.parse_known_args()
sys.argv=[sys.argv[0],*remaining]
RUN=boot.run.resolve();HUB=RUN.parent.parent
SESSION=json.loads((RUN/'SESSION.json').read_text(encoding='utf-8-sig'))
CONFIG=json.loads((RUN/'chassis_runtime_config.json').read_text(encoding='utf-8-sig'))
CONFIG_SHA256=hashlib.sha256((RUN/'chassis_runtime_config.json').read_bytes()).hexdigest()
assert CONFIG_SHA256==SESSION['runtime_config_sha256'], 'Runtime configuration differs from its owned session pin'
assert RUN.parent==HUB/'autorun_runs'
assert Path(SESSION['source_candidate']).resolve()==RUN/'source'
TOKEN=SESSION['token'];SOURCE=RUN/'source'
CASE=Path(CONFIG['case_root']);ENGINE=CASE/'runtime/rpcs3.exe'
APPROVED=CONFIG['baseline']['header_sha256']
CURRENT=CONFIG['candidate']['header_sha256']
sys.path.insert(0, str(SOURCE))
from garagehub.packages import source_package, install_files

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def write(p, data):
    p=Path(p); tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8'); os.replace(tmp,p)
def lease():
    data=read(HUB/'AUTORUN_LEASE/owner.json')
    assert data['token']==TOKEN and data['owner']==SESSION['owner'], 'Another session owns this project.'
    assert datetime.datetime.now(datetime.timezone.utc)<datetime.datetime.fromisoformat(data['expires_utc'])
    assert not (HUB/'STOP_AUTORUN').exists(), 'Project stop marker is present.'
def hub_owned_state():
    """Read owned hub metadata without rewriting either supported owner format."""
    state=read(HUB/'AUTORUN_STATE.json')
    assert type(state) is dict, 'Project state ownership record is invalid.'
    worker=state.get('current_worker')
    if type(worker) is str:
        assert worker==SESSION['owner'], 'Project state ownership belongs to another worker.'
    else:
        assert type(worker) is dict and worker.get('owner')==SESSION['owner'], 'Project state ownership belongs to another worker.'
        run=worker.get('run')
        assert type(run) is str and Path(run).is_absolute() and Path(run)==RUN, 'Project state ownership belongs to another or unidentified run.'
    return state
def processes():
    p=subprocess.run(['powershell.exe','-NoProfile','-Command',
        "@(Get-CimInstance Win32_Process -Filter \"Name='rpcs3.exe'\" | Select-Object ProcessId,ExecutablePath)|ConvertTo-Json -Compress"],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    a=json.loads(p.stdout) if p.stdout.strip() else []
    return a if isinstance(a,list) else [a]
def stopped():
    assert not any(x.get('ExecutablePath') and Path(x['ExecutablePath'])==ENGINE for x in processes()), 'Private runtime is still running.'
def identity(pid):
    k=ctypes.windll.kernel32
    k.OpenProcess.argtypes=[T.DWORD,T.BOOL,T.DWORD]; k.OpenProcess.restype=T.HANDLE
    k.QueryFullProcessImageNameW.argtypes=[T.HANDLE,T.DWORD,T.LPWSTR,ctypes.POINTER(T.DWORD)]
    k.GetProcessTimes.argtypes=[T.HANDLE,*([ctypes.POINTER(T.FILETIME)]*4)]
    k.CloseHandle.argtypes=[T.HANDLE]
    handle=k.OpenProcess(0x1000,False,pid)
    if not handle: raise RuntimeError('Recorded process is no longer running.')
    try:
        text=ctypes.create_unicode_buffer(32768); n=T.DWORD(len(text))
        assert k.QueryFullProcessImageNameW(handle,0,text,ctypes.byref(n))
        times=[T.FILETIME() for _ in range(4)]
        assert k.GetProcessTimes(handle,*[ctypes.byref(t) for t in times])
        return {'pid':pid,'path':str(Path(text.value)), 'created_filetime':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime}
    finally:k.CloseHandle(handle)
def owned(state):
    ident=identity(state['process']['pid'])
    assert ident==state['process'] and Path(ident['path'])==ENGINE, 'Process ownership changed.'
    launch=read(CASE/'last_launch.json')
    assert launch['ProcessId']==ident['pid'] and Path(launch['Executable'])==ENGINE
    assert launch['HeaderSHA256']==state['test_header']
    return ident
def recover_launch(trial,state):
    """Recover only the one requested launch; never launch or relaunch here."""
    assert state.get('launch_requested') and state.get('launch_requested_utc')
    launched=read(CASE/'last_launch.json')
    assert Path(launched['Executable'])==ENGINE and launched['HeaderSHA256']==state['test_header']
    requested=datetime.datetime.fromisoformat(state['launch_requested_utc'])
    started=datetime.datetime.fromisoformat(launched['StartedUtc'].replace('Z','+00:00'))
    assert requested<=started<=requested+datetime.timedelta(seconds=60), 'Launch record is outside this one-shot request.'
    assert launched['ProcessId'] not in {row['ProcessId'] for row in state['other_processes']}
    ident=identity(launched['ProcessId'])
    assert Path(ident['path'])==ENGINE
    unix_created=(ident['created_filetime']-116444736000000000)/10000000
    assert abs(unix_created-started.timestamp())<30, 'Process creation does not match the requested launch.'
    state.update(process=ident,launch_count=1,started_utc=launched['StartedUtc'],launch_record_recovered=True)
    write(trial/'state.json',state)
    shutil.copy2(CASE/'last_launch.json',trial/'test_launch.json')
    owned(state)
    return ident
def home_manifest(root):
    check_private_path(root)
    result={}
    for p in sorted(root.rglob('*')):
        assert not p.is_symlink() and not (hasattr(p,'is_junction') and p.is_junction()), 'Save snapshot contains filesystem links.'
        if p.is_file():result[p.relative_to(root).as_posix()]=sha(p)
    return result
def check_private_path(path):
    from garagehub.chassis_probe import _check_components
    path=Path(path).absolute()
    _check_components(path)
    if path.is_file():assert path.stat().st_nlink==1, 'Private recovery files must not be hard links.'
def package_check():
    iso=read(CASE/'isolation.json'); active=read(CASE/'active_package.json')
    assert Path(iso['runtime'])==ENGINE.parent
    assert sha(ENGINE)==iso['executable_sha256']==CONFIG['engine_sha256']
    target=Path(iso['title'])/'USRDIR/PDIPFS'
    assert target==CASE/'title/BCES01893/USRDIR/PDIPFS'
    assert active['passed'] and active['source_roundtrip_match']
    variant=next((v for v in (CONFIG['baseline'],CONFIG['candidate']) if v['header_sha256']==active['header_sha256']),None)
    assert variant is not None and active['candidate_id']==variant['candidate_id'], 'Active package is outside this comparison'
    actual={f['relative_path']:{'sha256':f['sha256'],'bytes':f['bytes']} for f in active['files']}
    assert len(actual)==len(active['files']) and actual==variant['physical_files'], 'Exact physical payload manifest differs'
    for f in active['files']:
        rel=Path(f['relative_path']); assert not rel.is_absolute() and '..' not in rel.parts
        assert sha(target/rel)==f['sha256'], f['relative_path']
    assert sha(target/'K/4D')==active['header_sha256']
    return active,target
def event(trial,event_kind,**details):
    path=trial/'events.jsonl'
    ordinal=len(path.read_text(encoding='utf-8').splitlines())+1 if path.exists() else 1
    row={'utc':now(),'event_id':uuid.uuid4().hex,'ordinal':ordinal,'event_kind':event_kind,**details}
    with (trial/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
    return row
def verify_setup():
    from garagehub.chassis_probe_package import verify_chassis_probe_manifest
    path=Path(CONFIG['comparison_manifest'])
    verified=verify_chassis_probe_manifest(path, CONFIG['comparison_manifest_sha256'])
    assert sha(path)==CONFIG['comparison_manifest_sha256']
    manifest=read(path)
    assert CONFIG['identity']==verified['identity'], 'Configured experiment identity differs from verified package'
    for name in ('baseline','candidate'):
        configured=CONFIG[name]
        assert configured['header_sha256']==verified[name+'_header_sha256']
        assert configured['physical_files']==verified[name+'_physical_files']
        expected=Path(manifest['baseline']['path']) if name=='baseline' else path.parent
        assert Path(configured['package']).resolve()==expected.resolve(), 'Configured package path differs'
        assert configured['candidate_id']==read(expected/'package_receipt.json')['candidate_id']
    assert CONFIG_SHA256==sha(RUN/'chassis_runtime_config.json')
    for name,digest in CONFIG['configuration_and_helpers'].items():
        assert sha(Path(name))==digest, 'Configuration/helper changed: '+name
    return dict(CONFIG['configuration_and_helpers'])
def control_preflight():
    sys.path.insert(0,str(CASE/'input_control_v001'));import shared_test_control as ctl
    s=ctl.input_state()
    return {'takeover_latched':ctl.LATCH.exists(),'held_keys_present':bool(s['held_keys']),
            'controller_active':ctl.active(s),'idle_ms':s['idle_ms'],'input_sent':False}
def preflight():
    hub_owned_state();stopped();verify_setup();active,_=package_check()
    assert active['header_sha256']==APPROVED
    result=control_preflight()
    result.update(ready=not any(result[k] for k in ('takeover_latched','held_keys_present','controller_active')),
                  baseline_files=len(active['files']),candidate_files=len(CONFIG['candidate']['physical_files']),
                  configuration_verified=True,game_launched=False,foreground_verified=False,
                  foreground_requires_explicit_action=True)
    write(RUN/'runtime_preflight.json',result);print(json.dumps(result));return result
def launch(trial,stage):
    hub_owned_state();stopped(); verified_configuration=verify_setup()
    controls=control_preflight()
    assert not any(controls[k] for k in ('takeover_latched','held_keys_present','controller_active')), 'Cooperative input prerequisite is blocked'
    assert not trial.exists(), 'One-shot trial already exists; do not silently relaunch.'
    active,target=package_check(); assert active['header_sha256']==APPROVED
    home=CASE/'runtime/portable/dev_hdd0/home/00000001'
    original_saves=home_manifest(home)
    if stage=='candidate':
        baseline=read(RUN/'runtime_baseline/state.json')
        assert baseline.get('restored') and original_saves==baseline['save_start_hashes'], 'Comparison save state differs.'
        assert baseline['configuration_sha256']==verified_configuration, 'Comparison configuration differs.'
        assert baseline.get('sequence_completed'), 'Baseline did not complete the recorded sequence; do not add a candidate launch.'
        assert measurement_sequence_complete(RUN/'runtime_baseline',baseline), 'Baseline capture evidence no longer satisfies the measurement sequence.'
    trial.mkdir();(trial/'control').mkdir();shutil.copytree(home,trial/'private_save_before')
    assert home_manifest(trial/'private_save_before')==original_saves
    shutil.copy2(CASE/'active_package.json',trial/'active_package_before.json')
    if (CASE/'last_launch.json').exists():shutil.copy2(CASE/'last_launch.json',trial/'last_launch_before.json')
    shutil.copy2(CASE/'profiles/SharedControlPurchaseSafe.yml',trial/'runtime_profile.yml')
    state={'stage':stage,'created_utc':now(),'purpose':'Controlled one-field chassis measurement with identical native assets, car/setup and registered stationary views',
        'approved_header':APPROVED,'test_header':APPROVED,'save_start_hashes':original_saves,'target':str(target),
        'launch_requested':False,'launch_count':0,'automatic_relaunch':False,'installed':False,'restored':False,
        'other_processes':processes(),'no_runtime_acceptance_claim':True,
        'configuration_sha256':verified_configuration,'comparison_manifest_sha256':CONFIG['comparison_manifest_sha256'],
        'runtime_config_sha256':CONFIG_SHA256,
        'expected_car_id':CONFIG['identity']['car_id'],'expected_model_path':CONFIG['identity']['model_path'],
        'sequence_completed':False,'input_sent':False}
    write(trial/'state.json',state)
    if stage=='candidate':
        package=Path(CONFIG['candidate']['package'])
        root,manifest=source_package(package,allow_diagnostic=True); assert manifest['header_sha256']==CURRENT
        assert all(row['path'] in CONFIG['baseline']['physical_files'] or not (target/row['path']).exists() for row in manifest['files']), 'New diagnostic payload collides with an existing private file.'
        state.update(install_requested=True,install_phase='before_transaction',test_header=CURRENT,package=str(package))
        write(trial/'state.json',state)
        result=install_files(root,manifest,target,trial/'package_backup',expected_header=APPROVED)
        assert result['installed']
        state.update(installed=True,install_phase='payloads_verified');write(trial/'state.json',state)
        shutil.copy2(package/'package_receipt.json',CASE/'active_package.json')
        state.update(install_phase='active_receipt_written');write(trial/'state.json',state)
    hub_owned_state();lease();state.update(launch_requested=True,launch_requested_utc=now());write(trial/'state.json',state)
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(CASE/'Start-Case.ps1'),'-Profile','SharedControlPurchaseSafe'],capture_output=True,text=True,timeout=45)
    except subprocess.TimeoutExpired:
        state['launcher_timed_out']=True;write(trial/'state.json',state)
        recover_launch(trial,state)
        raise RuntimeError('Launcher timed out; exact owned process recovered. Inspect/close it without relaunching.')
    (trial/'launch.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    assert result.returncode==0,result.stderr
    launched=read(CASE/'last_launch.json');assert launched['HeaderSHA256']==state['test_header']
    state.update(process=identity(launched['ProcessId']),launch_count=1,started_utc=launched['StartedUtc'])
    write(trial/'state.json',state);shutil.copy2(CASE/'last_launch.json',trial/'test_launch.json');owned(state)
    hub_state=hub_owned_state()
    hub_state.update(no_game_started=False,actual_game_started_this_run=True,private_runtime_running=True,
        game_launch_count_this_run=hub_state.get('game_launch_count_this_run',0)+1,latest_runtime_attempt=str(trial/'state.json'),latest_runtime_attempt_is_historical=False)
    write(HUB/'AUTORUN_STATE.json',hub_state)
    print(json.dumps({'launched':True,'stage':stage,'process':state['process'],'header':state['test_header'],'files_verified':len(active['files']),'save_files_backed_up':len(original_saves)}))
def window_snapshot(ctl,state,hwnd):
    """Bind one visible game HWND and its client geometry to the owned process."""
    process=owned(state);u=ctl.u
    assert hwnd and u.IsWindowVisible(hwnd), 'The owned game window is not visible.'
    u.IsIconic.argtypes=[T.HWND];u.IsIconic.restype=T.BOOL
    assert not u.IsIconic(hwnd), 'The owned game window is minimized.'
    pid=T.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    assert pid.value==process['pid'], 'Window ownership differs from the owned process.'
    rect=T.RECT();outer=T.RECT();point=T.POINT()
    assert u.GetClientRect(hwnd,ctypes.byref(rect)) and u.ClientToScreen(hwnd,ctypes.byref(point))
    u.GetWindowRect.argtypes=[T.HWND,ctypes.POINTER(T.RECT)]
    assert u.GetWindowRect(hwnd,ctypes.byref(outer))
    width,height=rect.right-rect.left,rect.bottom-rect.top
    assert rect.left==0 and rect.top==0 and 1<=width<=4096 and 1<=height<=2160, 'Unexpected game client geometry.'
    title=ctl.title(hwnd);title_id=CONFIG['identity']['title_id']
    assert isinstance(title_id,str) and title_id and '['+title_id+']' in title, 'The selected window is not the configured game render window.'
    return {'hwnd':int(hwnd),'process':process,'title':title,
            'client_rect':[0,0,width,height],
            'client_screen_rect':[point.x,point.y,point.x+width,point.y+height],
            'window_screen_rect':[outer.left,outer.top,outer.right,outer.bottom],
            'foreground_hwnd':int(u.GetForegroundWindow() or 0)}
def same_window_geometry(before,after):
    # FPS in the render-window title changes naturally between samples.
    return all(before[key]==after[key] for key in ('hwnd','process','client_rect','client_screen_rect','window_screen_rect'))
def request_foreground(trial,state,ctl,hwnd,shared_latch):
    """One explicit ordinary activation request; never part of launch or input."""
    hub_owned_state();lease();before=window_snapshot(ctl,state,hwnd)
    first=ctl.input_state()
    result={'method':'SetForegroundWindow','request_made':False,'activated':False,
            'input_sent':False,'focus_forced':False,'process':state['process'],
            'hwnd':int(hwnd),'foreground_before':before['foreground_hwnd']}
    def finish(reason,**details):
        result.update(reason=reason,**details)
        row=event(trial,'foreground_request',**result);print(json.dumps(row));return row
    if shared_latch.exists() or ctl.LATCH.exists():return finish('user_takeover_latched')
    if first.get('held_keys') or ctl.active(first):return finish('physical_input_active')
    second=ctl.input_state();change=ctl.changed(first,second)
    if change or second.get('held_keys') or ctl.active(second):return finish('physical_input_changed')
    current=window_snapshot(ctl,state,hwnd)
    if not same_window_geometry(before,current) or current['foreground_hwnd']!=before['foreground_hwnd']:
        return finish('window_or_focus_changed_before_request')
    hub_owned_state();lease()
    if shared_latch.exists() or ctl.LATCH.exists():return finish('user_takeover_latched')
    if current['foreground_hwnd']==int(hwnd):return finish('already_foreground',activated=True,foreground_after=int(hwnd))
    # No AttachThreadInput, key synthesis, window-order manipulation, retries,
    # foreground-lock changes or global input. Windows may refuse this request.
    ctl.u.SetForegroundWindow.argtypes=[T.HWND];ctl.u.SetForegroundWindow.restype=T.BOOL
    result['request_made']=True;result['os_return']=bool(ctl.u.SetForegroundWindow(hwnd))
    after=window_snapshot(ctl,state,hwnd);last=ctl.input_state();change=ctl.changed(second,last)
    result['foreground_after']=after['foreground_hwnd']
    if change or last.get('held_keys') or ctl.active(last):
        ctl.take_over(change or 'physical_input_after_activation')
        return finish('physical_input_changed_after_request')
    if shared_latch.exists() or ctl.LATCH.exists():return finish('user_takeover_latched')
    if not same_window_geometry(current,after):return finish('window_changed_after_request')
    if after['foreground_hwnd']!=int(hwnd):return finish('ordinary_activation_refused')
    return finish('ordinary_activation_succeeded',activated=True)
def grab_client_image(box):
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=tuple(box),all_screens=True)
def grab_window_paint(ctl,hwnd,window_rect):
    """Non-measurable background diagnostic, retained in memory until checked."""
    from PIL import Image
    u=ctl.u;g=ctypes.windll.gdi32
    u.GetWindowDC.argtypes=[T.HWND];u.GetWindowDC.restype=T.HDC
    u.ReleaseDC.argtypes=[T.HWND,T.HDC]
    u.PrintWindow.argtypes=[T.HWND,T.HDC,T.UINT];u.PrintWindow.restype=T.BOOL
    g.CreateCompatibleDC.argtypes=[T.HDC];g.CreateCompatibleDC.restype=T.HDC
    g.CreateCompatibleBitmap.argtypes=[T.HDC,ctypes.c_int,ctypes.c_int];g.CreateCompatibleBitmap.restype=T.HBITMAP
    g.SelectObject.argtypes=[T.HDC,T.HGDIOBJ];g.SelectObject.restype=T.HGDIOBJ
    g.DeleteObject.argtypes=[T.HGDIOBJ];g.DeleteDC.argtypes=[T.HDC]
    g.GetDIBits.argtypes=[T.HDC,T.HBITMAP,T.UINT,T.UINT,ctypes.c_void_p,ctypes.c_void_p,T.UINT]
    class BitmapInfo(ctypes.Structure):
        _fields_=[('size',T.DWORD),('width',T.LONG),('height',T.LONG),('planes',T.WORD),('bits',T.WORD),
                  ('compression',T.DWORD),('imagebytes',T.DWORD),('xppm',T.LONG),('yppm',T.LONG),('used',T.DWORD),('important',T.DWORD)]
    left,top,right,bottom=window_rect
    width,height=right-left,bottom-top
    assert 1<=width<=4096 and 1<=height<=2160
    dc=u.GetWindowDC(hwnd);assert dc, 'Window diagnostic DC unavailable.'
    memory=bitmap=old=None
    try:
        memory=g.CreateCompatibleDC(dc);assert memory
        bitmap=g.CreateCompatibleBitmap(dc,width,height);assert bitmap
        old=g.SelectObject(memory,bitmap);assert old
        ok=bool(u.PrintWindow(hwnd,memory,2));g.SelectObject(memory,old);old=None
        info=BitmapInfo(ctypes.sizeof(BitmapInfo),width,-height,1,32,0,width*height*4,0,0,0,0)
        data=ctypes.create_string_buffer(width*height*4)
        assert g.GetDIBits(memory,bitmap,0,height,data,ctypes.byref(info),0)==height
        frame=Image.frombytes('RGB',(width,height),data.raw,'raw','BGRX')
        return frame,{'printwindow_return':ok,'window_rect':list(window_rect)}
    finally:
        if old:g.SelectObject(memory,old)
        if bitmap:g.DeleteObject(bitmap)
        if memory:g.DeleteDC(memory)
        u.ReleaseDC(hwnd,dc)
def save_capture_image(path,frame):
    """Exclusive publication; a failed encoder leaves no partial owned image."""
    check_private_path(path);created=False
    try:
        with path.open('xb') as output:
            created=True;frame.save(output,format='PNG')
    except Exception:
        if created:path.unlink()
        raise
def capture_owned_window(trial,state,args,ctl,hwnd):
    """Keep measurement images only when one owned foreground client is stable."""
    name=args.name
    assert isinstance(name,str) and name.isascii() and name.replace('_','').replace('-','').isalnum() and len(name)<50, 'Invalid capture name.'
    assert state['stage'] in ('baseline','candidate')
    check_private_path(trial);before=window_snapshot(ctl,state,hwnd)
    prefix=state['stage']+'_'+name[:32]+'_'+uuid.uuid4().hex[:12]
    def discarded(reason):
        row=event(trial,'capture_discarded',requested_step=name,reason=reason,
                  measurement_eligible=False,process=state['process'],hwnd=int(hwnd))
        print(json.dumps(row));return row
    if before['foreground_hwnd']==int(hwnd):
        # Capture to memory first. A moved window or focus change must not leave
        # an accidental desktop crop in the evidence directory.
        frame=grab_client_image(before['client_screen_rect'])
        after=window_snapshot(ctl,state,hwnd)
        if not same_window_geometry(before,after) or after['foreground_hwnd']!=int(hwnd):
            return discarded('window_geometry_or_focus_changed')
        expected=tuple(before['client_rect'][2:])
        if frame.size!=expected:return discarded('capture_dimensions_differ')
        path=trial/(prefix+'.png');save_capture_image(path,frame)
        result={'path':str(path),'capture_method':'foreground_client_pixels',
                'measurement_eligible':True,'captured':True,'image_size_pixels':list(frame.size),
                'image_scale':1.0,'client_rect':before['client_rect'],
                'client_screen_rect':before['client_screen_rect'],'hwnd':int(hwnd),
                'foreground_before':before['foreground_hwnd'],'foreground_after':after['foreground_hwnd'],
                'title_before':before['title'],'title_after':after['title']}
    else:
        frame,raw=grab_window_paint(ctl,hwnd,before['window_screen_rect'])
        after=window_snapshot(ctl,state,hwnd)
        if not same_window_geometry(before,after) or before['foreground_hwnd']!=after['foreground_hwnd']:
            return discarded('diagnostic_window_geometry_or_focus_changed')
        if not raw['printwindow_return']:return discarded('window_diagnostic_api_refused')
        path=trial/(prefix+'.png');save_capture_image(path,frame)
        result={**raw,'path':str(path),'capture_method':'background_printwindow_diagnostic',
                'measurement_eligible':False,'captured':True,'hwnd':int(hwnd),
                'image_size_pixels':list(frame.size),'image_scale':1.0,
                'foreground_before':before['foreground_hwnd'],'foreground_after':after['foreground_hwnd'],
                'client_rect':before['client_rect'],'client_screen_rect':before['client_screen_rect'],
                'scope':'Diagnostic window painting only; not a presented game frame or wheel measurement.'}
    result.update(sha256=sha(path),requested_step=name,observed_state=None,
        log_offset_bytes=(CASE/'runtime/portable/log/RPCS3.log').stat().st_size if (CASE/'runtime/portable/log/RPCS3.log').exists() else None,
        process=state['process'],package_header_sha256=state['test_header'])
    row=event(trial,'capture',**result);print(json.dumps(row));return row
def control(trial,state,args):
    owned(state);verify_setup();sys.path.insert(0,str(CASE/'input_control_v001'));import shared_test_control as ctl
    # A fresh run has its own evidence folder, while an existing physical-user
    # takeover latch on the shared harness is also respected for input.
    shared_latch=ctl.LATCH
    ctl.ROOT=trial/'control';ctl.LATCH=ctl.ROOT/'USER_CONTROL_ACTIVE.json'
    pid,hwnd,_=ctl.target()
    assert pid==state['process']['pid'] and identity(pid)==state['process'], 'Control target differs from the owned process'
    if args.action=='status':
        s=ctl.input_state();print(json.dumps({'process':state['process'],'foreground':ctl.u.GetForegroundWindow()==hwnd,'title':ctl.title(hwnd),
            'user_takeover':ctl.LATCH.exists() or shared_latch.exists(),'controller_active':ctl.active(s),'held_keys_present':bool(s['held_keys']),'idle_ms':s['idle_ms']}));return
    if args.action=='foreground':return request_foreground(trial,state,ctl,hwnd,shared_latch)
    if args.action=='capture':return capture_owned_window(trial,state,args,ctl,hwnd)
    assert args.action=='tap'
    if shared_latch.exists():print(json.dumps({'input_sent':False,'reason':'Shared user takeover remains active.'}));return
    sys.argv=['shared_test_control.py','tap','--keys',args.keys,'--ms',str(args.ms)]
    with contextlib.redirect_stdout(io.StringIO()) as output:ctl.main()
    result_text=output.getvalue().strip()
    event(trial,'control_result',requested=args.keys,output=result_text)
    try:actual=json.loads(result_text)
    except ValueError:actual={}
    if actual.get('sent'):
        state['input_sent']=True;write(trial/'state.json',state)
        hub_state=hub_owned_state()
        hub_state['runtime_input_sent']=True;write(HUB/'AUTORUN_STATE.json',hub_state)
    print(result_text)
def capture_binding(trial,state,capture):
    path=Path(capture['path']);check_private_path(path)
    assert path.parent==trial and path.suffix=='.png', 'Capture is outside this trial.'
    assert capture['process']==state['process'] and capture['package_header_sha256']==state['test_header'], 'Capture belongs to another process or package.'
    assert sha(path)==capture['sha256'], 'Capture image changed.'
    return path
def measurable_capture(trial,state,capture):
    capture_binding(trial,state,capture)
    rect=capture.get('client_rect');screen=capture.get('client_screen_rect')
    if (not isinstance(rect,list) or len(rect)!=4 or any(type(x) is not int for x in rect)
            or rect[:2]!=[0,0] or not 1<=rect[2]<=4096 or not 1<=rect[3]<=2160
            or not isinstance(screen,list) or len(screen)!=4 or any(type(x) is not int for x in screen)
            or [screen[2]-screen[0],screen[3]-screen[1]]!=rect[2:]):return False
    from PIL import Image
    with Image.open(capture['path']) as frame:
        if frame.format!='PNG' or list(frame.size)!=rect[2:]:return False
    return (capture.get('measurement_eligible') is True
            and capture.get('captured') is True
            and capture.get('capture_method')=='foreground_client_pixels'
            and capture.get('foreground_before')==capture.get('hwnd')==capture.get('foreground_after')
            and type(capture.get('hwnd')) is int and capture['hwnd']>0
            and type(capture.get('image_scale')) in (int,float) and capture['image_scale']==1.0
            and rect[2:]==capture.get('image_size_pixels'))
def measurement_sequence_complete(trial,state,rows=None):
    if rows is None:
        path=trial/'events.jsonl';check_private_path(path)
        if not path.is_file():return False
        rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    observations=[row for row in rows if row.get('event_kind')=='visual_observation']
    assert len({row['capture_event_id'] for row in observations})==len(observations)
    observations.sort(key=lambda row:row['capture_ordinal'])
    needed=['garage_reference','stationary_side','stationary_side_repeat','stationary_three_quarter']
    selected=[row for row in observations if row['observed_state'] in needed]
    if [row['observed_state'] for row in selected]!=needed:return False
    captures=[row for row in rows if row.get('event_kind')=='capture']
    assert len({row['event_id'] for row in captures})==len(captures), 'Capture event identities are ambiguous.'
    by_id={row['event_id']:row for row in captures}
    for row in selected:
        capture=by_id.get(row['capture_event_id'])
        if (not capture or row['body_visible']!='yes' or row['rims_visible']!='yes'
                or row.get('setup_confirmed')!='yes' or not row.get('camera_key') or not row.get('note')
                or row.get('measurement_eligible') is not True
                or capture['ordinal']!=row['capture_ordinal']
                or capture['sha256']!=row['capture_sha256']
                or not measurable_capture(trial,state,capture)):return False
    return selected[1]['camera_key']==selected[2]['camera_key']
def observe_capture(trial,state,args):
    assert args.capture_sha256 and len(args.capture_sha256)==64
    rows=[json.loads(line) for line in (trial/'events.jsonl').read_text().splitlines()]
    matches=[row for row in rows if row.get('event_kind')=='capture' and row.get('event_id')==args.capture_event]
    assert len(matches)==1, 'Observation needs one exact reviewed capture'
    capture=matches[0];capture_binding(trial,state,capture)
    assert capture['sha256']==args.capture_sha256
    assert not any(row.get('event_kind')=='visual_observation' and row.get('capture_event_id')==args.capture_event for row in rows), 'Capture already has an observation'
    assert args.observed_state, 'Supply the state actually visible in the reviewed image'
    needed=['garage_reference','stationary_side','stationary_side_repeat','stationary_three_quarter']
    eligible=measurable_capture(trial,state,capture)
    if args.observed_state in needed:assert eligible, 'Chassis reference views require an eligible owned foreground capture.'
    details=dict(capture_event_id=capture['event_id'],capture_ordinal=capture['ordinal'],capture_sha256=args.capture_sha256,requested_step=capture['requested_step'],
        observed_state=args.observed_state,body_visible=args.body_visible,rims_visible=args.rims_visible,
        note=args.note,log_offset_bytes=capture['log_offset_bytes'],review_basis='Inspection of the exact saved image',
        setup_confirmed=args.setup_confirmed,camera_key=args.camera_key,measurement_eligible=eligible)
    complete=measurement_sequence_complete(trial,state,rows+[{'event_kind':'visual_observation',**details}])
    event(trial,'visual_observation',**details)
    state['sequence_completed']=complete
    state['runtime_relationship_established']=False
    # This sequence only authorizes the bounded changed comparison. Registered
    # wheel/body measurements and repeated-view error are separate evidence.
    write(trial/'state.json',state)
    if args.observed_state in needed:
        hub_state=hub_owned_state()
        hub_state['runtime_chassis_views_completed']=bool(state['sequence_completed'])
        hub_state['runtime_chassis_relationship_established']=False
        write(HUB/'AUTORUN_STATE.json',hub_state)
    print(json.dumps({'observation_recorded':True,'observed_state':args.observed_state,'sequence_completed':state['sequence_completed']}))
def close(trial,state):
    owned(state)
    p=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(CASE/'helpers/Close-Test.ps1'),'-TargetProcessId',str(state['process']['pid'])],capture_output=True,text=True,timeout=25)
    (trial/'close.log').write_text(p.stdout+'\n'+p.stderr,encoding='utf-8');assert p.returncode==0,p.stderr;stopped()
    state.update(closed=True,closed_utc=now());write(trial/'state.json',state)
    hub_state=hub_owned_state()
    hub_state.update(private_runtime_running=False,private_runtime_closed_this_run=True);write(HUB/'AUTORUN_STATE.json',hub_state)
    log=CASE/'runtime/portable/log/RPCS3.log'
    assert log.is_file(), 'Closed test log is missing from the verified portable log directory.'
    shutil.copy2(log,trial/'runtime.log')
    state.update(runtime_log_sha256=sha(trial/'runtime.log'),runtime_log_bytes=log.stat().st_size,runtime_log_source=str(log));write(trial/'state.json',state)
    event(trial,'closed_owned_runtime',process=state['process']);print(json.dumps({'closed':True,'stage':state['stage'],'log_sha256':state.get('runtime_log_sha256')}))
def restore(trial,state):
    stopped();assert not state.get('restored'), 'Already restored.'
    iso=read(CASE/'isolation.json')
    assert Path(iso['runtime'])==ENGINE.parent and sha(ENGINE)==iso['executable_sha256']==CONFIG['engine_sha256']
    target=Path(iso['title'])/'USRDIR/PDIPFS'
    assert target==CASE/'title/BCES01893/USRDIR/PDIPFS' and str(target)==state['target']
    for path in (target,trial,trial/'package_backup/journal.json',trial/'active_package_before.json',CASE/'active_package.json'):
        check_private_path(path)
    # Consult the transaction before active_package.json: an interruption can
    # occur after payload installation but before that metadata/state write.
    if state.get('install_requested') and not state.get('package_restored') and (trial/'package_backup/journal.json').is_file():
        journal=read(trial/'package_backup/journal.json')
        assert Path(journal['target'])==target and journal['expected_header']==APPROVED
        allowed=CONFIG['candidate']['physical_files'];baseline_files=CONFIG['baseline']['physical_files']
        entries=journal['entries'];assert len({entry['path'] for entry in entries})==len(entries)
        assert {entry['path'] for entry in entries}==set(allowed), 'The complete private transaction journal is required.'
        for entry in entries:
            rel=entry['path'];assert rel in allowed
            if entry['existed']:
                assert rel in baseline_files and entry['sha256']==baseline_files[rel]['sha256']
                assert sha(trial/'package_backup/before'/rel)==entry['sha256']
            else:assert rel not in baseline_files and entry['sha256'] is None
            current_path=target/rel
            check_private_path(current_path);check_private_path(trial/'package_backup/before'/rel)
            if current_path.exists():
                acceptable={allowed[rel]['sha256']}
                if entry['existed']:acceptable.add(entry['sha256'])
                assert sha(current_path) in acceptable, 'A private target was changed by another writer; automatic restoration stopped.'
        for entry in sorted(journal['entries'],key=lambda x:(x['path']=='K/4D',x['path'])):
            rel=Path(entry['path']);assert not rel.is_absolute() and '..' not in rel.parts
            dst=target/rel;old=trial/'package_backup/before'/rel
            check_private_path(dst);check_private_path(old)
            if entry['existed']:
                assert sha(old)==entry['sha256'];tmp=dst.with_name(dst.name+'.comparison-restore');assert not tmp.exists()
                shutil.copyfile(old,tmp);os.replace(tmp,dst);assert sha(dst)==entry['sha256']
            elif dst.exists():dst.unlink()
        shutil.copy2(trial/'active_package_before.json',CASE/'active_package.json')
    active,_=package_check();assert active['header_sha256']==APPROVED
    state.update(package_restored=True,package_restored_utc=now(),baseline_files_verified=len(active['files']))
    write(trial/'state.json',state)
    home=CASE/'runtime/portable/dev_hdd0/home/00000001'
    check_private_path(home)
    if (trial/'control/USER_CONTROL_ACTIVE.json').exists() or (CASE/'input_control_v001/USER_CONTROL_ACTIVE.json').exists():
        state.update(save_restore_pending=True,current_user_progress_preserved=True);write(trial/'state.json',state)
        raise RuntimeError('Physical user takeover was observed; save backup retained and current progress not replaced.')
    assert home_manifest(trial/'private_save_before')==state['save_start_hashes']
    ready=trial/'private_save_restore_ready'
    check_private_path(ready);check_private_path(trial/'private_save_after')
    if not ready.exists():shutil.copytree(trial/'private_save_before',ready)
    assert home_manifest(ready)==state['save_start_hashes']
    assert not (trial/'private_save_after').exists(), 'Save transition already started; reconcile its recorded phase.'
    os.replace(home,trial/'private_save_after')
    try:os.replace(ready,home)
    except Exception:
        if not home.exists():os.replace(trial/'private_save_after',home)
        raise
    assert home_manifest(home)==state['save_start_hashes']
    state.update(restored=True,restored_utc=now(),private_save_restored=True,baseline_files_verified=len(active['files']))
    write(trial/'state.json',state);event(trial,'restored_approved_package_and_save',files=len(active['files']));print(json.dumps({'restored':True,'stage':state['stage'],'files':len(active['files']),'save_files':len(state['save_start_hashes'])}))
def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['baseline','candidate']);parser.add_argument('action',choices=['preflight','launch','recover-launch','status','foreground','capture','observe','tap','close','restore']);parser.add_argument('--name',default='review');parser.add_argument('--keys',default='');parser.add_argument('--ms',type=int,default=90)
    parser.add_argument('--capture-event');parser.add_argument('--capture-sha256');parser.add_argument('--observed-state',choices=['my_home','garage','loading','garage_reference','stationary_side','stationary_side_repeat','stationary_three_quarter','other'])
    parser.add_argument('--body-visible',choices=['yes','no','unknown'],default='unknown')
    parser.add_argument('--rims-visible',choices=['yes','no','unknown'],default='unknown');parser.add_argument('--note',default='')
    parser.add_argument('--setup-confirmed',choices=['yes','no','unknown'],default='unknown')
    parser.add_argument('--camera-key',default='')
    args=parser.parse_args();lease();trial=RUN/('runtime_'+args.stage)
    if args.action=='preflight':return preflight()
    if args.action=='launch':return launch(trial,args.stage)
    state=read(trial/'state.json')
    assert state['runtime_config_sha256']==CONFIG_SHA256, 'Trial configuration pin differs'
    if args.action=='recover-launch':print(json.dumps({'recovered':recover_launch(trial,state),'relaunch':False}));return
    if args.action=='observe':return observe_capture(trial,state,args)
    if args.action=='close':return close(trial,state)
    if args.action=='restore':return restore(trial,state)
    return control(trial,state,args)
if __name__=='__main__':main()


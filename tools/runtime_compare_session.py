"""One explicitly requested private GT6 comparison session; never a scheduler.

Every invocation checks the current project lease. Launches are one-shot, input
is brief and cooperative, and close/restore are bound to our recorded process.
"""
from pathlib import Path
import argparse, contextlib, ctypes, datetime, hashlib, io, json, os, shutil, subprocess, sys, uuid
from ctypes import wintypes as T

bootstrap=argparse.ArgumentParser(add_help=False)
bootstrap.add_argument('--run',type=Path,required=True)
boot,remaining=bootstrap.parse_known_args()
sys.argv=[sys.argv[0],*remaining]
RUN=boot.run.resolve();HUB=RUN.parent.parent
SESSION=json.loads((RUN/'SESSION.json').read_text(encoding='utf-8-sig'))
CONFIG=json.loads((RUN/'runtime_comparison_config.json').read_text(encoding='utf-8-sig'))
CONFIG_SHA256=hashlib.sha256((RUN/'runtime_comparison_config.json').read_bytes()).hexdigest()
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
def home_manifest(root):
    result={}
    for p in sorted(root.rglob('*')):
        assert not p.is_symlink() and not (hasattr(p,'is_junction') and p.is_junction()), 'Save snapshot contains filesystem links.'
        if p.is_file():result[p.relative_to(root).as_posix()]=sha(p)
    return result
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
    from garagehub.runtime_comparison import verify_comparison_manifest
    path=Path(CONFIG['comparison_manifest'])
    verified=verify_comparison_manifest(path, CONFIG['comparison_manifest_sha256'])
    assert sha(path)==CONFIG['comparison_manifest_sha256']
    manifest=read(path)
    assert CONFIG['identity']==verified['identity'], 'Configured experiment identity differs from verified package'
    for name in ('baseline','candidate'):
        configured=CONFIG[name]
        assert configured['header_sha256']==verified[name+'_header_sha256']
        assert configured['physical_files']==verified[name+'_physical_files']
        expected=Path(manifest['inputs']['input_paths']['baseline']) if name=='baseline' else path.parent
        assert Path(configured['package']).resolve()==expected.resolve(), 'Configured package path differs'
        assert configured['candidate_id']==read(expected/'package_receipt.json')['candidate_id']
    assert CONFIG_SHA256==sha(RUN/'runtime_comparison_config.json')
    for name,digest in CONFIG['configuration_and_helpers'].items():
        assert sha(Path(name))==digest, 'Configuration/helper changed: '+name
    return dict(CONFIG['configuration_and_helpers'])
def control_preflight():
    sys.path.insert(0,str(CASE/'input_control_v001'));import shared_test_control as ctl
    s=ctl.input_state()
    return {'takeover_latched':ctl.LATCH.exists(),'held_keys_present':bool(s['held_keys']),
            'controller_active':ctl.active(s),'idle_ms':s['idle_ms'],'input_sent':False}
def preflight():
    stopped();verify_setup();active,_=package_check()
    assert active['header_sha256']==APPROVED
    result=control_preflight()
    result.update(ready=not any(result[k] for k in ('takeover_latched','held_keys_present','controller_active')),
                  baseline_files=len(active['files']),candidate_files=len(CONFIG['candidate']['physical_files']),
                  configuration_verified=True,game_launched=False)
    write(RUN/'runtime_preflight.json',result);print(json.dumps(result));return result
def launch(trial,stage):
    stopped(); verified_configuration=verify_setup()
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
    trial.mkdir();(trial/'control').mkdir();shutil.copytree(home,trial/'private_save_before')
    assert home_manifest(trial/'private_save_before')==original_saves
    shutil.copy2(CASE/'active_package.json',trial/'active_package_before.json')
    if (CASE/'last_launch.json').exists():shutil.copy2(CASE/'last_launch.json',trial/'last_launch_before.json')
    shutil.copy2(CASE/'profiles/SharedControlPurchaseSafe.yml',trial/'runtime_profile.yml')
    state={'stage':stage,'created_utc':now(),'purpose':'Matched first spawn, replay, return, restart and leave/re-enter visibility comparison',
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
        root,manifest=source_package(package); assert manifest['header_sha256']==CURRENT
        result=install_files(root,manifest,target,trial/'package_backup',expected_header=APPROVED)
        assert result['installed'];shutil.copy2(package/'package_receipt.json',CASE/'active_package.json')
        state.update(installed=True,test_header=CURRENT,package=str(package));write(trial/'state.json',state)
    lease();state['launch_requested']=True;write(trial/'state.json',state)
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(CASE/'Start-Case.ps1'),'-Profile','SharedControlPurchaseSafe'],capture_output=True,text=True,timeout=45)
    (trial/'launch.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    assert result.returncode==0,result.stderr
    launched=read(CASE/'last_launch.json');assert launched['HeaderSHA256']==state['test_header']
    state.update(process=identity(launched['ProcessId']),launch_count=1,started_utc=launched['StartedUtc'])
    write(trial/'state.json',state);shutil.copy2(CASE/'last_launch.json',trial/'test_launch.json');owned(state)
    hub_state=read(HUB/'AUTORUN_STATE.json');assert hub_state['current_worker']==SESSION['owner']
    hub_state.update(no_game_started=False,actual_game_started_this_run=True,private_runtime_running=True,
        game_launch_count_this_run=hub_state.get('game_launch_count_this_run',0)+1,latest_runtime_attempt=str(trial/'state.json'),latest_runtime_attempt_is_historical=False)
    write(HUB/'AUTORUN_STATE.json',hub_state)
    print(json.dumps({'launched':True,'stage':stage,'process':state['process'],'header':state['test_header'],'files_verified':len(active['files']),'save_files_backed_up':len(original_saves)}))
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
    if args.action=='capture':
        assert args.name.replace('_','').replace('-','').isalnum() and len(args.name)<50
        import window_only_probe
        with contextlib.redirect_stdout(io.StringIO()):result=window_only_probe.capture(args.stage+'_'+args.name)
        owned(state);p=Path(result['path']);copy=trial/p.name;shutil.copy2(p,copy)
        result.update(path=str(copy),sha256=sha(copy),requested_step=args.name,observed_state=None,
            log_offset_bytes=(CASE/'runtime/portable/log/RPCS3.log').stat().st_size if (CASE/'runtime/portable/log/RPCS3.log').exists() else None,
            process=state['process'],package_header_sha256=state['test_header'])
        row=event(trial,'capture',**result);print(json.dumps(row));return
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
        hub_state=read(HUB/'AUTORUN_STATE.json');assert hub_state['current_worker']==SESSION['owner']
        hub_state['runtime_input_sent']=True;write(HUB/'AUTORUN_STATE.json',hub_state)
    print(result_text)
def observe_capture(trial,state,args):
    assert args.capture_sha256 and len(args.capture_sha256)==64
    rows=[json.loads(line) for line in (trial/'events.jsonl').read_text().splitlines()]
    matches=[row for row in rows if row.get('event_kind')=='capture' and row.get('event_id')==args.capture_event]
    assert len(matches)==1, 'Observation needs one exact reviewed capture'
    capture=matches[0];assert sha(Path(capture['path']))==args.capture_sha256
    assert capture['sha256']==args.capture_sha256
    assert not any(row.get('event_kind')=='visual_observation' and row.get('capture_event_id')==args.capture_event for row in rows), 'Capture already has an observation'
    assert args.observed_state, 'Supply the state actually visible in the reviewed image'
    observation=event(trial,'visual_observation',capture_event_id=capture['event_id'],capture_ordinal=capture['ordinal'],capture_sha256=args.capture_sha256,requested_step=capture['requested_step'],
        observed_state=args.observed_state,body_visible=args.body_visible,rims_visible=args.rims_visible,
        note=args.note,log_offset_bytes=capture['log_offset_bytes'],review_basis='Inspection of the exact saved image')
    observations=[row for row in rows if row.get('event_kind')=='visual_observation']
    observations.append(observation)
    assert len({row['capture_event_id'] for row in observations})==len(observations)
    observations.sort(key=lambda row:row['capture_ordinal'])
    needed=['free_run_spawn','replay','free_run_return','restart_spawn','event_reentry']
    reached=[row['observed_state'] for row in observations if row['observed_state'] in needed]
    state['sequence_completed']=reached==needed and all(row['body_visible']=='yes' and row['rims_visible']=='yes' for row in observations if row['observed_state'] in needed)
    write(trial/'state.json',state)
    if args.observed_state in needed:
        hub_state=read(HUB/'AUTORUN_STATE.json');assert hub_state['current_worker']==SESSION['owner']
        hub_state['runtime_track_entered']=True
        hub_state['runtime_repeated_load_comparison_completed']=bool(state['stage']=='candidate' and state['sequence_completed'])
        write(HUB/'AUTORUN_STATE.json',hub_state)
    print(json.dumps({'observation_recorded':True,'observed_state':args.observed_state,'sequence_completed':state['sequence_completed']}))
def close(trial,state):
    owned(state)
    p=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(CASE/'helpers/Close-Test.ps1'),'-TargetProcessId',str(state['process']['pid'])],capture_output=True,text=True,timeout=25)
    (trial/'close.log').write_text(p.stdout+'\n'+p.stderr,encoding='utf-8');assert p.returncode==0,p.stderr;stopped()
    state.update(closed=True,closed_utc=now());write(trial/'state.json',state)
    hub_state=read(HUB/'AUTORUN_STATE.json');assert hub_state['current_worker']==SESSION['owner']
    hub_state.update(private_runtime_running=False,private_runtime_closed_this_run=True);write(HUB/'AUTORUN_STATE.json',hub_state)
    log=CASE/'runtime/portable/log/RPCS3.log'
    assert log.is_file(), 'Closed test log is missing from the verified portable log directory.'
    shutil.copy2(log,trial/'runtime.log')
    state.update(runtime_log_sha256=sha(trial/'runtime.log'),runtime_log_bytes=log.stat().st_size,runtime_log_source=str(log));write(trial/'state.json',state)
    event(trial,'closed_owned_runtime',process=state['process']);print(json.dumps({'closed':True,'stage':state['stage'],'log_sha256':state.get('runtime_log_sha256')}))
def restore(trial,state):
    stopped();assert not state.get('restored'), 'Already restored.'
    current,target=package_check();assert current['header_sha256']==(APPROVED if state.get('package_restored') else state['test_header'])
    if state['installed'] and not state.get('package_restored'):
        journal=read(trial/'package_backup/journal.json')
        for entry in sorted(journal['entries'],key=lambda x:(x['path']=='K/4D',x['path'])):
            rel=Path(entry['path']);assert not rel.is_absolute() and '..' not in rel.parts
            dst=target/rel;old=trial/'package_backup/before'/rel
            if entry['existed']:
                assert sha(old)==entry['sha256'];tmp=dst.with_name(dst.name+'.comparison-restore');assert not tmp.exists()
                shutil.copyfile(old,tmp);os.replace(tmp,dst);assert sha(dst)==entry['sha256']
            elif dst.exists():dst.unlink()
        shutil.copy2(trial/'active_package_before.json',CASE/'active_package.json')
    active,_=package_check();assert active['header_sha256']==APPROVED
    state.update(package_restored=True,package_restored_utc=now(),baseline_files_verified=len(active['files']))
    write(trial/'state.json',state)
    home=CASE/'runtime/portable/dev_hdd0/home/00000001'
    if (trial/'control/USER_CONTROL_ACTIVE.json').exists() or (CASE/'input_control_v001/USER_CONTROL_ACTIVE.json').exists():
        state.update(save_restore_pending=True,current_user_progress_preserved=True);write(trial/'state.json',state)
        raise RuntimeError('Physical user takeover was observed; save backup retained and current progress not replaced.')
    assert home_manifest(trial/'private_save_before')==state['save_start_hashes']
    ready=trial/'private_save_restore_ready'
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
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['baseline','candidate']);parser.add_argument('action',choices=['preflight','launch','status','capture','observe','tap','close','restore']);parser.add_argument('--name',default='review');parser.add_argument('--keys',default='');parser.add_argument('--ms',type=int,default=90)
    parser.add_argument('--capture-event');parser.add_argument('--capture-sha256');parser.add_argument('--observed-state',choices=['my_home','garage','loading','free_run_spawn','replay','free_run_return','restart_spawn','event_reentry','other'])
    parser.add_argument('--body-visible',choices=['yes','no','unknown'],default='unknown')
    parser.add_argument('--rims-visible',choices=['yes','no','unknown'],default='unknown');parser.add_argument('--note',default='')
    args=parser.parse_args();lease();trial=RUN/('runtime_'+args.stage)
    if args.action=='preflight':return preflight()
    if args.action=='launch':return launch(trial,args.stage)
    state=read(trial/'state.json')
    assert state['runtime_config_sha256']==CONFIG_SHA256, 'Trial configuration pin differs'
    if args.action=='observe':return observe_capture(trial,state,args)
    if args.action=='close':return close(trial,state)
    if args.action=='restore':return restore(trial,state)
    return control(trial,state,args)
if __name__=='__main__':main()


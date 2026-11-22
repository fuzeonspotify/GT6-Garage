"""Shared local desktop workshop used by the player and development builds."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
import webbrowser

from . import __version__
from .server import App, serve
from .util import atomic_json

def desktop_parser():
    parser=argparse.ArgumentParser(description='GT6 Garage offline workshop')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-dialogs', action='store_true')
    parser.add_argument('--no-idle-exit', action='store_true')
    parser.add_argument('--model-tester', action='store_true')
    parser.add_argument('--test-files',nargs='+',type=Path,metavar='MODEL',help='Test model files and save each result in the local model-tests folder.')
    parser.add_argument('--blender-check',type=Path,metavar='JOB',help='Check the Blender add-on export in its temporary folder.')
    parser.add_argument('--prepare-game',type=Path,metavar='FOLDER',help='Prepare authoring files from a stock BCES01893 1.22 update.')
    parser.add_argument('--template',type=Path,metavar='FOLDER',help='Your prepared v029 folder or a previous Garage installation.')
    parser.add_argument('--dialog', choices=('model','test-models','game-data','profile-folder','template','package','car-baseline','car-packer'))
    parser.add_argument('--dialog-result')
    return parser

def dialog(kind,result):
    import tkinter as tk
    from tkinter import filedialog
    root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
    if kind=='test-models':
        paths=filedialog.askopenfilenames(title='Choose your car models',filetypes=[('3D car models','*.glb *.gltf *.obj *.zip *.fbx *.blend'),('All files','*')])
        atomic_json(Path(result),{'paths':list(paths)});root.destroy();return
    if kind=='game-data':
        from .stock_setup import find_update
        from .errors import GarageError
        directory=filedialog.askdirectory(title='Choose your stock GT6 1.22 PDIPFS folder')
        values={}
        if directory:
            try:
                values={'path':str(find_update(directory))}
            except GarageError as exc:values={'error':str(exc),'code':exc.code}
        atomic_json(Path(result),values);root.destroy();return
    if kind=='profile-folder':p=filedialog.askdirectory(title='Choose the prepared template, previous installation, or stock workspace')
    elif kind in ('package','car-baseline'):p=filedialog.askdirectory(title='Select the built GT6 package (contains package_receipt.json and PatchOut)')
    elif kind=='car-packer':p=filedialog.askopenfilename(title='Select the reviewed append-only GTToolsSharp.exe',filetypes=[('Windows executable','*.exe')])
    elif kind=='template':p=filedialog.askopenfilename(title='Choose the owned Supra race/wheel template',filetypes=[('Native wheel asset','*')])
    else:p=filedialog.askopenfilename(title='Open a car model or project',filetypes=[('Car models and projects','*.glb *.obj *.gltf *.fbx *.blend *.zip *.gt6car'),('GLB 2.0','*.glb'),('OBJ or glTF','*.obj *.gltf'),('Blender or FBX','*.blend *.fbx'),('Model with textures','*.zip'),('GT6 Garage project','*.gt6car')])
    atomic_json(Path(result),{'path':p});root.destroy()


def open_workshop(a):
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))
    default_profile=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'GT6Garage_ModelTests'
    if getattr(a,'blender_check',None):
        from .blender_preflight import run_check
        run_check(a.blender_check, a.workspace or default_profile, base/'web')
        return 0
    profile='GT6Garage_ModelTests' if a.model_tester or a.test_files or a.prepare_game else 'GT6Garage_v032'
    workspace=a.workspace or Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/profile
    workspace.mkdir(parents=True,exist_ok=True);logging.basicConfig(filename=str(workspace/'diagnostics.log'),encoding='utf-8',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    app=App(workspace,base/'web',allow_dialogs=not a.no_dialogs)
    installation=Path(sys.executable).parent if getattr(sys,'frozen',False) else base
    app.detect_companions(installation)
    if getattr(sys,'frozen',False) and workspace==default_profile and not a.test_files and not a.prepare_game:
        try:
            atomic_json(workspace/'installation.json',{'application':str(Path(sys.executable).resolve()),'version':__version__})
        except OSError:
            logging.warning('Could not register the Garage application for Blender. Select its path in the Blender panel.')
    if a.test_files or a.prepare_game:
        from .errors import GarageError
        try:
            if a.prepare_game:app.prepare_from_game(a.prepare_game,a.template,None)
            if a.test_files:app.model_tests.run_paths(a.test_files,None)
        except (GarageError,OSError) as exc:
            logging.error('%s',exc)
            return 1
        finally:
            app.executor.shutdown(wait=True,cancel_futures=True)
        return 0
    server=serve(app,a.port);entry='tester.html' if getattr(a,'model_tester',False) else '';url=f'http://127.0.0.1:{server.server_port}/{entry}#'+app.token
    atomic_json(workspace/'session.json',{'url':url,'pid':os.getpid(),'port':server.server_port,'version':__version__})
    if sys.stdout is not None:print('GT6 Garage: '+url,flush=True)
    if not a.no_browser:webbrowser.open(url,new=1)
    def monitor():
        while True:
            time.sleep(10)
            busy=app.busy and app.jobs[app.busy]['state'] in ('queued','running')
            if not busy and time.monotonic()-app.last_seen>180:
                app.close();break
    if not a.no_idle_exit:threading.Thread(target=monitor,daemon=True).start()
    try:server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:pass
    finally:server.server_close();app.executor.shutdown(wait=True,cancel_futures=True)
    return 0

"""Local first-run template dialog. Only the player starts a console connection."""
from pathlib import Path
from queue import Empty, Queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
import webbrowser

from .template_build import blender_path, build_template
from .donor_source import ftp_reader

ARTIST = 'https://sketchfab.com/3d-models/1997-toyota-supra-rz-d6ed1376d91e4fd3b8e6fc061065c44f'


def wizard(workspace):
    root = tk.Tk()
    root.title('Prepare your GT6 car template')
    form = ttk.Frame(root, padding=18)
    form.grid(sticky='nsew')
    form.columnconfigure(1, weight=1)
    root.columnconfigure(0, weight=1)
    ttk.Label(form, text='Prepare your car template', font=('Segoe UI', 15, 'bold')).grid(row=0, columnspan=3, sticky='w')
    ttk.Label(form, text='Use your own European GT6 1.22 files and the original model by OUTPISTON.').grid(row=1, columnspan=3, sticky='w', pady=(3, 10))
    fields = {}

    def field(row, key, label, *, folder=False, path=False, initial='', secret=False):
        var = tk.StringVar(value=initial)
        fields[key] = var
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12))
        ttk.Entry(form, textvariable=var, width=54, show='*' if secret else '').grid(row=row, column=1, sticky='ew', pady=3)
        if folder or path:
            def browse():
                choice = filedialog.askdirectory(parent=root) if folder else filedialog.askopenfilename(parent=root)
                if choice:
                    var.set(choice)
            ttk.Button(form, text='Browse…', command=browse).grid(row=row, column=2, padx=(6, 0))

    field(2, 'model', 'Original Supra .glb', path=True)
    ttk.Button(form, text='Open the artist’s download page', command=lambda: webbrowser.open(ARTIST)).grid(row=3, column=1, sticky='w')
    field(4, 'blender', 'Blender 5.2 executable', path=True, initial=str(blender_path() or ''))
    modes = ('PS3 over FTP', 'Disc archive on this PC', 'Six extracted donor files')
    method = tk.StringVar(value=modes[0])
    ttk.Label(form, text='Read donor from').grid(row=5, column=0, sticky='w')
    selector = ttk.Combobox(form, textvariable=method, values=modes, state='readonly')
    selector.grid(row=5, column=1, sticky='ew', pady=6)
    field(6, 'update', 'Local stock PDIPFS folder', folder=True)
    field(7, 'host', 'PS3 local IP address')
    field(8, 'port', 'FTP port', initial='21')
    field(9, 'user', 'FTP user', initial='anonymous')
    field(10, 'password', 'FTP password', secret=True)
    field(11, 'disc', 'Local GT.VOL', path=True)
    field(12, 'stock', 'Extracted 0006 folder', folder=True)

    def visible(*_):
        rows = ({6, 7, 8, 9, 10}, {6, 11}, {12})[modes.index(method.get())]
        for row in range(6, 13):
            for widget in form.winfo_children():
                if getattr(widget, '_garage_row', None) is None:
                    info = widget.grid_info()
                    if info:
                        widget._garage_row = int(info['row'])
                if getattr(widget, '_garage_row', None) == row:
                    widget.grid() if row in rows else widget.grid_remove()

    selector.bind('<<ComboboxSelected>>', visible)
    visible()
    status = tk.StringVar(value='The template is stored in your Garage workspace and reused for future cars.')
    ttk.Label(form, textvariable=status, wraplength=560).grid(row=14, columnspan=3, sticky='w', pady=10)
    events = Queue()

    def poll():
        try:
            while True:
                event, value = events.get_nowait()
                status.set(value)
                if event == 'done':
                    button.configure(text='Close', command=root.destroy, state='normal')
                    root.protocol('WM_DELETE_WINDOW', root.destroy)
                    return
                if event == 'error':
                    button.configure(state='normal')
                    root.protocol('WM_DELETE_WINDOW', root.destroy)
                    return
        except Empty:
            root.after(150, poll)

    def start():
        values = {k: v.get().strip() for k, v in fields.items()}
        mode = method.get()
        install = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
        button.configure(state='disabled')
        status.set('Checking your inputs…')
        root.protocol('WM_DELETE_WINDOW', lambda: status.set('Please leave this window open while Garage prepares the template.'))

        def worker():
            try:
                read = None
                if mode == modes[0]:
                    if not values['host']:
                        raise ValueError('Enter your PS3’s local IP address.')
                    read = ftp_reader(values['host'], int(values['port']), values['user'] or 'anonymous', values['password'])
                result = build_template(values['model'], workspace / 'Car template',
                                        stock=values['stock'] if mode == modes[2] else None,
                                        update=values['update'], disc=values['disc'] if mode == modes[1] else None,
                                        read_ftp=read, blender=values['blender'],
                                        packer=install / 'Support/GTToolsSharp/GTToolsSharp.exe',
                                        progress=lambda value: events.put(('progress', value)))
                events.put(('done', f'Your template is ready. Return to Garage and choose Use my stock PDIPFS. Saved in {result}'))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        poll()

    button = ttk.Button(form, text='Prepare template', command=start)
    button.grid(row=15, columnspan=3, sticky='e')
    root.mainloop()

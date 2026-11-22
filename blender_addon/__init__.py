# SPDX-License-Identifier: GPL-3.0-or-later
"""GT6 Garage Blender extension. The Garage worker prepares a private GLB snapshot."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from .scene_check import issues


_folder = None
_process = None
_owner = None
_report = {}
_choices = {}
SURFACES = [
    ('auto','Choose a surface','Keep the detected surface'),
    ('body','Solid body / markings','Use an opaque panel with its colour image'),
    ('interior','Solid interior','Use an opaque interior surface'),
    ('glass','Glass','Keep tint and transparency with the GT6 glass finish'),
    ('headlight','Headlight lens','Use the GT6 front lamp'),
    ('taillight','Taillight lens','Use the GT6 rear lamp'),
    ('brakelight','Brake light','Use the GT6 brake lamp'),
]
LEVELS = {'choose': ('Choose','QUESTION'), 'block': ('Fix in Blender','ERROR'),
          'fixed': ('Fixed','CHECKMARK'), 'info': ('Note','INFO'), 'workshop': ('Review in Garage','INFO')}


def prefs(context):
    return context.preferences.addons[__package__].preferences


def application(context):
    setting = prefs(context).application
    if setting:
        return Path(bpy.path.abspath(setting))
    profile = Path(os.environ.get('LOCALAPPDATA',''))/'GT6Garage_ModelTests'/'installation.json'
    try:
        return Path(json.loads(profile.read_bytes())['application'])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def refresh():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='VIEW_3D':
                area.tag_redraw()


def clear_cache():
    global _folder, _process, _owner, _report
    if _process and _process.poll() is None:
        _process.kill()
        _process.wait()
    _process = None
    if _folder:
        _folder.cleanup()
    _folder, _owner, _report = None, None, {}


def invalidate(self, context):
    # A changed surface decision or export scope needs a freshly checked copy.
    if context:
        context.window_manager.gt6_prep.ready = False
        context.window_manager.gt6_prep.saved_path = ''


def surface_changed(self, context):
    if self.key:
        if self.surface=='auto':
            _choices.pop(self.key,None)
        else:
            _choices[self.key] = self.surface
    invalidate(self,context)


class GT6Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    application: StringProperty(name='GT6Garage.exe', subtype='FILE_PATH')
    def draw(self, context):
        self.layout.prop(self, 'application')


class GT6Finding(bpy.types.PropertyGroup):
    level: StringProperty()
    material: StringProperty()
    part: StringProperty()
    key: StringProperty()
    message: StringProperty()
    surface: EnumProperty(name='Use as', items=SURFACES, update=surface_changed)


class GT6State(bpy.types.PropertyGroup):
    selected: BoolProperty(name='Selected meshes only', default=False, update=invalidate)
    status: StringProperty(default='Open a car, then check its visible meshes.')
    ready: BoolProperty(default=False)
    saved_path: StringProperty(options={'SKIP_SAVE'})
    rows: CollectionProperty(type=GT6Finding)
    active: IntProperty(default=0)
    part_type: EnumProperty(name='Part type',items=[('tyre','Tyre','Separate outer rubber tyre'),
        ('rim','Rim','Separate wheel rim'), *SURFACES[1:]])
    wheel_corner: EnumProperty(name='Corner', items=[(v,n,'') for v,n in [('FL','Front left'),('FR','Front right'),('RL','Rear left'),('RR','Rear right')]])


def fill(state, rows, decisions):
    state.rows.clear()
    priority = {'choose':0,'block':1,'workshop':2,'fixed':3,'info':4}
    for item in sorted(rows, key=lambda r:priority.get(r['level'],5)):
        row = state.rows.add()
        row.level, row.message = item['level'], item['message']
        row.material, row.part = item.get('material',''), item.get('object','')
        row.key = item.get('material_key','')
        if row.key in decisions:
            row.surface = decisions[row.key]
    state.active = 0


def poll_worker():
    global _process, _report
    if _process is None:
        return None
    if _process.poll() is None:
        return .25
    code, _process = _process.returncode, None
    state = bpy.context.window_manager.gt6_prep
    folder = Path(_folder.name)
    try:
        if code or not (folder/'report.json').is_file():
            raise ValueError('Select GT6Garage.exe from the complete 0.5.0 or newer Windows download, then check again.')
        result = json.loads((folder/'report.json').read_bytes())
        if result.get('schema')!=1:
            raise ValueError('Update the Blender add-on and GT6 Garage together.')
        choices = json.loads((folder/'request.json').read_bytes())['choices']
        fill(state, result['findings'], choices)
        _report = result
        state.ready = bool(result['can_export'])
        garage = result.get('garage',{})
        if state.ready:
            state.status = {'ready_to_build':'Car and native garage/race body checks passed. Review the fit in Garage.',
                            'needs_game_files':'Source checks passed. Set up your game files in Garage for native checks.'
                            }.get(garage.get('status'),'Prepared. Review the reported fit issues in Car workshop.')
        else:
            state.status = 'Resolve the marked items, then check the car again.'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state.ready = False
        state.status = str(exc)
    refresh()
    return None


class GT6_OT_check(bpy.types.Operator):
    bl_idname = 'gt6garage.check_car'
    bl_label = 'Check & prepare car'
    bl_description = 'Check every exported material and run the GT6 car checks on a temporary copy'
    @classmethod
    def poll(cls, context):
        return context.mode=='OBJECT' and _process is None
    def execute(self, context):
        global _process, _folder, _owner
        state = context.window_manager.gt6_prep
        exe = application(context)
        if not exe or not exe.is_file() or exe.name.lower()!='gt6garage.exe':
            self.report({'ERROR'}, 'Open GT6 Garage 0.5.0 once, or choose its GT6Garage.exe in this panel.')
            return {'CANCELLED'}
        choices = _choices.copy()
        clear_cache()
        state.ready = False
        state.saved_path = ''
        try:
            problems = issues(context, state.selected)
            if problems:
                fill(state, problems, choices)
                state.status = 'Fix the source items below, then check again.'
                return {'FINISHED'}
            _folder = tempfile.TemporaryDirectory(prefix='gt6-blender-', ignore_cleanup_errors=True)
            _owner = context.scene.as_pointer()
            folder = Path(_folder.name)
            settings = dict(filepath=str(folder/'source.glb'), export_format='GLB', use_selection=state.selected,
                            use_visible=True, use_active_scene=True, export_animations=False, export_skins=False,
                            export_morph=False, export_apply=True, export_current_frame=True, export_yup=True,
                            export_rest_position_armature=False, export_normals=True, export_texcoords=True,
                            export_extras=True, export_draco_mesh_compression_enable=False, export_lights=False,
                            export_cameras=False, export_meshopt_compression_enable=False, will_save_settings=False)
            export = bpy.ops.export_scene.gltf
            supported = export.get_rna_type().properties.keys()
            if 'FINISHED' not in export(**{k:v for k,v in settings.items() if k in supported}):
                raise ValueError('Blender could not export the visible car. Check its meshes and images.')
            (folder/'request.json').write_text(json.dumps({'schema':1,'choices':choices}),encoding='utf8')
            _process = subprocess.Popen([str(exe),'--blender-check',str(folder)],cwd=folder,
                                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            state.status = 'Checking the exported car and preparing its textures…'
            if not bpy.app.timers.is_registered(poll_worker):
                bpy.app.timers.register(poll_worker, first_interval=.25)
            return {'FINISHED'}
        except (OSError, RuntimeError, ValueError) as exc:
            clear_cache()
            state.status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class GT6_OT_cancel(bpy.types.Operator):
    bl_idname = 'gt6garage.cancel_check'
    bl_label = 'Cancel check'
    def execute(self, context):
        clear_cache()
        context.window_manager.gt6_prep.status = 'Check cancelled.'
        context.window_manager.gt6_prep.ready = False
        return {'FINISHED'}


class GT6_OT_find(bpy.types.Operator):
    bl_idname = 'gt6garage.find_part'
    bl_label = 'Select affected object'
    def execute(self, context):
        state = context.window_manager.gt6_prep
        obj = context.view_layer.objects.get(state.rows[state.active].part)
        if not obj or not obj.visible_get():
            self.report({'WARNING'},'Find the named object or material in the Outliner.')
            return {'CANCELLED'}
        context.view_layer.objects.active = obj
        obj.select_set(True)
        return {'FINISHED'}


class GT6_OT_tag(bpy.types.Operator):
    bl_idname = 'gt6garage.mark_part'
    bl_label = 'Mark selected mesh'
    bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        obj = context.active_object
        if context.mode!='OBJECT' or len(context.selected_objects)!=1 or not obj or obj.type!='MESH':
            self.report({'WARNING'}, 'Select one mesh whose surfaces all have the selected part type.')
            return {'CANCELLED'}
        state = context.window_manager.gt6_prep
        obj['gt6_role'] = state.part_type
        if state.part_type in ('tyre','rim'):
            obj['gt6_corner'] = state.wheel_corner
        elif 'gt6_corner' in obj:
            del obj['gt6_corner']
        state.ready = False
        self.report({'INFO'}, 'Part marked. Check the car again.')
        return {'FINISHED'}


class GT6_OT_save(bpy.types.Operator, ExportHelper):
    bl_idname = 'gt6garage.save_checked'
    bl_label = 'Save checked GLB'
    filename_ext = '.glb'
    filter_glob: StringProperty(default='*.glb',options={'HIDDEN'})
    @classmethod
    def poll(cls, context):
        return _process is None and context.window_manager.gt6_prep.ready and _owner==context.scene.as_pointer()
    def invoke(self, context, event):
        self.filepath = (Path(bpy.data.filepath).stem or 'My car')+' - GT6.glb'
        return super().invoke(context,event)
    def execute(self, context):
        source = Path(_folder.name)/'prepared.glb'
        target = Path(bpy.path.abspath(self.filepath)).resolve()
        if target.suffix.lower()!='.glb':
            self.report({'ERROR'},'Choose a .glb filename.')
            return {'CANCELLED'}
        tmp = None
        try:
            digest = hashlib.sha256()
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as out, source.open('rb') as inp:
                tmp = Path(out.name)
                while chunk := inp.read(1024*1024):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest()!=_report['sha256']:
                raise ValueError('The checked snapshot changed. Check the car again.')
            os.replace(tmp,target)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
        context.window_manager.gt6_prep.saved_path = str(target)
        self.report({'INFO'},'Saved '+target.name+'. Choose this file in GT6 Garage.')
        return {'FINISHED'}


class GT6_OT_copy_path(bpy.types.Operator):
    bl_idname = 'gt6garage.copy_checked_path'
    bl_label = 'Copy checked file path'
    bl_description = 'Paste this path into the Garage Choose models file picker'
    @classmethod
    def poll(cls, context):
        return GT6_OT_save.poll(context) and bool(context.window_manager.gt6_prep.saved_path)
    def execute(self, context):
        context.window_manager.clipboard = context.window_manager.gt6_prep.saved_path
        self.report({'INFO'}, 'Paste the path in the File name box under Choose models.')
        return {'FINISHED'}


class GT6_UL_findings(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        label, symbol = LEVELS.get(item.level, ('Note','INFO'))
        layout.label(text=label+' · '+(item.material or item.part or item.message), icon=symbol)


def paragraph(layout, text):
    for line in textwrap.wrap(text, width=max(28, int(bpy.context.region.width/7)-6)):
        layout.label(text=line)


class GT6_PT_preflight(bpy.types.Panel):
    bl_label = 'GT6 Garage'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'GT6 Garage'
    def draw(self, context):
        layout = self.layout
        state = context.window_manager.gt6_prep
        executable = application(context)
        if not executable or not executable.is_file():
            paragraph(layout,'Open GT6 Garage once or select its application below.')
        layout.prop(prefs(context),'application')
        edit = layout.column()
        edit.enabled = _process is None
        edit.prop(state,'selected')
        if context.mode != 'OBJECT':
            paragraph(layout, 'Switch to Object Mode to check the car.')
        edit.operator(GT6_OT_check.bl_idname,icon='VIEWZOOM')
        if _process is not None:
            layout.operator(GT6_OT_cancel.bl_idname)
        paragraph(layout,state.status)
        if state.rows:
            layout.template_list('GT6_UL_findings','',state,'rows',state,'active',rows=5)
            row = state.rows[min(state.active,len(state.rows)-1)]
            box = layout.box()
            if row.material:
                box.label(text=row.material,icon='MATERIAL')
            paragraph(box,row.message)
            if row.part:
                box.label(text=row.part,icon='MESH_DATA')
                box.operator(GT6_OT_find.bl_idname)
            if row.key:
                control = box.column()
                control.enabled = _process is None
                first = next(item for item in state.rows if item.key==row.key)
                control.prop(first,'surface')
        layout.operator(GT6_OT_save.bl_idname,icon='EXPORT')
        if state.ready and state.saved_path and _owner == context.scene.as_pointer():
            paragraph(layout, 'In Garage, choose this saved file.')
            paragraph(layout, state.saved_path)
            layout.operator(GT6_OT_copy_path.bl_idname,icon='COPYDOWN')
        elif state.ready:
            paragraph(layout,'This saves the last checked copy. Check again after editing your car.')
        layout.separator()
        layout.label(text='Tag a whole mesh')
        edit = layout.column(align=True)
        edit.enabled = _process is None
        edit.prop(state,'part_type')
        if state.part_type in ('tyre','rim'):
            edit.prop(state,'wheel_corner')
        edit.operator(GT6_OT_tag.bl_idname)


classes = (GT6Preferences,GT6Finding,GT6State,GT6_OT_check,GT6_OT_cancel,GT6_OT_find,GT6_OT_tag,
           GT6_OT_save,GT6_OT_copy_path,GT6_UL_findings,GT6_PT_preflight)


@persistent
def on_load(_):
    clear_cache()
    _choices.clear()
    if bpy.app.timers.is_registered(poll_worker):
        bpy.app.timers.unregister(poll_worker)


@persistent
def new_scene(_):
    state = bpy.context.window_manager.gt6_prep
    state.ready = False
    state.saved_path = ''
    state.rows.clear()
    state.status = 'Open a car, then check its visible meshes.'


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.gt6_prep = PointerProperty(type=GT6State, options={'SKIP_SAVE'})
    bpy.app.handlers.load_pre.append(on_load)
    bpy.app.handlers.load_post.append(new_scene)
    atexit.register(clear_cache)


def unregister():
    on_load(None)
    atexit.unregister(clear_cache)
    bpy.app.handlers.load_pre.remove(on_load)
    bpy.app.handlers.load_post.remove(new_scene)
    del bpy.types.WindowManager.gt6_prep
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

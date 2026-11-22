# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch source features the glTF exporter can silently omit."""
from pathlib import Path

import bpy


def upstream(socket):
    pending = [link.from_node for link in socket.links] if socket else []
    seen = set()
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        yield node
        for pin in node.inputs:
            pending.extend(link.from_node for link in pin.links)


def issues(context, selected):
    meshes = [obj for obj in context.view_layer.objects if obj.type=='MESH' and obj.visible_get()
              and (not selected or obj.select_get())]
    findings = []
    def issue(message, obj='', mat=''):
        findings.append(dict(level='block', object=obj, material=mat, message=message))
    if not meshes:
        issue('Select the car meshes in Object Mode, or change the scope to Visible meshes.')
    deps = context.evaluated_depsgraph_get()
    checked = set()
    for obj in meshes:
        evaluated = obj.evaluated_get(deps)
        if evaluated.matrix_world.to_3x3().determinant() == 0:
            issue('This object has a zero scale. Correct its scale or hide it from this export.', obj.name)
        mesh = evaluated.data
        if not mesh.polygons:
            issue('This mesh has no faces. Hide it from the car export or add its surfaces.', obj.name)
        for number in {poly.material_index for poly in mesh.polygons}:
            if number >= len(evaluated.material_slots):
                continue
            mat = evaluated.material_slots[number].material
            if not mat or not mat.node_tree:
                continue
            tree = mat.node_tree
            output = next((n for n in tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output), None)
            nodes = list(upstream(output.inputs.get('Surface'))) if output else []
            if any(n.type=='TEX_IMAGE' for n in nodes) and not mesh.uv_layers:
                issue('This textured mesh needs a UV map. Unwrap it in Blender, then check again.', obj.name, mat.name)
            if mat.name in checked:
                continue
            checked.add(mat.name)
            if not output or not output.inputs['Surface'].is_linked:
                issue('Connect the surface shader to Material Output before exporting.', obj.name, mat.name)
                continue
            surface = output.inputs['Surface'].links[0].from_node
            while surface.type=='REROUTE' and surface.inputs[0].is_linked:
                surface = surface.inputs[0].links[0].from_node
            if surface.type not in ('BSDF_PRINCIPLED','EMISSION','BACKGROUND'):
                issue('Bake the visible shader to an image in Blender and connect it to a Principled BSDF for export.', obj.name, mat.name)
            # Inspect the actual connected textures, including outstanding packed paths.
            for node in nodes:
                if node.type=='TEX_IMAGE':
                    im = node.image
                    if not im:
                        issue('Choose an image for the empty Image Texture node.', obj.name, mat.name)
                    elif im.source in ('TILED','MOVIE','SEQUENCE','VIEWER'):
                        issue('Bake '+im.name+' to one still image on a single UV layout.',obj.name,mat.name)
                    elif im.source=='FILE' and not im.packed_file:
                        path = bpy.path.abspath(im.filepath, library=im.library)
                        if not path or not Path(path).is_file():
                            issue('Locate or pack the missing image '+im.name+' ('+im.filepath+').',obj.name,mat.name)
                elif node.type.startswith('TEX_') and node.type!='TEX_COORD':
                    issue('Bake the procedural '+node.name+' to a UV image in Blender before export.',obj.name,mat.name)
            if surface.type=='BSDF_PRINCIPLED':
                colour_nodes = upstream(surface.inputs.get('Base Color'))
                if any(n.type in ('GROUP','VALTORGB','CURVE_RGB','HUE_SAT','BRIGHTCONTRAST','INVERT')
                       for n in colour_nodes):
                    issue('Bake the processed Base Color to a UV image and connect that image directly to Base Color.',obj.name,mat.name)
                normal = surface.inputs.get('Normal')
                if normal.is_linked and not any(n.type=='NORMAL_MAP' for n in upstream(normal)):
                    issue('Bake the bump or procedural Normal input to a tangent-space normal image first.',obj.name,mat.name)
    return findings

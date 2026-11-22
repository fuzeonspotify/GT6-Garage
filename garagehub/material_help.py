"""Player-facing recovery steps for source materials that cannot be built."""
from PIL import Image

from .car_materials import Materials
from .errors import GarageError


REIMPORT = 'Export a new .glb from Blender and import that copy into Garage.'
BODY_DETAIL = ('In Car workshop, select a panel with the markings and choose Component type → Body detail. '
               'If every part using this material should match, click Apply role to this material.')
OPTICS = ('For a window or lamp lens, select its part in Garage and choose Glass, Headlight or Taillight '
          'to match its use. Click Check wheel fit and review the proposed GT6 glass or lights before choosing Apply to preview.')


def material_error(scene, index, part_id, code, reason, steps, *, check_alpha=False):
    material = scene.materials[index]
    parts = [p for p in scene.parts if p.material == index]
    part = next(p for p in parts if p.id == part_id)
    help_data = {'material': material['name'], 'part_id': part.id, 'part_name': part.name,
                 'part_count': len(parts), 'steps': steps}
    if check_alpha and material.get('alpha_mode') == 'BLEND' and material.get('base_texture') is not None:
        # Read at source resolution. Resizing first could hide small transparent markings.
        try:
            with Materials(scene, None).embedded_image(material['base_texture']) as image:
                low, _ = image.getchannel('A').getextrema()
        except (ValueError, OSError, IndexError, KeyError, TypeError, Image.DecompressionBombError):
            pass  # Keep the repair steps when the texture cannot be inspected.
        else:
            help_data['texture_note'] = ('Every pixel of the base colour texture is opaque.'
                                         if low == 255 else 'The base colour texture contains transparent pixels. '
                                         'Check which details use them before changing Alpha.')
    return GarageError(f'“{material["name"]}” {reason}', code, {'material_help': help_data})


def alpha_steps(material, role):
    if material.get('alpha_mode') == 'MASK':
        return ['If the holes are part of the design, model the openings in the mesh and use an opaque material. '
                'Garage cannot build holes from an alpha mask.',
                'If this is a window, remove the cutout from the Principled BSDF Alpha input in Blender '
                'and choose an Alpha value between 0 and 1 for the tint.',
                REIMPORT, 'For window glass, choose Component type → Glass in Garage and review the GT6 glass.']
    steps = []
    if material.get('alpha_mode') == 'BLEND':
        steps.append('For a solid panel, select this material in Blender’s Shading workspace. '
                     'On Principled BSDF, disconnect the wire into Alpha and set Alpha to 1.000. '
                     'Keep the colour texture connected to Base Color.')
    if material.get('transmission'):
        steps.append('For solid bodywork, disconnect any input to Principled BSDF → Transmission → Weight '
                     'and set Weight to 0. Keep genuine glass on a separate material.')
    if not steps:
        steps.append('Check that this solid material exports with opaque alpha and zero transmission.')
    steps.append(REIMPORT)
    if role == 'paint' and material.get('base_texture') is not None:
        steps.append(BODY_DETAIL)
    steps.append(OPTICS)
    return steps


def finish_steps(features):
    steps = []
    if 'KHR_materials_specular' in features:
        steps.append('In Blender’s Shading workspace, select this material. On Principled BSDF → Specular, '
                     'disconnect inputs to IOR Level and Tint. Set IOR Level to 0.5 and Tint to white '
                     'to use the default reflection settings on the export copy.')
    other = sorted(set(features) - {'KHR_materials_specular'})
    if other:
        steps.append('Make a copy of this material for export. On that copy, remove or bake the effects '
                     'listed in the warning that you can replace with the GT6 finish. Keep the Base Color '
                     'texture if it carries markings.')
    return [*steps, REIMPORT]

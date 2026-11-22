"""Explicit opaque glTF two-sided surfaces for the reviewed shell route.

glTF 2.0 section 3.9.5 requires negating back-facing lighting normals. The
authenticated native opaque recipes retain face culling, so each prepared LOD
can represent those surfaces as opposite winding with separately negated
normals. Generate backfaces only AFTER simplification; a simplifier must never
merge these coincident vertices or average their opposite normals.

This does not reproduce a glTF shader, infer material intent, alter source
bytes, or enable transparent/two-sided functional lamps. Native material state
must be checked against the actual owned template before preview/build.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from .car_profiles import ASSETS, PROFILE
from .errors import GarageError
from .native import MDL3
from .util import sha256


PLAN_KEY = 'opaque_two_sided_realization'
SHELL_SCOPE = 'template_shell_developer_review'
METHOD = 'reversed_winding_split_normals_after_lod'
OPAQUE_ROLES = frozenset(('body', 'paint', 'interior'))
EXCLUDED_ROLES = frozenset(('rim', 'tyre', 'valve', 'ignore'))

_SCALAR_FINISHES = {
    'KHR_materials_clearcoat': {'clearcoatFactor':1, 'clearcoatRoughnessFactor':1},
    'KHR_materials_specular': {'specularFactor':1, 'specularColorFactor':3},
    'KHR_materials_emissive_strength': {'emissiveStrength':1},
}


def native_finish_substitutions(source):
    """List exactly the source effects a player can replace with the native finish."""
    features = []
    if any(source.get('emissiveFactor', [])):
        features.append('emissiveFactor')
    if 'emissiveTexture' in source:
        features.append('emissiveTexture')
    if source.get('alphaMode','OPAQUE')=='OPAQUE' and 'KHR_materials_transmission' not in source.get('extensions',{}):
        maps={key:source.get(key) for key in ('normalTexture','occlusionTexture')}
        maps['metallicRoughnessTexture']=source.get('pbrMetallicRoughness',{}).get('metallicRoughnessTexture')
        for key,value in maps.items():
            if isinstance(value,Mapping) and type(value.get('index')) is int and value['index']>=0:
                features.append(key)
    for name, fields in _SCALAR_FINISHES.items():
        value = source.get('extensions', {}).get(name)
        if not isinstance(value, Mapping) or set(value) - fields.keys():
            continue
        valid = True
        for field, number in value.items():
            components = number if isinstance(number, list) else [number]
            valid &= (len(components) == fields[field] and (fields[field] != 1 or not isinstance(number,list))
                      and all(type(x) in (float,int) and math.isfinite(x) and x >= 0
                              and (name == 'KHR_materials_emissive_strength' or x <= 1) for x in components))
        if valid:
            features.append(name)
    return features


def _need(value, message):
    if not value:
        raise GarageError(message, 'shell_surface_realization')


def opaque_two_sided_plan(scene, inspection):
    """Derive a deterministic plan from current surfaces and reviewed roles.

    Counts here precede LOD reduction. They account only for selected two-sided
    source parts, so adding the separate supplied-light kit does not change the
    plan. Per-LOD receipts record the actual prepared front/back counts.
    """
    _need(isinstance(inspection, Mapping) and isinstance(inspection.get('parts'), list),
          'Two-sided surface conversion needs current reviewed component roles.')
    rows = inspection['parts']
    _need(all(isinstance(row, Mapping) and isinstance(row.get('id'), str) for row in rows),
          'Two-sided surface conversion received invalid component identities.')
    mapping = {row['id']: row for row in rows}
    _need(len(mapping) == len(rows), 'Two-sided surface conversion received duplicate component identities.')
    records = []
    optics = []
    for part in scene.parts:
        _need(part.id in mapping, 'Review every component before converting two-sided surfaces.')
        role = mapping[part.id].get('role')
        if role in EXCLUDED_ROLES:
            continue
        material = scene.materials[part.material]
        if mapping[part.id].get('use_native_optics') is True:
            _need(role in ('glass', 'headlight', 'taillight', 'brakelight'),
                  'Native optics require a reviewed glass or exterior lamp role.')
            # A player-approved exterior lens uses the existing optical material
            # and emitter draw states. Submit its authored faces once.
            optics.append({'id': part.id, 'role': role, 'material': part.material,
                           'source_double_sided': material['double_sided'],
                           'source_emissive': list(material['emissive']),
                           'method':'use_native_exterior_optics'})
            continue
        if not material.get('double_sided', False):
            continue
        _need(material.get('double_sided') is True,
              'The source two-sided setting must be a Boolean.')
        _need(role in OPAQUE_ROLES,
              'Two-sided conversion supports opaque Body detail, Paint and Interior surfaces; '
              'glass and functional lamp surfaces need a separate native recipe.')
        transmission = material.get('transmission', 0.)
        _need(isinstance(transmission, (int, float)) and not isinstance(transmission, bool)
              and math.isfinite(transmission) and transmission == 0.
              and material.get('alpha_mode') == 'OPAQUE',
              'Two-sided transparent, cutout or transmissive surfaces need a separate native recipe.')
        use_native_lighting = mapping[part.id].get('use_native_finish') is True
        originals=getattr(scene,'document',{}).get('materials',[]) if use_native_lighting else []
        replaced=native_finish_substitutions(originals[part.material]) if part.material<len(originals) else []
        _need(not material.get('normal_map') or 'normalTexture' in replaced,
              'Two-sided normal-mapped surfaces need a tangent-aware native recipe.')
        _need(use_native_lighting or not any(material.get('emissive', (0., 0., 0.))),
              'Two-sided emissive surfaces need a separately reviewed native recipe.')
        _need(role != 'paint' or material.get('base_texture') is None,
              'Textured Paint needs a native paint-texture recipe.')
        # Mirror the existing opaque native recipe selection, including its
        # earlier uniform-palette branch, without changing that production code.
        if role == 'paint':
            template = 0
        elif material.get('base_texture') is None and material['metallic'] < .4:
            template = 1
        else:
            template = 2 if material['metallic'] > .3 else 1
        records.append({'id': part.id, 'role': role, 'material': part.material,
                        **({'lighting':'native_opaque', 'source_emissive':list(material['emissive'])}
                           if use_native_lighting else {}),
                        'native_template_material': template,
                        'source_triangles': len(part.indices),
                        'source_vertices': len(part.positions)})
    records.sort(key=lambda row: row['id'])
    triangles = sum(row['source_triangles'] for row in records)
    vertices = sum(row['source_vertices'] for row in records)
    return {**({'native_exterior_optics': optics} if optics else {}),
            'schema': 1, 'method': METHOD, 'profile_id': PROFILE['id'],
            'part_ids': [row['id'] for row in records], 'parts': records,
            'source_triangles': triangles, 'source_vertices': vertices,
            'generated_backface_triangles_before_lod': triangles,
            'generated_backface_vertices_before_lod': vertices,
            'emitted_triangles_before_lod': triangles*2,
            'native_face_culling_required': bool(records),
            'source_unchanged': True, 'native_rendering_verified': False}


def validate_opaque_two_sided_plan(scene, inspection, plan):
    """Reject stale, partial or invented realization plans before budgeting."""
    _need(isinstance(plan, Mapping), 'Review the opaque two-sided surface conversion again.')
    expected = opaque_two_sided_plan(scene, inspection)
    _need(dict(plan) == expected,
          'The opaque two-sided surface plan no longer matches the current source and roles.')
    return frozenset(expected['part_ids'])


def duplicate_backfaces(positions, normals, uv, faces):
    """Return split opposite faces; retain every prepared front attribute exactly."""
    p, n, t, f = (np.asarray(value) for value in (positions, normals, uv, faces))
    _need(p.ndim == 2 and p.shape[1] == 3 and n.shape == p.shape
          and t.shape == (len(p), 2) and f.ndim == 2 and f.shape[1] == 3
          and np.issubdtype(f.dtype, np.integer) and len(p) and len(f),
          'Prepared two-sided geometry has invalid vertex or triangle arrays.')
    _need(np.isfinite(p).all() and np.isfinite(n).all() and np.isfinite(t).all()
          and f.min() >= 0 and f.max() < len(p),
          'Prepared two-sided geometry has invalid attributes or indices.')
    # Independent vertex indices are essential: an indexed vertex has one
    # authored normal, and the back side must not borrow its front normal.
    back = f[:, [0, 2, 1]].astype(np.int64) + len(p)
    return (np.concatenate((p, p)), np.concatenate((n, -n)),
            np.concatenate((t, t)), np.concatenate((f, back)))


def verify_native_opaque_culling(raw, lod):
    """Read the actual pinned native material state for every paint bank.

    Layout reference: Nenkai's original MDL3MaterialSet.bt, Material (+6 GCM
    index), MaterialMap (+5 count/+16 table pointer), CellGcmParam (0xa0 bytes).
    The cull method word is +0x8c and its enable payload is +0x90. The checker
    verifies the method words too; it never treats an arbitrary 1 as evidence.
    https://github.com/Nenkai/GT-File-Specifications-Documentation/blob/master/
    Formats/Shared/Models/MDL3MaterialSet.bt

    These serialized state checks are not a current game rendering test.
    """
    _need(lod in ('hq', 'race'), 'Unknown body LOD for the two-sided native material check.')
    asset = lod+'/body'
    _need(isinstance(raw, bytes) and sha256(raw) == ASSETS[asset],
          'Two-sided surface conversion requires the exact authenticated native body template.')
    model = MDL3(raw)
    model.validate()
    ms = model.u32(0x44)
    model.span(ms, 24, model.cut)
    material_count = model.u16(ms)
    banks, gcm_count = model.data[ms+4:ms+6]
    materials, params = model.u32(ms+8), model.u32(ms+16)
    _need(banks == 7 and material_count == 79*banks and gcm_count > 0,
          'The native opaque material-bank layout is not the reviewed body profile.')
    model.span(materials, material_count*52, model.cut)
    model.span(params, gcm_count*160, model.cut)
    records = []
    for bank in range(banks):
        for template in (0, 1, 2):
            row = materials+(bank*79+template)*52
            gcm = model.u16(row+6)
            _need(gcm < gcm_count, 'A native opaque material references missing render state.')
            state = params+gcm*160
            _need(model.u32(state) == 0x00780304 and model.u32(state+4) == 0
                  and model.u32(state+0x10) == 0 and model.u32(state+0x6c) == 0,
                  'The native opaque surface recipe unexpectedly enables alpha testing or blending.')
            _need(model.u32(state+0x7c) == 0x000c0a6c
                  and model.u32(state+0x80) == 0x0203
                  and model.u32(state+0x84) == 1 and model.u32(state+0x88) == 1,
                  'The native opaque surface recipe has unreviewed depth-test/write state.')
            _need(model.u32(state+0x8c) == 0x0004183c and model.u32(state+0x90) == 1,
                  'The native opaque surface recipe does not prove enabled face culling.')
            records.append({'bank': bank, 'template_material': template,
                            'gcm_state': gcm, 'face_culling_enabled': True,
                            'blending_disabled': True, 'depth_write_enabled': True})
    return {'schema': 1, 'asset': asset, 'asset_sha256': sha256(raw),
            'scope': 'authenticated_native_opaque_material_render_state',
            'material_states': records, 'face_culling_enabled': True,
            'native_rendering_verified': False}

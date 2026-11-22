"""Deterministic, bounded source adapters feeding the existing GLB reader.

No input is executed and no referenced resource is read from disk or network.
ZIP resources are resolved in memory. Existing self-contained GLB bytes remain
unchanged, so previously reviewed source fingerprints and recipes still apply.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import math
import posixpath
import shlex
import stat
import struct
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import numpy as np

from .errors import GarageError
from .glb import GLB, MAX_PARTS, MAX_TRIANGLES, MAX_VERTICES
from .util import MAX_MODEL_BYTES, clean_name, safe_rel, sha256

SUPPORTED_INPUT_EXTENSIONS = ('.glb', '.gltf', '.obj', '.zip')
MAX_ARCHIVE_FILES = 4096
MAX_POLYGON_CORNERS = 1024
MAX_TEXT_BYTES = 64 * 1024**2


def _fail(message, code='source_format'):
    raise GarageError(message, code)


def _integer(value, message, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(message)
    return value


def _text(raw, label, warnings):
    if len(raw) > MAX_TEXT_BYTES:
        _fail(f'{label} text exceeds the 64 MiB import limit.', 'model_limit')
    try:
        value = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            value = raw.decode('cp1252')
        except UnicodeDecodeError as e:
            raise GarageError(f'{label} is not readable UTF-8 or Windows text.') from e
        warnings.append(f'{label} uses legacy Windows text encoding; names were preserved as Unicode.')
    if '\x00' in value:
        _fail(f'{label} contains binary data. Choose the original text model.')
    return value


def _resource_path(root, reference, uri=False):
    if not isinstance(reference, str) or not reference.strip():
        _fail('A model resource has an empty filename.', 'missing_resource')
    reference = reference.strip()
    if uri:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            _fail('Only resources included in the model ZIP are allowed; external URLs are not loaded.', 'external_resource')
        reference = unquote(parsed.path)
    reference = reference.replace('\\', '/')
    if reference.startswith('/') or ':' in reference or '\x00' in reference:
        _fail('A model resource points outside its ZIP. Include it using a relative path.', 'unsafe_path')
    result = posixpath.normpath(posixpath.join(posixpath.dirname(root), reference))
    if result == '..' or result.startswith('../') or result.startswith('/'):
        _fail('A model resource leaves its ZIP folder.', 'unsafe_path')
    return safe_rel(result)


def _resource(resources, root, reference, uri=False):
    name = _resource_path(root, reference, uri)
    if name not in resources:
        _fail(f'Missing model resource: {name}. Put the model and its referenced files in one ZIP, keeping their relative folders.', 'missing_resource')
    return resources[name]


def _data_uri(uri):
    try:
        header, payload = uri.split(',', 1)
        if not header.startswith('data:') or not header.lower().endswith(';base64'):
            _fail('Use base64 data URIs or include the resource file in the model ZIP.', 'external_resource')
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as e:
        raise GarageError('A model contains an invalid base64 resource.', 'missing_resource') from e
    if len(raw) > MAX_MODEL_BYTES:
        _fail('An embedded model resource exceeds 256 MiB.', 'model_limit')
    return raw, header[5:-7].lower()


def _image_bytes(raw, filename, warnings, allow_conversion=False):
    """Identify image bytes; optional OBJ legacy formats are decoded by Pillow."""
    mime = None
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif raw.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        mime = 'image/webp'
    if mime or allow_conversion:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if im.width * im.height > 32_000_000 or im.width > 16384 or im.height > 16384:
                    _fail(f'Texture {filename} exceeds the import image limit.', 'model_limit')
                if getattr(im, 'n_frames', 1) != 1:
                    _fail(f'Texture {filename} is animated; export a single PNG image.', 'unsupported_texture')
                if mime:
                    im.verify()
                    return raw, mime
                out = io.BytesIO()
                im.convert('RGBA' if 'A' in im.getbands() else 'RGB').save(out, 'PNG')
                warnings.append(f'{filename} was converted losslessly to PNG for embedded model storage.')
                return out.getvalue(), 'image/png'
        except GarageError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
            raise GarageError(f'Texture {filename} is not a supported readable image. Use PNG or JPEG.', 'unsupported_texture') from e
    _fail(f'Texture {filename} must be PNG, JPEG or WebP.', 'unsupported_texture')


def _pack_glb(document, binary):
    doc = copy.deepcopy(document)
    doc['buffers'] = [{'byteLength': len(binary)}]
    js = json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    if len(js) > 32 * 1024**2:
        _fail('Model metadata exceeds the 32 MiB import limit.', 'model_limit')
    js += b' ' * (-len(js) % 4)
    binary = bytes(binary) + b'\0' * (-len(binary) % 4)
    size = 28 + len(js) + len(binary)
    if size > MAX_MODEL_BYTES:
        _fail('The normalized embedded model exceeds 256 MiB. Reduce its source size.', 'model_limit')
    return (struct.pack('<4sII', b'glTF', 2, size) + struct.pack('<II', len(js), 0x4e4f534a) + js
            + struct.pack('<II', len(binary), 0x004e4942) + binary)


def _append(binary, data):
    binary.extend(b'\0' * (-len(binary) % 4))
    offset = len(binary)
    if offset + len(data) > MAX_MODEL_BYTES:
        _fail('Normalized model resources exceed 256 MiB.', 'model_limit')
    binary.extend(data)
    return offset


def _gltf(raw, root, resources, warnings, embedded=None):
    if len(raw) > 32 * 1024**2:
        _fail('glTF metadata exceeds 32 MiB.', 'model_limit')
    try:
        doc = json.loads(raw.decode('utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (UnicodeError, ValueError, RecursionError) as e:
        raise GarageError('The glTF file does not contain valid UTF-8 JSON.') from e
    if not isinstance(doc, dict) or not isinstance(doc.get('asset'), dict) or doc['asset'].get('version') != '2.0':
        _fail('Choose a glTF 2.0 model.')
    binary = bytearray()
    offsets, lengths = [], []
    buffers = doc.get('buffers', [])
    if not isinstance(buffers, list) or len(buffers) > MAX_ARCHIVE_FILES:
        _fail('The glTF buffer list is invalid.')
    for index, buffer in enumerate(buffers):
        if not isinstance(buffer, dict):
            _fail('A glTF buffer is invalid.')
        count = _integer(buffer.get('byteLength'), 'A glTF buffer has no valid byte length.')
        uri = buffer.get('uri')
        if 'uri' not in buffer and index == 0 and embedded is not None:
            if not 0 <= len(embedded) - count <= 3:
                _fail('The embedded GLB buffer length does not match the model.')
            data = embedded[:count]
        elif not isinstance(uri, str):
            _fail('A text glTF buffer has no resource URI. Include its .bin file or use an embedded GLB.', 'missing_resource')
        else:
            data = _data_uri(uri)[0] if uri.startswith('data:') else _resource(resources, root, uri, True)
        if len(data) != count:
            _fail(f'glTF buffer byteLength ({count}) does not match its resource ({len(data)}). Re-export the complete model.', 'missing_resource')
        offsets.append(_append(binary, data))
        lengths.append(count)
    views = doc.setdefault('bufferViews', [])
    if not isinstance(views, list):
        _fail('The glTF bufferView list is invalid.')
    for view in views:
        if not isinstance(view, dict):
            _fail('A glTF bufferView is invalid.')
        if 'EXT_meshopt_compression' in view.get('extensions', {}):
            _fail('Re-export without meshopt compression before importing.', 'compressed_mesh')
        index = _integer(view.get('buffer', 0), 'Invalid glTF buffer index.')
        off = _integer(view.get('byteOffset', 0), 'Invalid glTF buffer offset.')
        length = _integer(view.get('byteLength'), 'Invalid glTF bufferView length.')
        if index >= len(offsets) or off + length > lengths[index]:
            _fail('A glTF bufferView reads outside its resource.')
        view.update(buffer=0, byteOffset=offsets[index] + off)
    images = doc.get('images', [])
    if not isinstance(images, list):
        _fail('The glTF image list is invalid.')
    for image in images:
        if not isinstance(image, dict):
            _fail('A glTF image is invalid.')
        if 'uri' not in image:
            continue
        if 'bufferView' in image:
            _fail('A glTF image contains both URI and bufferView data.')
        uri = image.pop('uri')
        if not isinstance(uri, str):
            _fail('A glTF texture has an invalid URI.')
        data = _data_uri(uri)[0] if uri.startswith('data:') else _resource(resources, root, uri, True)
        data, mime = _image_bytes(data, image.get('name', uri[:120]), warnings)
        if image.get('mimeType', mime) != mime:
            _fail('A glTF texture MIME type does not match the image bytes.', 'unsupported_texture')
        off = _append(binary, data)
        image.update(bufferView=len(views), mimeType=mime)
        views.append({'buffer': 0, 'byteOffset': off, 'byteLength': len(data)})
    return _pack_glb(doc, binary)


def _words(text):
    try:
        lex = shlex.shlex(text, posix=False)
        lex.whitespace_split = True
        lex.commenters = '#'
        return [x[1:-1] if len(x) >= 2 and x[0] == x[-1] and x[0] in ('\"', "'") else x for x in lex]
    except ValueError as e:
        raise GarageError('A model contains an unclosed quoted filename.') from e


def _numeric(values, count, label):
    if len(values) != count:
        _fail(f'{label} needs {count} numeric values.')
    try:
        result = [float(x) for x in values]
    except ValueError as e:
        raise GarageError(f'{label} contains an invalid number.') from e
    if not all(math.isfinite(x) for x in result):
        _fail(f'{label} contains a non-finite number.')
    return result


def _unit_interval(value, label):
    if not 0 <= value <= 1:
        _fail(f'{label} must be between 0 and 1.')
    return value


def _mtl(raw, root, resources, materials, warnings):
    current = None
    for lineno, line in enumerate(_text(raw, root, warnings).splitlines(), 1):
        tokens = _words(line)
        if not tokens:
            continue
        key, values = tokens[0], tokens[1:]
        if key == 'newmtl':
            name = ' '.join(values)
            if not name or name in materials:
                _fail(f'{root}:{lineno}: missing or duplicate material name {name!r}.')
            current = {'name': name, 'Kd': [.6, .6, .6], 'd': 1., 'Pr': .6, 'Pm': 0., 'root': root}
            materials[name] = current
            continue
        if current is None:
            _fail(f'{root}:{lineno}: material properties appear before newmtl.')
        if key in ('Kd', 'Ks', 'Ka', 'Ke'):
            vals = _numeric(values, 3, f'{root}:{lineno} {key}')
            if any(v < 0 for v in vals) or (key != 'Ke' and any(v > 1 for v in vals)):
                _fail(f'{root}:{lineno}: {key} colour is outside its supported range.')
            current[key] = vals
        elif key in ('d', 'Tr', 'Pr', 'Pm'):
            value = _unit_interval(_numeric(values, 1, f'{root}:{lineno} {key}')[0], key)
            if key in ('d', 'Tr'):
                alpha = value if key == 'd' else 1 - value
                if 'explicit_alpha' in current and abs(alpha - current['d']) > 1e-6:
                    _fail(f'Material {current["name"]} has conflicting d/Tr opacity values. Correct them before importing.')
                current.update(d=alpha, explicit_alpha=True)
            else:
                current[key] = value
                if key == 'Pr':
                    current['explicit_roughness'] = True
        elif key == 'Ns':
            value = _numeric(values, 1, f'{root}:{lineno} Ns')[0]
            if not 0 <= value <= 1000:
                _fail('MTL Ns must be in the supported 0–1000 range.')
            current['Ns'] = value
        elif key == 'Ni':
            current['Ni'] = _numeric(values, 1, f'{root}:{lineno} Ni')[0]
        elif key == 'illum':
            current['illum'] = int(_numeric(values, 1, f'{root}:{lineno} illum')[0])
        elif key == 'map_Kd':
            if not values or values[0].startswith('-'):
                _fail(f'Material {current["name"]} uses map_Kd texture options. Bake that UV transform or export GLB before importing.', 'unsupported_material')
            path = ' '.join(values)
            data = _resource(resources, root, path)
            current['texture'] = (*_image_bytes(data, path, warnings, True), path)
        elif key in ('bump', 'map_Bump', 'norm') or key.startswith('map_') or key in ('disp', 'decal', 'refl'):
            _fail(f'Material {current["name"]} uses unsupported MTL {key}. Bake it to a standard glTF material and export GLB to preserve its appearance.', 'unsupported_material')
        else:
            warnings.append(f'MTL property {key} on {current["name"]} has no imported PBR equivalent; review the material.')


def _cross2(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _triangulate(coords, label):
    """Ear-clip simple planar polygons without an absolute area cutoff."""
    count = len(coords)
    if count < 3 or count > MAX_POLYGON_CORNERS:
        _fail(f'{label}: use faces with 3–{MAX_POLYGON_CORNERS} corners, or triangulate them before import.', 'topology')
    p = np.asarray(coords, dtype=np.float64)
    p = p - p[0]
    normal = np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)
    norm = float(np.linalg.norm(normal))
    scale = float(np.max(np.linalg.norm(p, axis=1)))
    if not math.isfinite(norm) or norm == 0 or scale == 0:
        _fail(f'{label}: a face has zero area or self-intersects. Correct the source face.', 'topology')
    if count == 3:
        return [(0, 1, 2)], normal
    if float(np.max(np.abs(p @ (normal / norm)))) > scale * 1e-5:
        _fail(f'{label}: a non-planar polygon needs explicit triangulation in the source model.', 'topology')
    axis = int(np.argmax(np.abs(normal)))
    xy = np.delete(p, axis, axis=1)
    extent = float(np.max(np.ptp(xy, axis=0)))
    epsilon = extent * extent * 1e-14
    area = sum(_cross2(xy[i], xy[(i + 1) % count]) for i in range(count))
    sign = 1 if area > 0 else -1
    if abs(area) <= epsilon:
        _fail(f'{label}: polygon area is numerically ambiguous; triangulate it explicitly.', 'topology')
    # Reject crossings before ear clipping; a bow-tie must never become a fan.
    def orient(a, b, c):
        return _cross2(b - a, c - a)
    def between(a, b, c):
        return bool(np.all(c >= np.minimum(a, b) - extent * 1e-14) and np.all(c <= np.maximum(a, b) + extent * 1e-14))
    for i in range(count):
        a, b = xy[i], xy[(i + 1) % count]
        if float(np.dot(b - a, b - a)) <= extent * extent * 1e-28:
            _fail(f'{label}: a polygon has a repeated corner. Correct its topology.', 'topology')
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue
            c, d = xy[j], xy[(j + 1) % count]
            ab_c, ab_d, cd_a, cd_b = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
            crossing = ((ab_c > epsilon and ab_d < -epsilon) or (ab_c < -epsilon and ab_d > epsilon)) and ((cd_a > epsilon and cd_b < -epsilon) or (cd_a < -epsilon and cd_b > epsilon))
            touching = ((abs(ab_c) <= epsilon and between(a, b, c)) or (abs(ab_d) <= epsilon and between(a, b, d)) or (abs(cd_a) <= epsilon and between(c, d, a)) or (abs(cd_b) <= epsilon and between(c, d, b)))
            if crossing or touching:
                _fail(f'{label}: polygon edges intersect. Repair or explicitly triangulate the source.', 'topology')
    remaining, triangles = list(range(count)), []
    while len(remaining) > 3:
        found = False
        for at, b in enumerate(remaining):
            a, c = remaining[at - 1], remaining[(at + 1) % len(remaining)]
            if sign * orient(xy[a], xy[b], xy[c]) <= epsilon:
                continue
            inside = any(k not in (a, b, c) and min(sign * orient(xy[a], xy[b], xy[k]), sign * orient(xy[b], xy[c], xy[k]), sign * orient(xy[c], xy[a], xy[k])) >= -epsilon for k in remaining)
            if inside:
                continue
            triangles.append((a, b, c))
            del remaining[at]
            found = True
            break
        if not found:
            _fail(f'{label}: polygon triangulation is ambiguous. Triangulate this face in the source tool.', 'topology')
    if sign * orient(*(xy[x] for x in remaining)) <= epsilon:
        _fail(f'{label}: triangulation leaves a zero-area face. Correct its source topology.', 'topology')
    triangles.append(tuple(remaining))
    return triangles, normal


def _obj(raw, root, resources, warnings):
    positions, texcoords, normals = [], [], []
    faces, materials = [], {}
    object_name, group_name, material_name, smoothing = 'Model', '', None, 'off'
    libraries = []
    triangle_count = 0
    empty_triangles = 0
    for lineno, line in enumerate(_text(raw, root, warnings).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        bits = line.split(None, 1)
        key, rest = bits[0], bits[1] if len(bits) > 1 else ''
        values = rest.partition('#')[0].split() if key in ('v', 'vt', 'vn', 'f', 's') else _words(rest)
        label = f'{root}:{lineno}'
        if key == 'v':
            if len(values) in (6, 7):
                # White vertex colour is the identity multiplier for the MTL.
                colour = _numeric(values[3:], len(values)-3, label)
                if any(v != 1 for v in colour):
                    _fail(f'{label}: bake the nonwhite vertex colours to a texture before exporting this OBJ.', 'unsupported_attribute')
                values = values[:3]
            if len(values) not in (3, 4):
                _fail(f'{label}: OBJ vertices need xyz and optional homogeneous w. Bake vertex colours to a texture or export GLB.', 'unsupported_attribute')
            v = _numeric(values, len(values), label)
            if len(v) == 4:
                if v[3] == 0:
                    _fail(f'{label}: vertex w cannot be zero.')
                v = [x / v[3] for x in v[:3]]
            if max(abs(x) for x in v) > 1e7:
                _fail(f'{label}: vertex coordinates exceed the supported range.')
            positions.append(v)
            if len(positions) > MAX_VERTICES:
                _fail('OBJ exceeds the safe vertex limit.', 'model_limit')
        elif key == 'vt':
            if len(values) not in (1, 2, 3):
                _fail(f'{label}: invalid OBJ texture coordinate.')
            v = _numeric(values, len(values), label)
            if len(v) == 3 and v[2] != 0:
                _fail(f'{label}: projective texture coordinates require baking to 2D UVs.', 'unsupported_attribute')
            texcoords.append([v[0], 1 - (v[1] if len(v) > 1 else 0)])
        elif key == 'vn':
            normals.append(_numeric(values, 3, label))
        elif key in ('o', 'g'):
            if key == 'o':
                object_name = ' '.join(values) or 'Model'
            else:
                group_name = ' '.join(values)
        elif key == 'usemtl':
            material_name = ' '.join(values) or None
        elif key == 'mtllib':
            if not values:
                _fail(f'{label}: mtllib has no filename.', 'missing_resource')
            # Common exporters write an unquoted single filename with spaces.
            joined = ' '.join(values)
            if _resource_path(root, joined) in resources:
                values = [joined]
            for name in values:
                path = _resource_path(root, name)
                if path not in libraries:
                    _mtl(_resource(resources, root, name), path, resources, materials, warnings)
                    libraries.append(path)
        elif key == 's':
            if len(values) != 1:
                _fail(f'{label}: invalid smoothing group.')
            smoothing = 'off' if values[0].lower() in ('off', '0') else values[0]
        elif key == 'f':
            corners = []
            for token in values:
                fields = token.split('/')
                if len(fields) > 3 or not fields[0]:
                    _fail(f'{label}: invalid OBJ face index.')
                fields += [''] * (3 - len(fields))
                corner = []
                for value, count in zip(fields, (len(positions), len(texcoords), len(normals))):
                    if value == '':
                        corner.append(None)
                        continue
                    try:
                        index = int(value)
                    except ValueError as e:
                        raise GarageError(f'{label}: face indices must be integers.') from e
                    index = index - 1 if index > 0 else count + index if index < 0 else -1
                    if not 0 <= index < count:
                        _fail(f'{label}: face references a missing vertex, UV or normal.')
                    corner.append(index)
                corners.append(tuple(corner))
            if len(corners) > 3 and corners[0] == corners[-1]:
                corners.pop()
            if len(corners) == 3:
                a, b, c = np.asarray([positions[v[0]] for v in corners], dtype=np.float64)
                if not np.any(np.cross(b-a, c-a)):
                    empty_triangles += 1
                    continue
            triangles, face_normal = _triangulate([positions[c[0]] for c in corners], label)
            triangle_count += len(triangles)
            if triangle_count > MAX_TRIANGLES:
                _fail('OBJ exceeds the safe triangle limit.', 'model_limit')
            faces.append((object_name, group_name, material_name, smoothing, corners, triangles, face_normal))
        elif key in ('l', 'p', 'curv', 'curv2', 'surf', 'cstype', 'deg', 'bmat', 'step', 'parm', 'trim', 'hole', 'scrv', 'sp', 'end', 'con', 'vp'):
            _fail(f'{label}: OBJ {key} geometry is not a triangle surface. Convert curves, points and surfaces to a mesh first.', 'topology')
        elif key in ('call', 'csh'):
            _fail(f'{label}: executable OBJ directives are not accepted.', 'unsupported_source')
        else:
            warnings.append(f'OBJ directive {key} is not used by the rigid mesh importer.')
    if empty_triangles:
        warnings.append(f'Skipped {empty_triangles} zero-area OBJ triangles. They have no visible surface.')
    if not faces:
        _fail('The OBJ contains no polygon surfaces.', 'empty_model')
    if len(texcoords) > MAX_VERTICES * 3 or len(normals) > MAX_VERTICES * 3:
        _fail('OBJ attribute counts exceed the safe import limit.', 'model_limit')
    # Compute shared normals before splitting material/UV seams. Explicit source
    # normals remain separate even for identical positions.
    smooth_normals = {}
    for obj, group, material, smooth, corners, triangles, face_normal in faces:
        if smooth != 'off':
            for vertex in {c[0] for c in corners if c[2] is None}:
                key = (obj, smooth, vertex)
                smooth_normals[key] = smooth_normals.get(key, np.zeros(3)) + face_normal
    groups = {}
    for face_id, (obj, group, material, smooth, corners, triangles, face_normal) in enumerate(faces):
        if material is not None and material not in materials:
            _fail(f'OBJ material {material!r} has no MTL definition. Include the referenced MTL and textures in one ZIP.', 'missing_resource')
        key = (obj, group, material)
        target = groups.setdefault(key, {'vertices': {}, 'positions': [], 'uv': [], 'normals': [], 'triangles': [], 'has_uv': None})
        if len(groups) > MAX_PARTS:
            _fail('OBJ exceeds the safe part limit.', 'model_limit')
        remap = []
        for vi, ti, ni in corners:
            smoothing_key = ('normal', ni) if ni is not None else ('face', face_id) if smooth == 'off' else ('smooth', smooth)
            vertex_key = (vi, ti, smoothing_key)
            if vertex_key not in target['vertices']:
                index = len(target['positions'])
                target['vertices'][vertex_key] = index
                target['positions'].append(positions[vi])
                target['uv'].append(texcoords[ti] if ti is not None else [0, 0])
                normal = normals[ni] if ni is not None else face_normal if smooth == 'off' else smooth_normals[(obj, smooth, vi)]
                normal = np.asarray(normal, dtype=np.float64)
                normal_scale = float(np.max(np.abs(normal)))
                if normal_scale:
                    normal = normal / normal_scale
                    normal = normal / np.linalg.norm(normal)
                target['normals'].append(normal.tolist())
            remap.append(target['vertices'][vertex_key])
            if ti is None and material is not None and 'texture' in materials[material]:
                _fail(f'Textured OBJ material {material} has face corners without UVs. Supply a complete UV map before importing.', 'missing_uv')
        target['triangles'].extend(tuple(remap[x] for x in tri) for tri in triangles)
    if sum(len(g['positions']) for g in groups.values()) > MAX_VERTICES:
        _fail('OBJ needs too many vertices after preserving its normal and UV seams.', 'model_limit')
    doc = {'asset': {'version': '2.0', 'generator': 'GT6 Garage deterministic OBJ adapter'}, 'bufferViews': [], 'accessors': [], 'meshes': [], 'nodes': [], 'scenes': [{'nodes': []}], 'scene': 0, 'materials': [], 'images': [], 'textures': []}
    binary = bytearray()
    material_ids = {}
    image_ids = {}
    for material_name in dict.fromkeys(key[2] for key in groups):
        m = materials.get(material_name, {'name': 'Default', 'Kd': [.6, .6, .6], 'd': 1., 'Pr': .6, 'Pm': 0.})
        roughness = m['Pr']
        if 'Ns' in m and not m.get('explicit_roughness'):
            roughness = math.sqrt(2 / (m['Ns'] + 2))
            warnings.append('Legacy MTL shininess was approximated as PBR roughness; review highlights in the destination material.')
        pbr = {'baseColorFactor': [*m['Kd'], m['d']], 'metallicFactor': m['Pm'], 'roughnessFactor': roughness}
        dest = {'name': clean_name(m['name']), 'pbrMetallicRoughness': pbr, 'alphaMode': 'BLEND' if m['d'] < 1 else 'OPAQUE'}
        if 'texture' in m:
            data, mime, filename = m['texture']
            digest = sha256(data)
            if digest not in image_ids:
                off = _append(binary, data)
                image_ids[digest] = len(doc['images'])
                doc['images'].append({'name': clean_name(filename), 'bufferView': len(doc['bufferViews']), 'mimeType': mime})
                doc['bufferViews'].append({'buffer': 0, 'byteOffset': off, 'byteLength': len(data)})
                doc['textures'].append({'source': image_ids[digest]})
            pbr['baseColorTexture'] = {'index': image_ids[digest]}
        if 'Ke' in m:
            strength = max(1., *m['Ke'])
            dest['emissiveFactor'] = [v / strength for v in m['Ke']]
            if strength > 1:
                dest.setdefault('extensions', {})['KHR_materials_emissive_strength'] = {'emissiveStrength': strength}
                doc.setdefault('extensionsUsed', []).append('KHR_materials_emissive_strength')
        if m.get('illum') == 0:
            dest.setdefault('extensions', {})['KHR_materials_unlit'] = {}
            doc.setdefault('extensionsUsed', []).append('KHR_materials_unlit')
        if 'Ks' in m or 'Ni' in m or 'Ka' in m:
            warnings.append('Legacy MTL ambient/specular/IOR values need a native material recipe; base colour, opacity, roughness and metallic values are retained.')
        material_ids[material_name] = len(doc['materials'])
        doc['materials'].append(dest)
    def attribute(data, dtype, count_type, component):
        array = np.asarray(data, dtype=dtype)
        encoded = array.tobytes()
        off = _append(binary, encoded)
        view = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': off, 'byteLength': len(encoded)})
        accessor = len(doc['accessors'])
        record = {'bufferView': view, 'componentType': component, 'count': len(array), 'type': count_type}
        if count_type == 'VEC3' and component == 5126:
            record.update(min=array.min(axis=0).astype(float).tolist(), max=array.max(axis=0).astype(float).tolist())
        doc['accessors'].append(record)
        return accessor
    for (obj, group, material), value in groups.items():
        attrs = {'POSITION': attribute(value['positions'], '<f4', 'VEC3', 5126), 'NORMAL': attribute(value['normals'], '<f4', 'VEC3', 5126), 'TEXCOORD_0': attribute(value['uv'], '<f4', 'VEC2', 5126)}
        indices = attribute(np.asarray(value['triangles']).reshape(-1), '<u4', 'SCALAR', 5125)
        name = ' / '.join(x for x in (obj, group) if x) + (' / ' + material if material else '')
        mesh = len(doc['meshes'])
        doc['meshes'].append({'name': clean_name(name), 'primitives': [{'attributes': attrs, 'indices': indices, 'material': material_ids[material], 'mode': 4}]})
        node = len(doc['nodes'])
        doc['nodes'].append({'mesh': mesh, 'name': clean_name(name), 'extras': {'source_format': 'obj', 'source_object': obj, 'source_group': group}})
        doc['scenes'][0]['nodes'].append(node)
    if 'extensionsUsed' in doc:
        doc['extensionsUsed'] = sorted(set(doc['extensionsUsed']))
    warnings.append('OBJ has no standard unit or up-axis declaration. Coordinates were preserved; confirm Y-up orientation and physical scale before conversion.')
    return _pack_glb(doc, binary)


def _zip_resources(raw):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as e:
        raise GarageError('This is not a complete model ZIP.') from e
    resources, folded, size = {}, set(), 0
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_FILES:
            _fail('Model ZIP contains more than 4,096 entries.', 'model_limit')
        for info in entries:
            if info.flag_bits & 1:
                _fail('Encrypted model ZIPs are not supported. Save an unencrypted copy.')
            if stat.S_ISLNK(info.external_attr >> 16):
                _fail('Model ZIP contains a symbolic link. Include ordinary files only.', 'unsafe_path')
            raw_name = info.filename[:-1] if info.is_dir() else info.filename
            name = safe_rel(raw_name)
            if name.casefold() in folded:
                _fail('Model ZIP contains duplicate or case-conflicting paths.', 'unsafe_path')
            folded.add(name.casefold())
            if info.is_dir():
                continue
            size += info.file_size
            if info.file_size < 0 or size > MAX_MODEL_BYTES:
                _fail('Expanded model ZIP exceeds 256 MiB.', 'model_limit')
            try:
                data = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
                raise GarageError(f'Model ZIP resource {name} could not be read.') from e
            if len(data) != info.file_size:
                _fail('Model ZIP contains truncated resource data.')
            resources[name] = data
    return resources


def prepare_model(raw: bytes, filename: str, *, resources=None):
    """Return ``(embedded_glb_bytes, provenance_metadata)`` without side effects.

    Caller owns saving original bytes, their original filename and this metadata.
    ``source_sha256`` here identifies the original upload, while
    ``normalized_sha256`` identifies the GLB passed to existing source recipes.
    """
    if not isinstance(raw, bytes) or not raw:
        _fail('Choose a nonempty model file.')
    if len(raw) > MAX_MODEL_BYTES:
        _fail('Model input exceeds 256 MiB.', 'model_limit')
    if not isinstance(filename, str) or not filename:
        _fail('The model filename is missing.')
    basename = PurePosixPath(filename.replace('\\', '/')).name
    source_filename = clean_name(basename)
    extension = PurePosixPath(basename).suffix.lower()
    if extension not in SUPPORTED_INPUT_EXTENSIONS:
        _fail('Choose GLB, OBJ, glTF, or a ZIP containing one model and its resources. Export FBX/Blend/CAD files as GLB or OBJ first.', 'unsupported_source')
    warnings = []
    metadata = {'schema': 1, 'input_format': extension[1:], 'source_filename': source_filename, 'source_sha256': sha256(raw), 'normalized_format': 'glb', 'warnings': warnings}
    has_companions = resources is not None or extension == '.zip'
    if resources is None:
        resources = {}
    root = source_filename
    model = raw
    if extension == '.zip':
        resources = _zip_resources(raw)
        roots = [name for name in resources if PurePosixPath(name).suffix.lower() in ('.glb', '.gltf', '.obj') and not name.startswith('__MACOSX/') and not PurePosixPath(name).name.startswith('._')]
        if len(roots) != 1:
            if not roots:
                _fail('The ZIP has no GLB, glTF or OBJ model. Export one supported model first.', 'missing_model')
            _fail('The ZIP contains several models. Make a ZIP containing only the model you want to import and its resources: ' + ', '.join(sorted(roots)[:8]), 'ambiguous_model')
        root = roots[0]
        model = resources[root]
        extension = PurePosixPath(root).suffix.lower()
        metadata.update(archive_model_path=root, archive_files=len(resources), model_format=extension[1:])
    if extension == '.glb':
        try:
            GLB(model)
            result = model
        except GarageError as exc:
            if exc.code != 'external_resource' or not has_companions:
                raise
            chunks = GLB.chunks(model)
            buffers = [data for kind, data in chunks if kind == 0x004e4942]
            if len(buffers) > 1:
                _fail('GLB has multiple binary chunks.')
            result = _gltf(chunks[0][1], root, resources, warnings, buffers[0] if buffers else None)
    elif extension == '.gltf':
        result = _gltf(model, root, resources, warnings)
    else:
        result = _obj(model, root, resources, warnings)
        metadata.update(requires_axis_review=True, units='as_authored')
    # Check the common container contract, including unsupported extensions and
    # forbidden external resources, before handing data to the scene reader.
    GLB(result)
    metadata['normalized_sha256'] = sha256(result)
    metadata['warnings'] = sorted(set(warnings))
    return result, metadata


class _LocalCompanions(dict):
    """Read only the model's named, in-folder material, texture and buffer files."""
    suffixes = {'.mtl','.bin','.png','.jpg','.jpeg','.webp','.bmp','.tga','.tif','.tiff','.dds'}

    def __init__(self, root, source_size):
        super().__init__()
        self.root = root.resolve()
        self.total = source_size

    def __contains__(self, name):
        if super().__contains__(name):
            return True
        path = self.root / safe_rel(name)
        if path.suffix.lower() not in self.suffixes or not path.resolve().is_relative_to(self.root):
            _fail('Include local image, material and buffer files within the chosen model folder.', 'unsafe_path')
        if not path.is_file():
            return False
        if len(self) >= MAX_ARCHIVE_FILES - 1 or self.total + path.stat().st_size > MAX_MODEL_BYTES:
            _fail('The model and referenced files exceed the import budget.', 'model_limit')
        with path.open('rb') as stream:
            data = stream.read(MAX_MODEL_BYTES - self.total + 1)
        self.total += len(data)
        if self.total > MAX_MODEL_BYTES:
            _fail('The model and its referenced files exceed 256 MiB.', 'model_limit')
        self[name] = data
        return True


def local_model_upload(path, cancel=None):
    """Package dependencies selected through the native picker with the original model."""
    path = Path(path)
    from .util import MAX_PROJECT_BYTES
    limit = MAX_PROJECT_BYTES if path.suffix.lower() == '.gt6car' else MAX_MODEL_BYTES
    if not path.is_file() or path.stat().st_size > limit:
        _fail('Choose a model file within its import size limit.', 'model_limit')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        _fail('The input exceeds the import size limit.', 'model_limit')
    if path.suffix.lower() in ('.blend','.fbx'):
        from .blender_bridge import convert_model
        return convert_model(raw,path.name,path,cancel)
    if path.suffix.lower() in ('.zip', '.gt6car'):
        return raw, path.name
    if path.suffix.lower() == '.glb':
        try:
            GLB(raw)
            return raw, path.name
        except GarageError as exc:
            if exc.code=='unsupported_extension' and 'KHR_draco_mesh_compression' in str(exc):
                from .blender_bridge import convert_model
                return convert_model(raw,path.name,path,cancel)
            if exc.code != 'external_resource':
                raise
    companions = _LocalCompanions(path.parent, len(raw))
    prepare_model(raw, path.name, resources=companions)
    if not companions:
        return raw, path.name
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        bundle.writestr(path.name, raw)
        for name, data in sorted(companions.items()):
            bundle.writestr(name, data)
    return output.getvalue(), path.stem + '.zip'

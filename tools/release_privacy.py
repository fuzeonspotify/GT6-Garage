"""Check the staged distribution for private machine paths and account files."""
import io
import os
from pathlib import Path
import re
import struct
import zipfile
import zlib


def _bundle_entries(payload):
    # .NET 9 HostModel single-file manifest v6. Inspect compressed assemblies too.
    signature = bytes.fromhex('8b1202b96a612038727b930214d7a03213f5b9e6efae3318ee3b2dce24b36aae')
    location = payload.find(signature)
    if location < 8:
        raise ValueError('The expected packer bundle signature is missing.')
    header, = struct.unpack_from('<Q', payload, location - 8)
    table = io.BytesIO(payload)
    table.seek(header)
    major, minor, count = struct.unpack('<IIi', table.read(12))
    if (major, minor) != (6, 0) or not 0 < count < 4096:
        raise ValueError('The packer bundle manifest needs review.')

    def read_name():
        length = 0
        for shift in range(0, 28, 7):
            byte = table.read(1)[0]
            length |= (byte & 127) << shift
            if byte < 128 and length <= 4096:
                return table.read(length).decode('utf-8')
        raise ValueError('Invalid name in packer bundle.')

    read_name()  # Bundle ID
    table.seek(40, 1)  # Dependency and runtime-config locations, then flags
    for _ in range(count):
        offset, size, compressed, _kind = struct.unpack('<qqqB', table.read(25))
        name = read_name()
        stored = compressed or size
        if min(offset, size, compressed) < 0 or size > 128 * 1024**2 or offset + stored > header:
            raise ValueError('Invalid packer bundle entry.')
        data = payload[offset:offset + stored]
        if compressed:
            data = zlib.decompress(data, -15)
        if len(data) != size:
            raise ValueError('Incomplete packer bundle entry.')
        yield name, data


def _local_paths(project):
    roots = {Path.home(), Path(project).resolve()}
    if os.environ.get('USERPROFILE'):
        roots.add(Path(os.environ['USERPROFILE']))
    values = set()
    for root in roots:
        if root.parent == root:
            continue
        parts = root.as_posix()
        backslashes = parts.replace('/', '\\')
        for value in (parts, backslashes, backslashes.replace('\\', '\\\\')):
            for encoding in ('utf8', 'utf-16-le', 'utf-16-be'):
                values.add(value.encode(encoding).lower())
    return values


def check_release_tree(folder, project):
    from PyInstaller.archive.readers import CArchiveReader
    markers = _local_paths(project)
    private_names = {'.git-credentials', '.netrc', '_netrc', 'credentials.json', 'key.json',
                     'session.json', 'settings.json', 'installation.json', 'id_rsa', 'id_ed25519', 'id_ecdsa'}

    def inspect(name, payload):
        normal = name.replace('\\', '/').lower()
        relative = Path(normal)
        if (normal.startswith(('/', '../')) or (len(normal) > 1 and normal[1] == ':')
                or '.ssh' in relative.parts or '.git' in relative.parts or '..' in relative.parts
                or relative.name in private_names or relative.name == '.env' or relative.name.startswith('.env.')
                or relative.suffix in ('.key', '.p12', '.pfx', '.kdbx')):
            raise ValueError('A private account file or an absolute member name was found in the release.')
        for contents in (normal.encode(), payload.lower()):
            if any(marker in contents for marker in markers):
                raise ValueError('A build-machine path was found in the release. Rebuild the affected input in a clean location.')
        if payload.startswith(b'PK\x03\x04') and zipfile.is_zipfile(io.BytesIO(payload)):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for entry in archive.infolist():
                    inspect(entry.filename, archive.read(entry))
        if relative.name == 'gttoolssharp.exe':
            for assembly, data in _bundle_entries(payload):
                inspect(assembly, data)
                if assembly == 'GTToolsSharp.dll' or assembly.startswith('PDTools.'):
                    for match in re.finditer(rb'RSDS.{20}([^\x00]+\.pdb)\x00', data, re.S | re.I):
                        path = match[1].replace(b'\\', b'/')
                        if re.match(rb'^(?:[a-z]:/|/)', path, re.I) and not path.startswith(b'/_/'):
                            raise ValueError('A game-packer debug record includes an absolute build path.')

    for path in Path(folder).rglob('*'):
        if not path.is_file():
            continue
        inspect(path.relative_to(folder).as_posix(), path.read_bytes())
        if path.name == 'GT6Garage.exe':
            executable = CArchiveReader(str(path))
            for name, entry in executable.toc.items():
                if entry[-1] == 'z':
                    modules = executable.open_embedded_archive(name)
                    for module in modules.toc:
                        inspect(module, modules.extract(module, raw=True) or b'')
                else:
                    inspect(name, executable.extract(name) or b'')

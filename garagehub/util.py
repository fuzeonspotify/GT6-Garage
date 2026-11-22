from __future__ import annotations
import hashlib, json, os, re, tempfile, unicodedata
from pathlib import Path, PurePosixPath
from .errors import GarageError

MAX_MODEL_BYTES = 256 * 1024**2
MAX_PROJECT_BYTES = 2 * MAX_MODEL_BYTES + 20 * 1024**2

def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def hash_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def clean_name(value: str, fallback='Untitled car') -> str:
    value = unicodedata.normalize('NFKC', str(value))
    value = ''.join(c for c in value if c.isprintable())[:120].strip()
    return value or fallback

def safe_slug(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '_', clean_name(value)).strip('_')[:64] or 'car'

def safe_rel(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise GarageError('Unsafe path in package.', 'unsafe_path')
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ('..','.') for x in value.split('/')) or any(not x for x in value.split('/')):
        raise GarageError('Unsafe path in package.', 'unsafe_path')
    # Windows aliases and device names are rejected even when validating on Linux.
    for c in p.parts:
        if c[-1:] in (' ', '.') or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', c, re.I):
            raise GarageError('Reserved path in package.', 'unsafe_path')
    return p.as_posix()

def inside(root: Path, rel: str) -> Path:
    rel=safe_rel(rel); p=root.joinpath(*rel.split('/'))
    if root.resolve() not in p.resolve().parents:
        raise GarageError('Path leaves the project folder.', 'unsafe_path')
    return p

def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):os.unlink(name)

def atomic_json(path: Path, obj):
    atomic_bytes(path, (json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8'))

def read_json(path: Path, max_bytes=16*1024**2):
    if path.stat().st_size>max_bytes:raise GarageError('JSON file is too large.', 'file_limit')
    try:return json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, UnicodeError) as e:raise GarageError('Invalid JSON file.', 'invalid_json') from e

def unique_path(parent: Path, name: str) -> Path:
    p=parent/name
    for i in range(1,10000):
        if not p.exists():return p
        p=parent/(Path(name).stem+f'_{i}'+Path(name).suffix)
    raise GarageError('Too many files with the same name. Choose another output folder.')

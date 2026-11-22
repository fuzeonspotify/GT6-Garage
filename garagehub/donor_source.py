"""Read five small disc ranges and one update file from the player's own game."""
from __future__ import annotations

import ftplib
import hashlib
import json
from pathlib import Path
import shutil

from .car_pack import SEED, execute, trusted_packer
from .errors import GarageError
from .stock_setup import RESOURCES, find_update
from .util import hash_file

DISC = '/dev_bdvd/PS3_GAME/USRDIR/GT.VOL'
UPDATE = '/dev_hdd0/game/BCES01893/USRDIR/PDIPFS'
MODEL = 'car/0004/0006'


def layout():
    return json.loads((RESOURCES / 'donor_ranges.json').read_bytes())


def check_stock(root):
    for name, row in layout()['assets'].items():
        file = Path(root) / name
        if not file.is_file() or file.is_symlink() or hash_file(file) != row['sha256']:
            raise GarageError(f'The original 1.22 Supra {name} is missing or different. Check the donor source.')
    return Path(root)


def ftp_reader(host, port=21, user='anonymous', password='anonymous@'):
    """Close each bounded transfer with its control socket before starting another."""
    def read(path, offset, length):
        client = ftplib.FTP(timeout=25)
        try:
            client.connect(host, port)
            client.login(user, password)
            client.voidcmd('TYPE I')
            with client.transfercmd('RETR ' + path, rest=offset or None) as data:
                result = bytearray()
                while len(result) < length:
                    block = data.recv(min(65536, length - len(result)))
                    if not block:
                        break
                    result.extend(block)
                return bytes(result)
        finally:
            client.close()
    return read


def extract_donor(update, work, packer, *, disc=None, read_ftp=None, progress=print):
    """Make a temporary, six-file PDIPFS view using the retail 1.22 TOC."""
    update = find_update(update)
    trusted_packer(packer)
    spec = layout()
    work = Path(work)
    staged = work / 'PDIPFS'
    for name, digest in SEED.items():
        target = staged / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(update / name, target)
        if hash_file(target) != digest:
            raise GarageError(f'PDIPFS/{name} differs from stock European GT6 1.22.')

    def read(path, offset, length):
        if read_ftp:
            return read_ftp(path, offset, length)
        file = Path(disc) if path == DISC else update / path.removeprefix(UPDATE + '/')
        with file.open('rb') as stream:
            stream.seek(offset)
            return stream.read(length)

    progress('Checking the European retail disc header')
    if hashlib.sha256(read(DISC, 0, 2048)).hexdigest() != spec['header_sha256']:
        raise GarageError('The disc header differs from the supported European retail pressing.')
    for name, row in spec['assets'].items():
        progress('Reading original ' + name)
        if 'offset' in row:
            block = read(DISC, row['offset'], row['bytes'])
        else:
            block = read(UPDATE + '/' + row['packed'], 0, row['bytes'])
        if hashlib.sha256(block).hexdigest() != row['packed_sha256']:
            raise GarageError(f'The downloaded {name} differs. Check the game version and FTP resume support.')
        file = staged / row['packed']
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(block)
    destination = work / 'original'
    indices = [str(row['index']) for row in spec['assets'].values()]
    execute(packer, ['unpack', '-i', staged, '-o', destination, '--indices', *indices],
            work / 'logs', 'donor.log', None)
    return check_stock(destination / MODEL)

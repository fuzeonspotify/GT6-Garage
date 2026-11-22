"""Build a public Windows ZIP from an explicit allowlist. No local game assets."""
import argparse
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from garagehub import __version__
from garagehub.car_pack import trusted_packer
from tools.build_blender_addon import FILENAME as ADDON_FILENAME, package_addon


def package(frozen, packer, output):
    trusted_packer(packer)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='garage-release-') as tmp:
        folder = Path(tmp)
        app = folder / 'GT6 Garage'
        shutil.copytree(frozen, app)
        package_addon(app/ADDON_FILENAME)
        shutil.copytree(ROOT / 'third_party/GTToolsSharp', app / 'Support/GTToolsSharp')
        shutil.copyfile(packer, app / 'Support/GTToolsSharp/GTToolsSharp.exe')
        for name in ('LICENSE', 'README.md', 'THIRD_PARTY.md'):
            shutil.copyfile(ROOT / name, app / name)
        shutil.copytree(ROOT / 'docs', app / 'docs', ignore=shutil.ignore_patterns('evidence', 'engineering_history.json'))
        shutil.copyfile(ROOT / 'resources/template-credits.txt', app / 'MODEL_CREDITS.txt')
        (app / 'START HERE.txt').write_text(
            'GT6 Garage ' + __version__ + '\n\nExtract the entire ZIP. Double-click GT6Garage.exe.\n'
            'First time? Choose Prepare car template. Have an older kit? Choose Find car template.\n'
            'Next choose Use my stock PDIPFS and select your five-file update folder.\n'
            'Blender users can install the included GT6-Garage-Blender ZIP from Blender Preferences. See docs/blender-addon.md.\n'
            'For PS3 FTP installation, see docs/player-guide.md or open:\n'
            'https://github.com/fuzeonspotify/GT6-Garage/blob/main/docs/player-guide.md\n', encoding='utf-8')
        # Offline guide links stay local. Implementation links open the tagged source.
        for guide in list(app.glob('*.md')) + list((app / 'docs').rglob('*.md')):
            def public_link(match):
                target = match.group(1)
                relative = target.split('#', 1)[0]
                if not relative or '://' in target or (guide.parent / relative).exists():
                    return match.group(0)
                repository_path = ((ROOT / guide.relative_to(app)).parent / relative).resolve()
                if not repository_path.is_relative_to(ROOT) or not repository_path.exists():
                    raise ValueError(f'Broken guide link in {guide.name}: {target}')
                view = 'tree' if repository_path.is_dir() else 'blob'
                anchor = target[len(relative):]
                url = f'https://github.com/fuzeonspotify/GT6-Garage/{view}/v{__version__}/'
                return '](' + url + repository_path.relative_to(ROOT).as_posix() + anchor + ')'
            guide.write_text(re.sub(r'\]\(([^)]+)\)', public_link, guide.read_text(encoding='utf-8')), encoding='utf-8')
        archive = folder / 'public.zip'
        forbidden = {'gt.vol', 'db0106.dat', 'eboot.bin', 'rpcs3.exe', 'param.sfo', 'key.json',
                     'settings.json', 'session.json', 'carname.dat', 'body_s', 'wheel', 'body', 'interior'}
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(app.rglob('*')):
                if p.is_file():
                    rel = p.relative_to(folder)
                    if p.is_symlink() or (p.name.lower() in forbidden and not (p.name == 'WHEEL' and p.parent.name.endswith('.dist-info'))) or any(
                            s.lower() in {'pdipfs', 'stock-authoring', 'starting package', '__pycache__'} for s in rel.parts):
                        raise RuntimeError(f'Unexpected content in public executable distribution: {rel}')
                    z.write(p, rel.as_posix())
        with zipfile.ZipFile(archive) as z:
            if z.testzip() or 'GT6 Garage/GT6Garage.exe' not in z.namelist():
                raise RuntimeError('Release ZIP did not verify.')
        os.replace(archive, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(digest, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packer', type=Path, default=ROOT / 'Support/GTToolsSharp/GTToolsSharp.exe')
    parser.add_argument('--frozen', type=Path, help='Package an already-built clean player directory.')
    parser.add_argument('--output', type=Path, default=ROOT / f'dist/GT6-Garage-{__version__}-Windows-x64.zip')
    a = parser.parse_args()
    if a.frozen:
        package(a.frozen.resolve(), a.packer.resolve(), a.output.resolve())
        return
    with tempfile.TemporaryDirectory(prefix='gt6-player-build-') as tmp:
        work = Path(tmp)
        env = {**os.environ, 'GT6_GARAGE_PLAYER_BUILD':'1', 'PYTHONDONTWRITEBYTECODE':'1'}
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--workpath', str(work / 'work'),
                        '--distpath', str(work / 'dist'), 'GT6Garage.spec'], cwd=ROOT, env=env, check=True)
        package(work / 'dist/GT6Garage', a.packer.resolve(), a.output.resolve())


if __name__ == '__main__':
    main()

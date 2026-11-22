"""Build and pin this checkout's append-only GTToolsSharp for Windows x64."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '8ad944caf9c1c9c113a070a20bbf668cddc91315'
PDTOOLS = '2a9637d8c36100e4c211d73cd8140a9d690578d2'


def main():
    patch = ROOT / 'third_party/GTToolsSharp/always_append.patch'
    with tempfile.TemporaryDirectory(prefix='gt6-packer-') as temp:
        src = Path(temp) / 'source'
        subprocess.run(['git', 'clone', '--no-checkout', 'https://github.com/Nenkai/GTToolsSharp.git', str(src)], check=True)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(src), *args], text=True).strip()
        git('checkout', '--detach', SOURCE)
        git('submodule', 'update', '--init', '--recursive')
        if git('rev-parse', 'HEAD') != SOURCE or git('-C', 'PDTools', 'rev-parse', 'HEAD') != PDTOOLS:
            raise RuntimeError('Upstream revisions differ from the reviewed combination.')
        git('apply', '--check', str(patch))
        git('apply', str(patch))
        build = Path(temp) / 'publish'
        subprocess.run(['dotnet', 'publish', str(src / 'GTToolsSharp/GTToolsSharp.csproj'),
                        '-c', 'Release', '-r', 'win-x64', '--self-contained', 'true',
                        '-o', str(build), '-p:PublishSingleFile=true', '-p:IncludeNativeLibrariesForSelfExtract=true',
                        '-p:EnableCompressionInSingleFile=true', '-m:2', '-nr:false'], check=True)
        exe = build / 'GTToolsSharp.exe'
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        support = ROOT / 'Support/GTToolsSharp'
        support.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, support / exe.name)
        source_id = ROOT / 'garagehub/packer_build_id.py'
        source_id.write_text('"""SHA-256 of the packer compiled for this distribution."""\n'
                             + f"PACKER_SHA = '{digest}'\n", encoding='utf-8')
        print('Built and pinned append-only GTToolsSharp', digest)


if __name__ == '__main__':
    main()

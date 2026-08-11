# sierra_patcher.spec
# Build from the project root (the folder that contains `sierra_patcher/`, `bin/`, etc.):
#   pyinstaller sierra_patcher.spec
#
# Output: dist/sierra-patcher.exe
# This spec builds the public GUI onefile executable. Package transport data
# stays external as manifests/objects; runtime patching uses only zstd.exe.

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files


def _project_root():
    try:
        return os.path.abspath(os.path.dirname(__file__))
    except NameError:
        return os.path.abspath(os.getcwd())


PR = _project_root()


def P(*parts):
    return os.path.join(PR, *parts)


# Build metadata is generated from sierra_patcher.__version__ so the Windows
# VERSIONINFO resource stays in sync with the application's canonical version.
from windows_version_info import build_version_info

version_info = build_version_info(PR)

block_cipher = None

binaries = []


def _add_bin(src, dest):
    if os.path.exists(src):
        binaries.append((src, dest))


# Zstd is the only external package-processing executable in current builds.
_add_bin(P('bin', 'zstd64', 'zstd.exe'), os.path.join('bin', 'zstd64'))

datas = []


def _add_data(src, dest):
    if os.path.exists(src):
        datas.append((src, dest))


_add_data(P('sierra_patcher', 'assets', 'title.ico'), os.path.join('sierra_patcher', 'assets'))
datas += collect_data_files('sierra_patcher', includes=['assets/*'])

if os.path.exists(P('title.ico')):
    datas.append((P('title.ico'), '.'))

hiddenimports = (
    collect_submodules('sierra_patcher') + [
        'tkinter',
        'win32timezone',
        'PIL', 'PIL.Image', 'PIL.ImageTk',
    ]
)

icon_path = P('sierra_patcher', 'assets', 'title.ico')
if not os.path.exists(icon_path):
    icon_path = P('title.ico') if os.path.exists(P('title.ico')) else None

a = Analysis(
    [P('sierra_patcher', 'main.py')],
    pathex=[PR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='sierra-patcher',
    icon=icon_path,
    version=version_info,
    console=False,
    debug=False,
    strip=False,
    upx=False,
    upx_exclude=[],
)

# sierra_patcher.spec
# Build from the project root (the folder that contains `sierra_patcher/`, `bin/`, etc.):
#   pyinstaller sierra_patcher.spec
#
# Output: dist/sierra-patcher.exe
# This spec builds the public GUI onefile executable. Patch data stays external
# beside the exe in patchfiles/ and storage/.

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# --- robust project root (no reliance on __file__) ---
def _project_root():
    try:
        return os.path.abspath(os.path.dirname(__file__))
    except NameError:
        return os.path.abspath(os.getcwd())

PR = _project_root()
def P(*parts):  # join helper
    return os.path.join(PR, *parts)

block_cipher = None

# --- external tools (only add if they exist) ---
binaries = []
def _add_bin(src, dest):
    if os.path.exists(src):
        binaries.append((src, dest))

_add_bin(P('bin', '7za.exe'), 'bin')
_add_bin(P('bin', 'zstd64', 'zstd.exe'), os.path.join('bin', 'zstd64'))

# --- assets / data files ---
datas = []
def _add_data(src, dest):
    if os.path.exists(src):
        datas.append((src, dest))
_add_data(P('sierra_patcher', 'assets', 'title.ico'), os.path.join('sierra_patcher', 'assets'))
# package assets (icons, images, etc.)
datas += collect_data_files('sierra_patcher', includes=['assets/*'])

# optional top-level icon (e.g., project_root/title.ico)
if os.path.exists(P('title.ico')):
    datas.append((P('title.ico'), '.'))

# --- hidden imports ---
hiddenimports = (
    collect_submodules('sierra_patcher') + [
        'tkinter',
        'win32timezone',
        # Pillow bits used for logo rendering
        'PIL', 'PIL.Image', 'PIL.ImageTk',
    ]
)

# choose an icon if present
icon_path = P('sierra_patcher', 'assets', 'title.ico')
if not os.path.exists(icon_path):
    icon_path = P('title.ico') if os.path.exists(P('title.ico')) else None

a = Analysis(
    [P('sierra_patcher', 'main.py')],   # 
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
    icon=icon_path,          # None if not found
    console=False,            # public GUI build; packaged CLI output is hidden
    debug=False,
    strip=False,
    upx=False,                # avoid packed binaries; friendlier for AV scans
    upx_exclude=[],
)

# sierra_patcher.spec
# Usage:
#   pyinstaller sierra_patcher.spec
# Produces a single executable: dist/sierra-patcher.exe
#
# Repo layout (expected):
#   sierra_patcher/        # package with cli.py and modules
#   bin/7za.exe
#   bin/zstd64/zstd.exe
#
# NOTE:
# - This is a ONEFILE build. If you prefer ONEDIR, see the commented
#   COLLECT section at the bottom and remove onefile=True in EXE.

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

project_root = os.path.abspath(os.path.dirname(__file__))

block_cipher = None

# Bundle external tools used at runtime
binaries = [
    (os.path.join('bin', '7za.exe'), 'bin'),
    (os.path.join('bin', 'zstd64', 'zstd.exe'), os.path.join('bin', 'zstd64')),
]

# Include any data files the package may need at runtime (e.g., templates, txt/info)
datas = []
datas += collect_data_files('sierra_patcher', includes=['**/*.txt', '**/*.info'], excludes=[])

# Ensure all package modules are discovered (safe blanket include)
hiddenimports = collect_submodules('sierra_patcher') + [
    # pywin32 timezone helper sometimes required
    'win32timezone',
    # tkinter ensures file dialogs work in frozen app
    'tkinter',
]

a = Analysis(
    ['-m', 'sierra_patcher.cli'],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ONEFILE build
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='sierra-patcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,          # set to False if you want a windowed app without console
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=title.ico,             # add an .ico path here if you want a custom icon
)

# --- ONEDIR alternative ---
# If you prefer an ONEDIR build instead of a single .exe, comment out the EXE above
# and use the COLLECT pipeline below; also run `pyinstaller --noconfirm sierra_patcher.spec`.
# 
# coll = COLLECT(
#     exe,
#     a.binaries,
#     a.zipfiles,
#     a.datas,
#     strip=False,
#     upx=True,
#     upx_exclude=[],
#     name='sierra-patcher',
# )

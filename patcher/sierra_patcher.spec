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

binaries = [
    ('bin/7za.exe', 'bin'),
    ('bin/zstd64/zstd.exe', 'bin/zstd64'),
]

hiddenimports = [
    'win32timezone',
    'tkinter',
]

# If you collect package data elsewhere, keep that too

a = Analysis(
    ['-m', 'sierra_patcher.main'],
    pathex=['.'],
    binaries=binaries,
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='sierra-patcher',
    console=True,         # keep console for CLI; GUI hides it at runtime
    debug=False, strip=False, upx=True,
)


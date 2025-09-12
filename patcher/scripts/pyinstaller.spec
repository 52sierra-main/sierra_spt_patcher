

---


## scripts/pyinstaller.spec (example, optional)
```python
# Minimal example; adjust paths as needed
block_cipher = None


a = Analysis(['-m', 'sierra_patcher.cli'],
pathex=['.'],
binaries=[],
datas=[('bin/7za.exe', 'bin'), ('bin/zstd64/zstd.exe', 'bin/zstd64')],
hiddenimports=[],
hookspath=[],
runtime_hooks=[],
excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name='sierra-patcher', console=True)

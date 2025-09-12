# Sierra Patcher


Unified toolkit to **generate** ZSTD patch packages and **install** them.


## Usage


```bash
# Generator (developer side)
sierra-patcher generate --source "C:/Battlestate Games/EFT" \
--dest "C:/patch_workspace/3.10/target" \
--title "SPT 3.10" --date "2025-09-11"


# Installer (user side)
sierra-patcher install --dir "D:/Games/TarkovCopy" --prereqs -y

# Sierra Installer — Simple User Guide

This guide is for normal users installing SPT with **Sierra Installer 0.2.0**.

> **Important:** Never install SPT directly into your official Live Tarkov folder.

---

## 1. Before you start

1. Make sure official Escape from Tarkov is installed and up to date.
2. Close Tarkov, the BSG launcher, SPT, and other programs using the game folder.
3. Choose a new or empty folder for SPT.
4. Make sure the destination drive has enough free space.

Example:

```text
Official Live Tarkov:
C:\Games\EscapeFromTarkov

New SPT folder:
C:\Games\SPT
```

Use **Automatic copy (recommended)** whenever Sierra can detect Live Tarkov. If detection is unavailable, make a separate copy yourself and choose **Use existing copy**.

---

## 2. Normal installation — Web release

1. Open **Sierra Installer**.
2. Stay on the **Install** tab.
3. Leave **Source** on **Web release**.
4. Wait for the available versions to load.
5. Choose the SPT version you want.
6. Leave **Automatic copy (recommended)** selected and choose a new/empty SPT folder.
7. Click **Install SPT** **once**.
8. Wait until the status says **Done**.

For a current Web release, Automatic Copy works like this:

```text
Check release/version compatibility
        ↓
Fetch the small storage/integrity data
        ↓
Verify official Live Tarkov files
        ↓
Copy Live Tarkov → new SPT folder
        ↓
Verify the copied SPT folder again
        ↓
Download the rest of the release
        ↓
Apply patches and finish installation
```

The second verification is intentional. It catches a file that was missed, changed, blocked, or corrupted while being copied before Sierra starts patching it.

With **Use existing copy**, Sierra verifies the selected copy directly before downloading the rest of the release.

The source-file check can take a minute or two because Sierra reads every file that will be used as delta input. That is normal.

---

## 3. Installing from an Archived snapshot

An **Archived snapshot** is an offline/local copy of a Sierra release.

1. Keep the whole snapshot together. Do not remove files inside `objects` or `releases`.
2. Run the included Sierra Installer or select **Archived snapshot**.
3. Use **Automatic copy (recommended)** with a new SPT folder, or select a separate fresh copy under **Use existing copy**.
4. Click **Install SPT** once.
5. Wait for verification and installation to finish.

Archived snapshots may spend several minutes on:

```text
Verifying archived objects
```

This is normal, especially on an HDD.

---

# If something does not work

## The version list does not load / catalogue download fails

1. Close and reopen Sierra and try again.
2. Check your internet connection.
3. Try another network if possible.
4. Try a VPN.

If another network or VPN fixes it, the problem is probably somewhere along the route between you and the download service.

If it still fails, send the session log to support.

---

## Version mismatch

Do **not** use Force just to get past this warning.

1. Update/verify official Live Tarkov.
2. Use a new/empty SPT destination for Automatic Copy, or make a fresh manual copy.
3. Try again.

If the selected SPT release requires a different Live Tarkov build than the one currently available, you may need to wait for the matching patch release.

---

## "Source files mismatch"

This means exact source-file verification failed. Sierra may show:

```text
Source files mismatch
Checked: 5397
Matched: 1569
Mismatched: 3828
```

For current Web releases this happens **before patching starts**.

- If the official Live source fails verification, Sierra stops before copying/patching it.
- If an existing copy fails verification, Sierra does not patch it.
- If Automatic Copy succeeds but the **copied destination** fails its second verification, files may already have been copied into the new SPT folder, but **no patches were applied**. Delete that destination and try again with a new/empty folder.

Do not repeatedly retry the same bad destination.

---

## Zstd / checksum / corruption error

Possible diagnostic codes include:

```text
ZSTD_CHECKSUM_MISMATCH
ZSTD_CORRUPTION
ZSTD_IO
```

- **ZSTD_CHECKSUM_MISMATCH** is deterministic for the same source + delta and is not retried.
- **ZSTD_CORRUPTION** is also treated as deterministic, but does not by itself prove that your Tarkov source is the cause; patch/package data can also be involved.
- **ZSTD_IO** can be temporary (for example antivirus/indexer/file-lock interference), so Sierra may retry it automatically.

If 25 deterministic patch failures are reached, Sierra stops the remaining patch work to avoid damaging more files.

> If **Applying patches** had already started, do not reuse that destination. Delete it and start from a fresh copy/new Automatic Copy destination.

If the same failure happens again from a fresh source, send the session log to support.

---

## .NET error / SPT.Server will not start

Sierra records the runtime requirements of newer releases and warns when required Microsoft .NET/ASP.NET components are missing.

1. Install the **x64 runtime family and minimum version shown by Sierra**.
2. Restart Sierra/SPT after installation.

A newer major .NET version does not automatically replace every older runtime family.

---

## Installation looks stuck

Check the current stage and the **Logs** tab. Some stages can take time, especially:

```text
Preparing installation
Verifying source files
Copying Live game
Verifying archived objects
Reconstructing package
```

Do not repeatedly press **Install SPT**. Sierra blocks duplicate install workers, but the current operation still needs time to finish or fail.

---

## Download fails part-way through

For a download/package-preparation failure, reopening Sierra and retrying is normally safe because completed cache objects can be reused.

If the failure happened **after `Applying patches` started**, do not reuse the destination folder.

---

## What does Force do?

**Force is not a normal installation option.**

Force can bypass heuristic checks such as:

- the version-number warning
- the rough folder-size check

**Force cannot bypass exact source-file SHA-256 verification.** If those source bytes do not match, the delta cannot be safely applied.

Only use Force when you understand the warning or support specifically asks you to.

---

# Safest rule after a patching failure

> **If `Applying patches` had already started, delete that destination and start from a fresh copy before trying again.**

Do not try to repair a half-patched folder by repeatedly running Sierra on it.

---

# What to send when asking for help

The most useful item is the **session log**.

Open **Logs** and click **Save log to file...**. Sierra also has **Copy log** and **Open log folder** buttons and keeps the latest 10 session logs.

The log records useful support information such as system details, selected release, destination, worker settings, Force state, install mode, detected Live source/version, integrity results, and errors.

> Logs can contain local file paths and system information. Review them before posting them publicly. Never send passwords or account credentials.

If you cannot send the log, include:

- a screenshot of the error
- the SPT version selected
- Web release or Archived snapshot
- Automatic Copy or Use existing copy
- whether Force was enabled
- whether another network/VPN changes a download problem

Support:

- Discord: https://discord.gg/uKMW8PxE8s
- Email: sierra@52sierra.net

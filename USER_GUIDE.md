# Sierra Installer — Simple User Guide

This guide is for normal users installing SPT with **Sierra Installer 0.2.0**.

> **Important:** Do **not** install SPT directly into your official Live Tarkov folder. Always make a separate copy first.

---

## 1. Before you start

1. Make sure your official Escape from Tarkov installation is installed and up to date.
2. Close Escape from Tarkov, the Tarkov launcher, SPT, and any other program using the game folder.
3. Make a **fresh copy of the entire official Tarkov folder** to a new location.

Example:

```text
Official Live Tarkov:
C:\Games\EscapeFromTarkov

Fresh copy for SPT:
C:\Games\SPT
```

The copied folder should contain `EscapeFromTarkov.exe`.

**Use the copied folder as the Sierra Installer destination. Do not select the official Live folder.**

Make sure the drive has enough free space for the copied game and the installation process.

---

## 2. Normal installation — Web release

1. Open **Sierra Installer**.
2. Stay on the **Install** tab.
3. Make sure **Source** is set to **Web release**.
4. Wait for the available versions to load.
5. Choose the SPT version you want to install.
6. Under **Destination to patch**, select the **fresh Tarkov copy** you made earlier.
7. Click **Install SPT** **once**.
8. Wait for the installer to finish.

The installer may spend some time on stages such as:

```text
Preparing installation
Downloading objects
Reconstructing package
Verifying source files
Applying patches
Applying storage
```

This can take a while, especially on slower drives. **Do not close Sierra Installer while it is working.**

When the status shows **Done**, open the completed SPT folder and use the SPT launcher normally.

---

## 3. Installing from an Archived snapshot

An **Archived snapshot** is an offline/local copy of a Sierra release.

1. Keep the entire Archived snapshot folder together. Do not delete or move files inside its `objects` or `releases` folders.
2. Run the Sierra Installer executable included with the Archived snapshot, or select **Archived snapshot** as the source.
3. Select a **fresh copy of Tarkov** as the destination.
4. Click **Install SPT** once.
5. Wait for the archive verification and installation to finish.

Archived snapshots may spend several minutes on:

```text
Verifying archived objects
```

This is normal, especially when the snapshot is stored on an HDD.

---

# If something does not work

## The version list does not load / catalogue download fails

If Sierra says it could not load the available versions, or you see a connection/reset error:

1. Close and reopen Sierra Installer and try again.
2. Check that your internet connection is working.
3. Try another network if one is available.
4. Try a VPN.

If the installer works through another network or VPN, the problem is probably somewhere along the network route between you and the download service.

If it still fails, send the error from the **Logs** tab to support.

---

## Version mismatch

Do **not** use Force just to get past this message.

Instead:

1. Update or verify your official Live Tarkov installation.
2. Delete the SPT copy you were trying to use.
3. Make a **new copy** from the updated Live Tarkov folder.
4. Select the new copy in Sierra Installer and try again.

---

## "Source files mismatch"

This means the Tarkov copy you selected is not the exact source expected by that patch.

Sierra may show something like:

```text
Source files mismatch
Checked: 5397
Matched: 1569
Mismatched: 3828
```

Do not continue using that copy.

1. Delete the failed SPT destination folder.
2. Verify/update the official Live Tarkov installation.
3. Make a **fresh copy** of Live Tarkov.
4. Run the installer again using the new copy.

Do **not** repeatedly retry the same failed destination.

---

## Zstd / checksum / "Data corruption detected" error

Examples include:

```text
Restored data doesn't match checksum
Data corruption detected
ZSTD_FAILURE
```

This usually means the selected Tarkov copy does not match the files the patch expects.

**If the installer had already started Applying patches, do not run the installer again on the same folder.** Some files may already have been changed.

Delete that destination, make a fresh Tarkov copy, and try again.

If a fresh copy fails in exactly the same way, send the Logs to support.

---

## .NET error / SPT.Server will not start

If Sierra or `SPT.Server.exe` says a Microsoft .NET component is missing:

1. Read the error carefully.
2. Install the **x64 .NET / ASP.NET runtime version requested by the error or Sierra warning**.
3. Restart Sierra Installer or SPT after installation.

Do not assume that having a newer major .NET version automatically replaces the requested version.

If you are unsure, send a screenshot of the .NET error to support.

---

## Installation looks stuck

First check the current stage and the **Logs** tab.

Some stages can take time without immediately showing a large amount of progress, especially:

```text
Preparing installation
Verifying archived objects
Verifying source files
Reconstructing package
```

Do not repeatedly press **Install SPT**. Sierra prevents multiple installations from starting at the same time, but you should still wait for the current operation to finish or fail.

If there has been no new log activity for a long time, copy the Logs and contact support.

---

## Download fails part-way through

For a download/package preparation failure, you can normally reopen Sierra and try the download again because verified cache files can be reused.

However, if the failure happened **after `Applying patches` started**, do not reuse the destination folder. Make a fresh Tarkov copy first.

---

## What does Force do?

**Force bypasses compatibility/safety checks. It is not a normal installation option.**

Using Force with the wrong Tarkov files can result in a broken or partially patched installation.

Only use **Force** when you understand why a check is failing or when support specifically asks you to use it.

---

# The safest rule after any patching failure

> **If `Applying patches` had already started, throw away that destination folder and make a fresh copy of Live Tarkov before trying again.**

Do not try to repair a half-patched folder by running Sierra Installer repeatedly.

---

# What to send when asking for help

Please include:

- A screenshot of the error.
- The contents of the **Logs** tab, preferably from the beginning of the failure.
- The SPT version you selected.
- Whether you used **Web release** or **Archived snapshot**.
- Whether the destination was a **fresh copy of Live Tarkov**.
- Whether another network or VPN changes the problem, if the failure is download-related.

Support:

- Discord: https://discord.gg/uKMW8PxE8s
- Email: sierra@52sierra.net

Please do not send passwords, account credentials, or other private information.

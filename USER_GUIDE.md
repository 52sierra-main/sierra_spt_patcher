# Sierra Installer — Simple User Guide

This guide is for normal users installing SPT with **Sierra Installer 0.2.0**.

> **Important:** Do **not** install SPT directly into your official Live Tarkov folder. Use automatic copy or a separate copy.

---

## 1. Before you start

1. Make sure your official Escape from Tarkov installation is installed and up to date.
2. Close Escape from Tarkov, the Tarkov launcher, SPT, and any other program using the game folder.
3. Prepare a new or empty folder for SPT. Sierra Installer can copy the official Tarkov folder automatically.

Example:

```text
Official Live Tarkov:
C:\Games\EscapeFromTarkov

Fresh copy for SPT:
C:\Games\SPT
```

The copied folder should contain `EscapeFromTarkov.exe`.

If automatic detection is unavailable, make a fresh copy yourself and select **Use existing copy**. Never select the official Live folder as the destination.

Make sure the drive has enough free space for the copied game and the installation process.

---

## 2. Normal installation — Web release

1. Open **Sierra Installer**.
2. Stay on the **Install** tab.
3. Make sure **Source** is set to **Web release**.
4. Wait for the available versions to load.
5. Choose the SPT version you want to install.
6. Leave **Automatic copy (recommended)** selected and choose the new SPT folder. To use a manual copy, select **Use existing copy** instead.
7. Click **Install SPT** **once**.
8. Wait for the installer to finish.

The installer may spend some time on stages such as:

```text
Preparing installation
Checking your Tarkov copy
Verifying source files
Downloading objects
Reconstructing package
Applying patches
Applying storage
```

**Sierra Installer checks your Tarkov copy before it downloads anything.** It only needs
about 5 MB to do this. If your copy is not the right one, it stops there and
tells you, without downloading the whole release and without changing a single
file in your folder.

The check itself takes a minute or two, because Sierra reads every file the
patch needs. That is normal, even though nothing appears to be downloading.

This can take a while, especially on slower drives. **Do not close Sierra Installer while it is working.**

When the status shows **Done**, open the completed SPT folder and use the SPT launcher normally.

---

## 3. Installing from an Archived snapshot

An **Archived snapshot** is an offline/local copy of a Sierra release.

1. Keep the entire Archived snapshot folder together. Do not delete or move files inside its `objects` or `releases` folders.
2. Run the Sierra Installer executable included with the Archived snapshot, or select **Archived snapshot** as the source.
3. Use **Automatic copy (recommended)** with a new SPT folder, or select a fresh Tarkov copy under **Use existing copy**.
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

**Nothing in your folder was changed.** This check runs before Sierra downloads
the release and before it touches any of your files, so you have lost nothing but
a couple of minutes.

Do not continue using that copy.

1. Delete the SPT destination folder you selected.
2. Verify/update the official Live Tarkov installation.
3. Make a **fresh copy** of Live Tarkov.
4. Run the installer again using the new copy.

Do **not** repeatedly retry the same failed destination. The result will be
exactly the same every time.

---

## Zstd / checksum / "Data corruption detected" error

Examples include:

```text
Restored data doesn't match checksum
Data corruption detected
ZSTD_FAILURE
```

This usually means the selected Tarkov copy does not match the files the patch expects.

This should now be rare, because Sierra checks your files before it starts
patching. If you see it anyway, something unusual happened and support wants to
know about it.

Sierra also stops on its own after 25 of these errors instead of continuing
through thousands of files, so the damage is kept small.

**If the installer had already started Applying patches, do not run the installer again on the same folder.** Some files may already have been changed.

Delete that destination, make a fresh Tarkov copy, and try again.

If a fresh copy fails in exactly the same way, send your log file to support.

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

Note that if your Tarkov copy was the problem, Sierra will have told you before
the download even started, so a download failure is a separate issue.

However, if the failure happened **after `Applying patches` started**, do not reuse the destination folder. Make a fresh Tarkov copy first.

---

## What does Force do?

**Force is not a normal installation option.** You should not need it.

Force skips two checks that can sometimes be wrong:

- the **version number** check, because Tarkov's version number does not always change when the game files do
- the **folder size** check, which is only a rough guess

**Force does not skip the file check.** Sierra always compares your files against the exact files the patch needs, and Force cannot turn that off. This is on
purpose: if those files do not match, the patch physically cannot work, so letting you continue would only break your folder.

In short: Force can get you past a warning that might be a false alarm. It cannot get you past a real problem, and it will not make a wrong Tarkov copy work.

Only use **Force** when you understand why a check is failing, or when support asks you to.

---

# The safest rule after any patching failure

> **If `Applying patches` had already started, throw away that destination folder and make a fresh copy of Live Tarkov before trying again.**

Do not try to repair a half-patched folder by running Sierra Installer repeatedly.

---

# What to send when asking for help

**The easiest and most useful thing you can send is the log file.**

Go to the **Logs** tab and click **Save log to file...**, then send us that file.
It already contains your PC details, the version you chose, the folder you
selected, your settings, and everything Sierra did, with times. That answers
almost every question we would otherwise have to ask you.

There is also an **Open log folder** button if you would rather find the files
yourself. Sierra keeps the last 10.

If you cannot send the file, please include:

- A screenshot of the error.
- The SPT version you selected.
- Whether you used **Web release** or **Archived snapshot**.
- Whether the destination was a **fresh copy of Live Tarkov**.
- Whether **Force** was ticked.
- Whether another network or VPN changes the problem, if the failure is download-related.

Support:

- Discord: https://discord.gg/uKMW8PxE8s
- Email: sierra@52sierra.net

Please do not send passwords, account credentials, or other private information.

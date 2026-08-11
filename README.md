# Sierra Installer

**Sierra Installer** is a Windows patcher and delivery tool for installing supported versions of **SPT (Single Player Tarkov)** from a clean copy of Escape from Tarkov.

Instead of redistributing a complete game installation, Sierra applies verified patch data to the user's own Tarkov copy. Releases can be installed directly from Sierra's web repository or kept as **Archived snapshots** for local/offline use.

## In service

This project is the refactored successor to the legacy Sierra SPT patcher. It has been used as Sierra's active SPT patch delivery and installation system, serving users who need access to supported and archived SPT releases.

The current installer includes resumable web delivery, SHA-256 package verification, source-file compatibility checks, .NET prerequisite warnings, archived/offline releases, and detailed failure logging.

## Using it

1. Install or verify your official Escape from Tarkov installation.
2. Make a **fresh copy** of the Tarkov folder. Do not patch the official Live folder directly.
3. Open Sierra Installer, choose the SPT release you want, select the copied folder, and click **Install SPT**.
4. Wait for the installer to finish before launching SPT.

For troubleshooting and a foolproof step-by-step guide, see **[USER_GUIDE.md](USER_GUIDE.md)**.

## Links

- Website: https://52sierra.net/patcher/
- Discord: https://discord.gg/uKMW8PxE8s

> Sierra Installer is an independent community project and is not affiliated with Battlestate Games.

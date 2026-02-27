# Gentoo Installer (CLI)

**Gentoo Installer** is a simple **non-graphical Gentoo Linux installer**, inspired by **archinstall**.

This project is **open-source** and was created for educational and experimental purposes.

## Important warning
⚠️ **This installer has NOT been tested**.  
⚠️ You may encounter **bugs**, **missing features**, or **unexpected behavior**.  
⚠️ Use **at your own risk**.

## Features
- 🖥️ Text-based (TUI) installer with curses-based menus  
- 🔧 Inspired by archinstall  
- 🧱 Designed for Gentoo Linux  
- 🆓 Open Source  
- 🧪 Hobby / educational project  
- 💾 Configuration save/load (JSON-based, similar to archinstall)  
- 🔒 LUKS disk encryption support  
- 📂 Btrfs with subvolumes (`@`, `@home`, `@snapshots`)  
- 🗄️ Multiple filesystem options: ext4, Btrfs, XFS  
- 🌐 Multiple network modes including static IP configuration  
- 🪞 Configurable Gentoo mirror selection  
- 🖥️ Desktop profiles: GNOME, KDE Plasma, Xfce, or none  
- 🔧 Stage3 variant selection (systemd, openrc, musl, hardened)  
- 🪝 Pre- and post-install hook scripts  
- ⚙️ Dry-run mode for testing without writing to disk  

## Configuration options

The installer provides a TUI menu to configure the following:

- **Language** — UI language (English, Polish, German, French, and more)  
- **Disk** — Target disk selection, auto or manual partitioning  
- **Filesystem** — Root filesystem type (ext4, btrfs, xfs) and optional Btrfs subvolumes  
- **Encryption** — Optional LUKS encryption on the root partition  
- **Desktop** — Desktop environment profile (GNOME, Plasma, Xfce, or console-only)  
- **Hostname / User** — System hostname, username, and passwords  
- **Bootloader** — systemd-boot, GRUB, efistub, limine, or rEFInd  
- **Kernel** — dist-kernel, genkernel, or manual  
- **Initramfs** — Auto-detect, dracut, or genkernel  
- **Network** — copy ISO config, NetworkManager (default/iwd), static IP, or manual  
- **Mirror** — Gentoo mirror URL (supports `mirrorselect` via `GENTOO_USE_MIRRORSELECT=1`)  
- **Stage3** — Stage3 variant (systemd, openrc, musl, hardened)  
- **Hooks** — Paths to scripts to run before and/or after installation  

Configurations can be saved to and loaded from JSON files.

## Project status
🚧 This project is in a **very early development stage**.  
Not intended for daily or production use.

## License
This is an **open-source** project.  
See the repository for license details.

## TODO / Roadmap

### 🇬🇧 English
- [x] Basic CLI menu
- [x] Disk selection and partitioning
- [ ] Gentoo base system installation
- [x] fstab configuration
- [x] User and root password setup
- [ ] Error handling
- [ ] Virtual machine testing
- [ ] Full LUKS encryption testing
- [ ] Btrfs subvolume layout testing
- [ ] Static networking testing

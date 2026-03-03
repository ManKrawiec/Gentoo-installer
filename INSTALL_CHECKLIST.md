# Gentoo Install Checklist (systemd flow)

This file maps the installer pipeline to standard Gentoo handbook phases for an amd64 installation.

## Covered by `gentoo_install.py`

1. Disk prep: partition table, filesystems, optional LUKS, mounting target.
2. Stage3: use provided tarball or auto-download latest stage3 and extract into `/mnt/gentoo`.
3. Base config: hostname, hosts, locale, timezone, make.conf basics, repos config copy.
4. Chroot prep: bind mounts (`/proc`, `/sys`, `/dev`, `/run`) and resolver copy.
5. Portage sync: `emerge --sync`.
6. Kernel: `gentoo-kernel-bin` / `genkernel` / manual sources mode.
7. Bootloader: `systemd-boot` or `grub`.
8. Users: root password, regular user, sudoers.
9. Network: NetworkManager modes or static config (systemd-networkd or OpenRC netifrc).
10. Initramfs: optional dracut regeneration for LUKS/Btrfs.

## Current constraints

1. `systemd-boot` path is only valid with `stage3_variant=systemd` and UEFI.
2. Advanced bootloader flows (`efistub`, `limine`, `refind`) are not implemented.

## Suggested next expansions

1. Full profile selection automation (`eselect profile`) in TUI.
2. Boot entry generation validation for systemd-boot edge cases.
3. Add implementations for extra bootloaders.

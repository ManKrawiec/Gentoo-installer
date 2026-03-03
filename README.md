# Gentoo Installer

Text installer for Gentoo Linux inspired by `archinstall`.

## Current status

The installer is usable but still evolving.  
Default mode is **dry-run** (safe): it prints planned commands and does not execute destructive actions.

Supported high-level flow:
- amd64 stage3 variants: `systemd`, `openrc`, `musl`, `hardened` (best tested: `systemd`, `openrc`)
- disk mode: `auto` (wipe and create layout) and `manual` (reuse existing partitions)
- filesystems: `ext4`, `btrfs`, `xfs`
- optional LUKS on root
- kernel modes: `dist-kernel`, `genkernel`, `manual`
- bootloaders: `grub`, `systemd-boot` (`systemd-boot` requires UEFI + systemd variant)
- network: copy/manual, NetworkManager, static config (systemd-networkd or OpenRC netifrc)

## Quick start

Run in dry-run:

```bash
python3 gentoo_install.py
```

Run real installation (dangerous):

```bash
sudo python3 gentoo_install.py --execute
```

## Stage3 source

Source resolution order:
1. `stage3_source` from TUI/config
2. `GENTOO_STAGE3_TARBALL` environment variable
3. auto-download latest stage3 from configured Gentoo mirror

So you can install without manually finding a stage3 URL.

## Config files

CLI supports saving/loading config and credentials:

```bash
python3 gentoo_install.py --save-config /root/gentoo_config.json --save-creds /root/gentoo_creds.json
python3 gentoo_install.py --config /root/gentoo_config.json --creds /root/gentoo_creds.json --execute
```

## Important warnings

- `auto` disk mode destroys data on selected disk.
- This is not yet a full replacement for every handbook scenario.
- Always test in VM before running on hardware.

## Checklist

Implementation coverage and known constraints: [`INSTALL_CHECKLIST.md`](INSTALL_CHECKLIST.md).

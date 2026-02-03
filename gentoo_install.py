#!/usr/bin/env python3
"""Text-based Gentoo installer, inspired by archinstall.

Warning: this is an early, mostly dry-run implementation. It prints the
commands it would run instead of actually installing Gentoo, until the
individual steps are fully implemented and tested.
"""

import argparse
import curses
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from typing import Dict, List


@dataclasses.dataclass
class DesktopProfile:
    name: str
    description: str
    packages: List[str]
    services: List[str]


@dataclasses.dataclass
class GentooInstallConfig:
    language: str = "en_US"
    # Stage3
    stage3_source: str | None = None  # local path or URL; optional if env var is used
    # Disk configuration
    target_disk: str | None = None
    disk_mode: str = "auto"  # "auto" = wipe and create layout, "manual" = use existing partitions
    root_partition: str | None = None
    boot_partition: str | None = None
    swap_partition: str | None = None
    # Map partition path -> filesystem to be created (only used in manual mode)
    format_partitions: Dict[str, str] = dataclasses.field(default_factory=dict)
    root_fs: str = "ext4"
    use_uefi: bool | None = None
    # System
    desktop_profile: str | None = None
    hostname: str | None = None
    username: str | None = None
    # Authentication
    root_password: str | None = None
    user_password: str | None = None
    user_is_sudoer: bool = True
    # Boot/kernel/network
    bootloader: str = "systemd-boot"
    kernel: str = "dist-kernel"  # dist-kernel, genkernel, manual
    network_mode: str = "copy_iso"  # copy_iso, manual, nm_default, nm_iwd

    def is_complete(self) -> bool:
        # For manual mode we require at least root_partition, for auto we require target_disk.
        if self.disk_mode == "manual":
            disk_ok = bool(self.root_partition)
        else:
            disk_ok = bool(self.target_disk)

        return all(
            [
                self.language,
                disk_ok,
                self.root_fs,
                self.use_uefi is not None,
                self.desktop_profile,
                self.hostname,
                self.username,
                self.root_password,
                self.user_password,
                self.bootloader,
                self.kernel,
                self.network_mode,
            ]
        )


GENTOO_ROOT = "/mnt/gentoo"


DESKTOP_PROFILES: Dict[str, DesktopProfile] = {
    "none": DesktopProfile(
        name="none",
        description="No desktop environment (console-only system)",
        packages=[],
        services=[],
    ),
    "gnome": DesktopProfile(
        name="gnome",
        description="GNOME desktop environment",
        packages=["gnome-base/gnome"],
        services=["gdm"],
    ),
    "plasma": DesktopProfile(
        name="plasma",
        description="KDE Plasma desktop environment",
        packages=["kde-plasma/plasma-meta"],
        services=["sddm"],
    ),
    "xfce": DesktopProfile(
        name="xfce",
        description="Xfce desktop environment",
        packages=["xfce-base/xfce4-meta"],
        services=["lightdm"],
    ),
}

# Language options for the installer UI (not system locales yet).
# This is a curated subset of common languages/locales.
LANGUAGES: Dict[str, str] = {
    "en_US": "English (US)",
    "pl_PL": "Polish",
    "de_DE": "German",
    "fr_FR": "French",
    "es_ES": "Spanish",
    "pt_BR": "Brazilian Portuguese",
    "it_IT": "Italian",
    "ru_RU": "Russian",
    "uk_UA": "Ukrainian",
    "tr_TR": "Turkish",
    "cs_CZ": "Czech",
    "nl_NL": "Dutch",
    "sv_SE": "Swedish",
    "fi_FI": "Finnish",
    "et_EE": "Estonian",
    "lt_LT": "Lithuanian",
    "el_GR": "Greek",
    "hu_HU": "Hungarian",
    "zh_CN": "Chinese (simplified)",
    "ja_JP": "Japanese",
    "ko_KR": "Korean",
    "ar_EG": "Arabic",
}

# Bootloader / kernel / network options used by the installer.
BOOTLOADERS: list[str] = [
    "systemd-boot",
    "grub",
    "efistub",
    "limine",
    "refind",
]

KERNELS: list[str] = [
    "dist-kernel",
    "genkernel",
    "manual",
]

NETWORK_MODES: list[str] = [
    "copy_iso",      # Copy ISO network config into installation
    "manual",        # User will configure later
    "nm_default",    # NetworkManager (default backend)
    "nm_iwd",        # NetworkManager (iwd backend)
]


def part_name(disk: str, number: int) -> str:
    """Return full partition name for a given disk and partition number.

    Handles nvme/mmcblk style devices that need a "p" before the number.
    """

    if disk.startswith("/dev/nvme") or disk.startswith("/dev/mmcblk"):
        return f"{disk}p{number}"
    return f"{disk}{number}"


def run_cmd(cmd: List[str], dry_run: bool) -> None:
    """Run a shell command or just print it when in dry-run mode."""

    printable = " ".join(cmd)
    print(f"[CMD]{' (dry-run)' if dry_run else ''}: {printable}")
    if dry_run:
        return

    subprocess.run(cmd, check=True)


def run_cmd_capture(cmd: List[str]) -> subprocess.CompletedProcess:
    """Run a command and return the CompletedProcess (stdout/stderr captured).

    This helper never prints; callers are responsible for logging.
    """

    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def run_in_chroot(cmd: List[str], dry_run: bool, root: str = GENTOO_ROOT) -> None:
    """Execute a command inside the Gentoo chroot.

    Assumes that GENTOO_ROOT contains a valid stage3 and bind mounts.
    """

    chroot_cmd = ["chroot", root] + cmd
    run_cmd(chroot_cmd, dry_run=dry_run)


def setup_chroot_mounts(dry_run: bool, root: str = GENTOO_ROOT) -> None:
    """Bind-mount host pseudo filesystems into the chroot.

    This mirrors the standard Gentoo installation handbook procedure.
    """

    mounts = [
        ["mount", "--types", "proc", "/proc", os.path.join(root, "proc")],
        ["mount", "--rbind", "/sys", os.path.join(root, "sys")],
        ["mount", "--make-rslave", os.path.join(root, "sys")],
        ["mount", "--rbind", "/dev", os.path.join(root, "dev")],
        ["mount", "--make-rslave", os.path.join(root, "dev")],
        ["mount", "--rbind", "/run", os.path.join(root, "run")],
        ["mount", "--make-rslave", os.path.join(root, "run")],
    ]
    for cmd in mounts:
        run_cmd(cmd, dry_run=dry_run)


def generate_fstab(cfg: GentooInstallConfig, dry_run: bool, root: str = GENTOO_ROOT) -> None:
    """Generate a basic /etc/fstab for the target system.

    Uses findmnt/blkid to create UUID-based entries for currently mounted
    filesystems under GENTOO_ROOT plus the configured swap partition.
    """

    etc_dir = os.path.join(root, "etc")
    fstab_path = os.path.join(etc_dir, "fstab")
    os.makedirs(etc_dir, exist_ok=True)

    print(f"[STEP] Generating fstab at {fstab_path}")

    lines: list[str] = []

    # Filesystems mounted under root (/, /boot, etc.)
    try:
        result = run_cmd_capture([
            "findmnt",
            "-R",
            "-no",
            "SOURCE,TARGET,FSTYPE,UUID",
            root,
        ])
        for raw_line in result.stdout.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            parts = raw_line.split()
            if len(parts) != 4:
                continue
            source, target, fstype, uuid = parts
            if not uuid or not fstype:
                continue
            # Map absolute target to mountpoint inside the new system
            if not target.startswith(root):
                continue
            rel = target[len(root) :]
            mountpoint = rel if rel else "/"
            passno = "1" if mountpoint == "/" else "2"
            line = f"UUID={uuid} {mountpoint} {fstype} defaults 0 {passno}"
            lines.append(line)
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Could not auto-generate fstab from findmnt: {exc!r}")

    # Swap partition from config (might not be mounted)
    if cfg.swap_partition:
        try:
            res = run_cmd_capture([
                "blkid",
                "-s",
                "UUID",
                "-o",
                "value",
                cfg.swap_partition,
            ])
            swap_uuid = res.stdout.strip()
            if swap_uuid:
                lines.append(f"UUID={swap_uuid} none swap sw 0 0")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Could not determine UUID for swap {cfg.swap_partition}: {exc!r}")

    if not lines:
        print("[WARN] No fstab entries were generated.")
        return

    content = "\n".join(lines) + "\n"
    print("[INFO] fstab entries that will be written:")
    for l in lines:
        print("  ", l)

    if dry_run:
        print("[INFO] Dry-run: not writing fstab file.")
        return

    with open(fstab_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("[OK] fstab written.")


def list_disks() -> list[dict]:
    """Return a list of available disks using lsblk JSON output.

    Each entry contains: name, path, size, model, parts (list of partitions).
    """

    try:
        result = subprocess.run(
            [
                "lsblk",
                "-J",
                "-o",
                "NAME,TYPE,SIZE,MODEL,FSTYPE,MOUNTPOINT",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(result.stdout)
    except Exception:
        return []

    disks: list[dict] = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        parts: list[dict] = []
        for ch in dev.get("children", []) or []:
            parts.append(
                {
                    "name": ch.get("name"),
                    "path": f"/dev/{ch.get('name')}",
                    "size": ch.get("size") or "",
                    "fstype": ch.get("fstype") or "",
                    "mountpoint": ch.get("mountpoint") or "",
                }
            )
        disks.append(
            {
                "name": dev.get("name"),
                "path": f"/dev/{dev.get('name')}",
                "size": dev.get("size") or "",
                "model": dev.get("model") or "",
                "parts": parts,
            }
        )
    return disks


# ---------- TUI (curses) frontend ----------

TUI_STEPS = [
    "Language",
    "Stage3 source",
    "Disk configuration",
    "Swap",
    "Hostname",
    "User",
    "Authentication",
    "Desktop profile",
    "Bootloader",
    "Kernel",
    "Network",
    "Install",
    "Abort",
]


def _tui_draw_main(stdscr, current_idx: int, cfg: GentooInstallConfig, message: str) -> None:
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    stdscr.addstr(0, 2, "Gentoo Installer (TUI prototype)", curses.A_BOLD)
    stdscr.addstr(1, 2, "Press q to quit, Enter to edit/confirm")

    # left column: steps
    for i, name in enumerate(TUI_STEPS):
        y = 3 + i
        attr = curses.A_REVERSE if i == current_idx else curses.A_NORMAL
        stdscr.addstr(y, 2, name, attr)

    # right panel: summary/info
    x0 = 28
    stdscr.addstr(3, x0, "Summary", curses.A_BOLD)
    lang_label = LANGUAGES.get(cfg.language, cfg.language)
    stdscr.addstr(5, x0, f"Language  : {lang_label or '-'}")
    stdscr.addstr(6, x0, f"Stage3    : {cfg.stage3_source or os.environ.get('GENTOO_STAGE3_TARBALL', '-')}")
    stdscr.addstr(7, x0, f"Disk      : {cfg.target_disk or '-'}")
    stdscr.addstr(8, x0, f"Disk mode : {cfg.disk_mode}")
    stdscr.addstr(9, x0, f"Root part.: {cfg.root_partition or '-'}")
    stdscr.addstr(10, x0, f"Boot part.: {cfg.boot_partition or '-'}")
    stdscr.addstr(11, x0, f"Swap part.: {cfg.swap_partition or '-'}")
    stdscr.addstr(12, x0, f"Root FS   : {cfg.root_fs or '-'}")
    stdscr.addstr(13, x0, f"UEFI      : " + ("yes" if cfg.use_uefi else "no" if cfg.use_uefi is not None else "-"))
    stdscr.addstr(14, x0, f"Hostname  : {cfg.hostname or '-'}")
    stdscr.addstr(15, x0, f"User      : {cfg.username or '-'}")
    stdscr.addstr(16, x0, f"Root pwd  : {'set' if cfg.root_password else 'NOT set'}")
    stdscr.addstr(17, x0, f"User pwd  : {'set' if cfg.user_password else 'NOT set'}")
    stdscr.addstr(18, x0, f"User sudo : {'yes' if cfg.user_is_sudoer else 'no'}")
    stdscr.addstr(19, x0, f"Desktop   : {cfg.desktop_profile or '-'}")
    stdscr.addstr(20, x0, f"Bootloader: {cfg.bootloader or '-'}")
    stdscr.addstr(21, x0, f"Kernel    : {cfg.kernel or '-'}")
    stdscr.addstr(22, x0, f"Network   : {cfg.network_mode or '-'}")

    if cfg.is_complete():
        stdscr.addstr(24, x0, "Config status: COMPLETE", curses.color_pair(0) | curses.A_BOLD)
    else:
        stdscr.addstr(24, x0, "Config status: incomplete", curses.A_DIM)

    if message:
        stdscr.addstr(h - 2, 2, message[: max(0, w - 4)], curses.A_BOLD)

    stdscr.refresh()


def _tui_prompt_input(stdscr, title: str, prompt: str, default: str | None = None) -> str | None:
    curses.curs_set(1)
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(0, 2, title, curses.A_BOLD)
    if default:
        prompt_full = f"{prompt} [{default}]: "
    else:
        prompt_full = f"{prompt}: "
    stdscr.addstr(2, 2, prompt_full)
    stdscr.refresh()

    curses.echo()
    s = stdscr.getstr(2, 2 + len(prompt_full), max(0, w - len(prompt_full) - 4))
    curses.noecho()
    curses.curs_set(0)

    value = s.decode(errors="ignore").strip()
    if not value and default is not None:
        return default
    return value or None


def _tui_prompt_password(stdscr, title: str, prompt: str) -> str | None:
    """Prompt for a password without echoing input."""

    curses.curs_set(1)
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(0, 2, title, curses.A_BOLD)
    prompt_full = f"{prompt}: "
    stdscr.addstr(2, 2, prompt_full)
    stdscr.refresh()

    curses.noecho()
    s = stdscr.getstr(2, 2 + len(prompt_full), max(0, w - len(prompt_full) - 4))
    curses.echo()
    curses.curs_set(0)

    value = s.decode(errors="ignore").strip()
    return value or None


def _tui_confirm(stdscr, question: str) -> bool:
    curses.curs_set(1)
    stdscr.clear()
    stdscr.addstr(0, 2, question)
    stdscr.addstr(2, 2, "[y/N]: ")
    stdscr.refresh()
    curses.echo()
    s = stdscr.getstr(2, 8, 4)
    curses.noecho()
    curses.curs_set(0)
    ans = s.decode(errors="ignore").strip().lower()
    return ans in {"y", "yes"}


def _tui_edit_language(stdscr, cfg: GentooInstallConfig) -> None:
    curses.curs_set(0)
    keys = list(LANGUAGES.keys())
    try:
        current_idx = keys.index(cfg.language)
    except ValueError:
        current_idx = 0

    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, "Installer language", curses.A_BOLD)
        stdscr.addstr(1, 2, "Choose language for the installer UI. Use '/' to search.")
        for i, code in enumerate(keys):
            label = LANGUAGES.get(code, code)
            mark = "[x]" if code == cfg.language else "[ ]"
            attr = curses.A_REVERSE if i == current_idx else curses.A_NORMAL
            stdscr.addstr(3 + i, 2, f"{mark} {label} ({code})", attr)
        stdscr.addstr(4 + len(keys), 2, "Enter = select, '/' = search, q = cancel")
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif ch == curses.KEY_DOWN and current_idx < len(keys) - 1:
            current_idx += 1
        elif ch in (ord("/"),):
            # Search by substring in label or code
            pattern = _tui_prompt_input(stdscr, "Search language", "Substring", None)
            if pattern:
                pattern_l = pattern.lower()
                for i, code in enumerate(keys):
                    label = LANGUAGES.get(code, code)
                    if pattern_l in label.lower() or pattern_l in code.lower():
                        current_idx = i
                        break
        elif ch in (curses.KEY_ENTER, 10, 13):
            cfg.language = keys[current_idx]
            return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_select_disk(stdscr, disks: list[dict], current_path: str | None) -> str | None:
    """Interactive disk selector similar in spirit to archinstall's view."""

    curses.curs_set(0)
    idx = 0
    if current_path:
        for i, d in enumerate(disks):
            if d.get("path") == current_path:
                idx = i
                break

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 2, "Disk configuration", curses.A_BOLD)
        stdscr.addstr(1, 2, "Select target disk (Up/Down, Enter, q=cancel)")

        for i, d in enumerate(disks):
            mark = "[x]" if d.get("path") == current_path else "[ ]"
            line = f"{mark} {d.get('path'):>12}  {d.get('size'):>8}  {d.get('model') or ''}"
            attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
            stdscr.addstr(3 + i, 2, line[: max(0, w - 4)], attr)

        # partitions of current disk
        y = 4 + len(disks)
        if y < h - 2:
            stdscr.addstr(y, 2, "Partitions", curses.A_BOLD)
            y += 1
            for part in disks[idx].get("parts", []):
                if y >= h - 1:
                    break
                line = (
                    f"{part.get('path'):>16}  {part.get('size'):>8}  "
                    f"{(part.get('fstype') or '-'):6}  {part.get('mountpoint') or ''}"
                )
                stdscr.addstr(y, 4, line[: max(0, w - 6)])
                y += 1

        stdscr.refresh()
        ch = stdscr.getch()
        if ch == curses.KEY_UP and idx > 0:
            idx -= 1
        elif ch == curses.KEY_DOWN and idx < len(disks) - 1:
            idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            return disks[idx].get("path")
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return None


def _tui_partition_menu(stdscr, part: dict, cfg: GentooInstallConfig) -> None:
    """Per-partition menu: assign roles and optional format flags.

    This does not modify partition table immediately; it only updates config
    about which partition will be used for which mountpoint and which should
    be formatted during installation.
    """

    options = [
        "Set as root (/)",
        "Set as /boot (EFI or boot)",
        "Set as swap",
        "Mark for format as ext4",
        "Mark for format as vfat (EFI)",
        "Clear format flag",
        "Cancel",
    ]
    idx = 0
    path = part.get("path")
    size = part.get("size") or ""
    fstype = part.get("fstype") or "-"
    fmt = cfg.format_partitions.get(path or "", "")

    curses.curs_set(0)
    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, path or "(unknown)", curses.A_BOLD)
        stdscr.addstr(1, 2, f"Size: {size}  FS: {fstype}")
        stdscr.addstr(2, 2, f"Current format flag: {fmt or 'none'}")
        stdscr.addstr(3, 2, "Assign role / format for this partition (q/ESC = back)")
        for i, label in enumerate(options):
            attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
            stdscr.addstr(5 + i, 2, label, attr)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and idx > 0:
            idx -= 1
        elif ch == curses.KEY_DOWN and idx < len(options) - 1:
            idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            choice = options[idx]
            if choice.startswith("Set as root"):
                cfg.root_partition = path
            elif choice.startswith("Set as /boot"):
                cfg.boot_partition = path
            elif choice.startswith("Set as swap"):
                cfg.swap_partition = path
            elif choice.startswith("Mark for format as ext4"):
                if path:
                    cfg.format_partitions[path] = "ext4"
            elif choice.startswith("Mark for format as vfat"):
                if path:
                    cfg.format_partitions[path] = "vfat"
            elif choice.startswith("Clear format flag"):
                if path and path in cfg.format_partitions:
                    del cfg.format_partitions[path]
            # refresh fmt for next redraw
            fmt = cfg.format_partitions.get(path or "", "")
            if choice.startswith("Cancel"):
                return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_pick_manual_partitions(stdscr, disk: dict, cfg: GentooInstallConfig) -> None:
    """Let user choose existing partitions for root/boot/swap.

    Does not modify partition table; just records paths in config.
    """

    parts = disk.get("parts", [])
    if not parts:
        return

    curses.curs_set(0)
    idx = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 2, "Manual partition selection", curses.A_BOLD)
        stdscr.addstr(1, 2, f"Disk: {disk.get('path')}")
        stdscr.addstr(2, 2, "Enter = manage partition, q/ESC = done")
        for i, p in enumerate(parts):
            mark_root = "(root)" if cfg.root_partition == p.get("path") else ""
            mark_boot = "(boot)" if cfg.boot_partition == p.get("path") else ""
            mark_swap = "(swap)" if cfg.swap_partition == p.get("path") else ""
            fmt = cfg.format_partitions.get(p.get("path") or "", None)
            fmt_tag = f"[fmt:{fmt}]" if fmt else ""
            mark = " ".join(m for m in [mark_root, mark_boot, mark_swap, fmt_tag] if m).strip()
            line = (
                f"{p.get('path'):>16}  {p.get('size'):>8}  "
                f"{(p.get('fstype') or '-'):6} {mark}"
            )
            attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
            stdscr.addstr(4 + i, 2, line[: max(0, w - 4)], attr)

        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and idx > 0:
            idx -= 1
        elif ch == curses.KEY_DOWN and idx < len(parts) - 1:
            idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            _tui_partition_menu(stdscr, parts[idx], cfg)
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            break


def _tui_edit_disk(stdscr, cfg: GentooInstallConfig) -> None:
    disks = list_disks()
    selected_disk: dict | None = None
    if disks:
        selected_path = _tui_select_disk(stdscr, disks, cfg.target_disk)
        if selected_path:
            cfg.target_disk = selected_path
            for d in disks:
                if d.get("path") == selected_path:
                    selected_disk = d
                    break
    else:
        # Fallback: manual entry if lsblk failed or found nothing.
        disk = _tui_prompt_input(
            stdscr,
            "Disk configuration",
            "Enter target disk (e.g. /dev/sda, /dev/nvme0n1)",
            cfg.target_disk,
        )
        if disk:
            cfg.target_disk = disk

    # Disk mode: auto (wipe disk) vs manual (reuse existing partitions)
    mode_default = cfg.disk_mode or "auto"
    mode = _tui_prompt_input(
        stdscr,
        "Disk configuration",
        "Disk mode (auto = wipe disk, manual = use existing partitions)",
        mode_default,
    )
    if mode and mode.lower().startswith("man"):
        cfg.disk_mode = "manual"
        # For manual mode, pick partitions on the selected disk.
        if selected_disk is None:
            # try to find by path
            for d in disks:
                if d.get("path") == cfg.target_disk:
                    selected_disk = d
                    break
        if selected_disk is not None:
            _tui_pick_manual_partitions(stdscr, selected_disk, cfg)
    else:
        cfg.disk_mode = "auto"
        # auto mode ignores manually specified partitions
        cfg.root_partition = None
        cfg.boot_partition = None

    # UEFI yes/no
    current = "yes" if cfg.use_uefi else "no" if cfg.use_uefi is not None else None
    ans = _tui_prompt_input(
        stdscr,
        "Disk configuration",
        "Use UEFI layout? (yes/no)",
        current,
    )
    if ans:
        ans_l = ans.lower()
        if ans_l in {"y", "yes"}:
            cfg.use_uefi = True
        elif ans_l in {"n", "no"}:
            cfg.use_uefi = False


def _tui_edit_hostname(stdscr, cfg: GentooInstallConfig) -> None:
    host = _tui_prompt_input(
        stdscr,
        "Hostname",
        "Hostname",
        cfg.hostname or "gentoo",
    )
    if host:
        cfg.hostname = host


def _tui_edit_user(stdscr, cfg: GentooInstallConfig) -> None:
    user = _tui_prompt_input(
        stdscr,
        "User",
        "Main username",
        cfg.username or "user",
    )
    if user:
        cfg.username = user


def _tui_edit_authentication(stdscr, cfg: GentooInstallConfig) -> None:
    """Configure passwords and sudo flag for the main user."""

    options = [
        "Set root password",
        "Set user password",
        "Toggle user sudo (wheel)",
        "Back",
    ]
    idx = 0

    curses.curs_set(0)
    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, "Authentication", curses.A_BOLD)
        stdscr.addstr(1, 2, "Configure passwords and whether the user is a sudoer.")
        stdscr.addstr(3, 2, f"Root password set : {'yes' if cfg.root_password else 'no'}")
        stdscr.addstr(4, 2, f"User password set : {'yes' if cfg.user_password else 'no'}")
        stdscr.addstr(5, 2, f"User is sudoer   : {'yes' if cfg.user_is_sudoer else 'no'}")

        for i, label in enumerate(options):
            attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
            stdscr.addstr(7 + i, 2, label, attr)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and idx > 0:
            idx -= 1
        elif ch == curses.KEY_DOWN and idx < len(options) - 1:
            idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            choice = options[idx]
            if choice.startswith("Set root"):
                pw = _tui_prompt_password(stdscr, "Root password", "Enter root password")
                if pw is not None:
                    cfg.root_password = pw
            elif choice.startswith("Set user"):
                pw = _tui_prompt_password(stdscr, "User password", "Enter user password")
                if pw is not None:
                    cfg.user_password = pw
            elif choice.startswith("Toggle user sudo"):
                cfg.user_is_sudoer = not cfg.user_is_sudoer
            else:
                return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_edit_desktop(stdscr, cfg: GentooInstallConfig) -> None:
    # simple numeric choice like in the CLI version
    curses.curs_set(0)
    stdscr.clear()
    stdscr.addstr(0, 2, "Desktop profile", curses.A_BOLD)
    stdscr.addstr(1, 2, "Choose which profile to install.")
    keys = list(DESKTOP_PROFILES.keys())
    for idx, key in enumerate(keys, start=1):
        p = DESKTOP_PROFILES[key]
        stdscr.addstr(3 + idx, 4, f"{idx}) {p.name:8} - {p.description}")
    stdscr.addstr(4 + len(keys) + 1, 2, "Enter number (blank to keep current): ")
    stdscr.refresh()

    curses.echo()
    s = stdscr.getstr(4 + len(keys) + 1, 2 + len("Enter number (blank to keep current): "), 4)
    curses.noecho()
    choice = s.decode(errors="ignore").strip()
    if not choice:
        return
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(keys):
            cfg.desktop_profile = keys[idx - 1]


def _tui_edit_swap(stdscr, cfg: GentooInstallConfig) -> None:
    """Pick an existing partition to use as swap (optional)."""

    disks = list_disks()
    parts: list[dict] = []
    for d in disks:
        for p in d.get("parts", []):
            parts.append(p)
    if not parts:
        # Fallback: manual entry
        path = _tui_prompt_input(stdscr, "Swap", "Swap partition path (blank = none)", cfg.swap_partition)
        cfg.swap_partition = path or None
        return

    curses.curs_set(0)
    idx = 0
    # try to preselect current swap
    if cfg.swap_partition:
        for i, p in enumerate(parts):
            if p.get("path") == cfg.swap_partition:
                idx = i
                break

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(0, 2, "Swap configuration", curses.A_BOLD)
        stdscr.addstr(1, 2, "Select partition for swap (Enter), n = none, q = cancel")
        for i, p in enumerate(parts):
            mark = "[x]" if p.get("path") == cfg.swap_partition else "[ ]"
            line = (
                f"{mark} {p.get('path'):>16}  {p.get('size'):>8}  "
                f"{(p.get('fstype') or '-'):6}"
            )
            attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
            stdscr.addstr(3 + i, 2, line[: max(0, w - 4)], attr)
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and idx > 0:
            idx -= 1
        elif ch == curses.KEY_DOWN and idx < len(parts) - 1:
            idx += 1
        elif ch in (ord("n"), ord("N")):
            cfg.swap_partition = None
            return
        elif ch in (curses.KEY_ENTER, 10, 13):
            cfg.swap_partition = parts[idx].get("path")
            return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_edit_bootloader(stdscr, cfg: GentooInstallConfig) -> None:
    curses.curs_set(0)
    try:
        current_idx = BOOTLOADERS.index(cfg.bootloader)
    except ValueError:
        current_idx = 0

    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, "Bootloader", curses.A_BOLD)
        stdscr.addstr(1, 2, "Choose bootloader to install.")
        for i, b in enumerate(BOOTLOADERS):
            label = b
            if b == "systemd-boot":
                label += " (default)"
            mark = "[x]" if b == cfg.bootloader else "[ ]"
            attr = curses.A_REVERSE if i == current_idx else curses.A_NORMAL
            stdscr.addstr(3 + i, 2, f"{mark} {label}", attr)
        stdscr.addstr(4 + len(BOOTLOADERS), 2, "Enter = select, q = cancel")
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif ch == curses.KEY_DOWN and current_idx < len(BOOTLOADERS) - 1:
            current_idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            cfg.bootloader = BOOTLOADERS[current_idx]
            return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_edit_kernel(stdscr, cfg: GentooInstallConfig) -> None:
    curses.curs_set(0)
    try:
        current_idx = KERNELS.index(cfg.kernel)
    except ValueError:
        current_idx = 0

    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, "Kernel selection", curses.A_BOLD)
        stdscr.addstr(1, 2, "Choose how the kernel should be managed.")
        for i, k in enumerate(KERNELS):
            label = k
            mark = "[x]" if k == cfg.kernel else "[ ]"
            attr = curses.A_REVERSE if i == current_idx else curses.A_NORMAL
            stdscr.addstr(3 + i, 2, f"{mark} {label}", attr)
        stdscr.addstr(4 + len(KERNELS), 2, "Enter = select, q = cancel")
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif ch == curses.KEY_DOWN and current_idx < len(KERNELS) - 1:
            current_idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            cfg.kernel = KERNELS[current_idx]
            return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_edit_network(stdscr, cfg: GentooInstallConfig) -> None:
    curses.curs_set(0)
    try:
        current_idx = NETWORK_MODES.index(cfg.network_mode)
    except ValueError:
        current_idx = 0

    labels = {
        "copy_iso": "Copy ISO network configuration to installation",
        "manual": "Manual configuration",
        "nm_default": "Use NetworkManager (default backend)",
        "nm_iwd": "Use NetworkManager (iwd backend)",
    }

    while True:
        stdscr.clear()
        stdscr.addstr(0, 2, "Network configuration", curses.A_BOLD)
        stdscr.addstr(1, 2, "Choose how network should be set up in the install.")
        for i, mode in enumerate(NETWORK_MODES):
            label = labels.get(mode, mode)
            mark = "[x]" if mode == cfg.network_mode else "[ ]"
            attr = curses.A_REVERSE if i == current_idx else curses.A_NORMAL
            stdscr.addstr(3 + i, 2, f"{mark} {label}", attr)
        stdscr.addstr(4 + len(NETWORK_MODES), 2, "Enter = select, q = cancel")
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif ch == curses.KEY_DOWN and current_idx < len(NETWORK_MODES) - 1:
            current_idx += 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            cfg.network_mode = NETWORK_MODES[current_idx]
            return
        elif ch in (ord("q"), ord("Q"), 27):  # q or ESC
            return


def _tui_edit_stage3(stdscr, cfg: GentooInstallConfig) -> None:
    """Configure source for the Gentoo stage3 tarball.

    User can enter a local path or an HTTP/HTTPS URL. If left empty, the
    installer will fall back to the GENTOO_STAGE3_TARBALL environment
    variable or skip automatic stage3 extraction.
    """

    current = cfg.stage3_source or os.environ.get("GENTOO_STAGE3_TARBALL", "")
    value = _tui_prompt_input(
        stdscr,
        "Stage3 source",
        "Path or URL to stage3 tarball (blank = use env or skip)",
        current or None,
    )
    cfg.stage3_source = value or None


def tui_main(stdscr, cfg: GentooInstallConfig, dry_run: bool) -> bool:
    curses.curs_set(0)
    stdscr.keypad(True)
    message = ""
    current_idx = 0

    while True:
        _tui_draw_main(stdscr, current_idx, cfg, message)
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q"), 27):  # q or ESC
            if _tui_confirm(stdscr, "Abort installer and quit?"):
                return False
            message = ""
            continue
        if ch == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
            message = ""
        elif ch == curses.KEY_DOWN and current_idx < len(TUI_STEPS) - 1:
            current_idx += 1
            message = ""
        elif ch in (curses.KEY_ENTER, 10, 13):
            step = TUI_STEPS[current_idx]
            if step == "Language":
                _tui_edit_language(stdscr, cfg)
            elif step == "Stage3 source":
                _tui_edit_stage3(stdscr, cfg)
            elif step == "Disk configuration":
                _tui_edit_disk(stdscr, cfg)
            elif step == "Swap":
                _tui_edit_swap(stdscr, cfg)
            elif step == "Hostname":
                _tui_edit_hostname(stdscr, cfg)
            elif step == "User":
                _tui_edit_user(stdscr, cfg)
            elif step == "Authentication":
                _tui_edit_authentication(stdscr, cfg)
            elif step == "Desktop profile":
                _tui_edit_desktop(stdscr, cfg)
            elif step == "Bootloader":
                _tui_edit_bootloader(stdscr, cfg)
            elif step == "Kernel":
                _tui_edit_kernel(stdscr, cfg)
            elif step == "Network":
                _tui_edit_network(stdscr, cfg)
            elif step == "Install":
                if not cfg.is_complete():
                    message = "Config incomplete – fill all fields before installing."
                    continue
                if _tui_confirm(stdscr, "Start installation now? (steps will run in this terminal)"):
                    return True
                message = "Installation cancelled."
            elif step == "Abort":
                if _tui_confirm(stdscr, "Abort installer and quit?"):
                    return False
                message = ""


def run_tui(cfg: GentooInstallConfig, dry_run: bool) -> bool:
    """Run the curses TUI.

    Returns True if user chose Install, False if aborted.
    """

    def _inner(stdscr):
        return tui_main(stdscr, cfg, dry_run)

    return curses.wrapper(_inner)


# ---------- Classic line-based prompts (fallback / internal) ----------


def confirm(prompt: str) -> bool:
    while True:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no", ""}:
            return False
        print("Please answer y or n.")


def prompt_target_disk() -> str:
    print("\n=== Disk selection ===")
    print("This will ERASE the selected disk. Make sure you know what you are doing.")
    disk = input("Enter target disk (e.g. /dev/sda, /dev/nvme0n1): ").strip()
    if not disk:
        print("No disk entered, aborting.")
        sys.exit(1)
    return disk


def prompt_root_fs() -> str:
    print("\n=== Filesystem selection ===")
    print("For now only ext4 is supported as a safe default.")
    fs = input("Root filesystem [ext4]: ").strip() or "ext4"
    return fs


def prompt_uefi() -> bool:
    print("\n=== Boot mode ===")
    print("Assume UEFI on modern machines. Legacy BIOS is not yet implemented.")
    return confirm("Use UEFI partition layout?")


def prompt_hostname() -> str:
    hostname = input("Hostname [gentoo]: ").strip() or "gentoo"
    return hostname


def prompt_username() -> str:
    username = input("Main user name [user]: ").strip() or "user"
    return username


def prompt_desktop_profile() -> str:
    print("\n=== Desktop environment profile ===")
    print("Choose which profile should be installed on top of base Gentoo.")
    keys = list(DESKTOP_PROFILES.keys())
    for idx, key in enumerate(keys, start=1):
        profile = DESKTOP_PROFILES[key]
        print(f" {idx}) {profile.name:8} - {profile.description}")

    while True:
        choice = input(f"Select profile [1-{len(keys)}] (default 1 = none): ").strip()
        if not choice:
            return keys[0]
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(keys):
                return keys[idx - 1]
        print("Invalid choice, try again.")


def collect_config() -> GentooInstallConfig:
    print("""\nGentoo Installer (early prototype)
=================================
This tool is inspired by archinstall and aims to automate a full Gentoo
installation. Right now it is in a dry-run / planning stage: it will show
what it *would* do, but most steps are not yet implemented.
""")

    if os.geteuid() != 0:
        print("[WARN] You are not running as root. Real installation would fail; this is fine for dry-run.")

    target_disk = prompt_target_disk()
    root_fs = prompt_root_fs()
    use_uefi = prompt_uefi()
    hostname = prompt_hostname()
    username = prompt_username()
    desktop_profile = prompt_desktop_profile()

    print("\n=== Summary of configuration ===")
    print(f"Target disk      : {target_disk}")
    print(f"Root filesystem  : {root_fs}")
    print(f"UEFI             : {'yes' if use_uefi else 'no'}")
    print(f"Hostname         : {hostname}")
    print(f"User             : {username}")
    print(f"Desktop profile  : {desktop_profile}")

    if not confirm("Proceed with these settings?"):
        print("Aborted by user.")
        sys.exit(1)

    return GentooInstallConfig(
        target_disk=target_disk,
        root_fs=root_fs,
        use_uefi=use_uefi,
        hostname=hostname,
        username=username,
        desktop_profile=desktop_profile,
    )


# --- Installation steps (mostly stubs for now) ---


def prepare_disks(cfg: GentooInstallConfig, dry_run: bool) -> None:
    """Partition, format and mount the target disk or reuse existing partitions.

    Layout implemented for auto mode:
    - UEFI: GPT, 512MiB EFI system partition (FAT32) + rest as ext4 root.
    - BIOS: MBR (msdos), single ext4 root partition using the whole disk.

    For manual mode:
    - Reuses existing partitions, only mounts the ones selected in config.
    """

    print("\n[STEP] Preparing disks")

    if cfg.disk_mode == "manual":
        # Manual mode: optionally format marked partitions, then mount.
        if not cfg.root_partition:
            print("[ERROR] Manual disk mode selected but no root_partition set.")
            sys.exit(1)

        if cfg.format_partitions:
            print("Partitions marked to be formatted (manual mode):")
            for path, fs in cfg.format_partitions.items():
                print(f"  {path} -> {fs}")
            if not confirm("Proceed with formatting the above partitions? THIS WILL DESTROY DATA."):
                print("User declined formatting; continuing without mkfs.")
            else:
                for path, fs in cfg.format_partitions.items():
                    if fs == "ext4":
                        run_cmd(["mkfs.ext4", path], dry_run=dry_run)
                    elif fs == "vfat":
                        run_cmd(["mkfs.vfat", "-F32", path], dry_run=dry_run)

        print("Using existing partitions (manual mode); partition table will NOT be modified.")
        cmds: list[list[str]] = [
            ["mkdir", "-p", GENTOO_ROOT],
            ["mount", cfg.root_partition, GENTOO_ROOT],
        ]
        if cfg.boot_partition:
            cmds.extend(
                [
                    ["mkdir", "-p", os.path.join(GENTOO_ROOT, "boot")],
                    ["mount", cfg.boot_partition, os.path.join(GENTOO_ROOT, "boot")],
                ]
            )
        for cmd in cmds:
            run_cmd(cmd, dry_run)
        return

    # Auto mode: warn that entire disk will be wiped.
    if not confirm(f"This will erase ALL data on {cfg.target_disk}. Continue?"):
        print("Skipping disk preparation.")
        return

    if cfg.root_fs != "ext4":
        print("[WARN] Only ext4 is implemented right now; using ext4 for root.")

    if cfg.use_uefi:
        print("Planning GPT + EFI system partition + ext4 root on", cfg.target_disk)
        boot_part = part_name(cfg.target_disk, 1)
        root_part = part_name(cfg.target_disk, 2)

        cmds = [
            # Partition table and partitions
            ["parted", cfg.target_disk, "--script", "mklabel", "gpt"],
            [
                "parted",
                cfg.target_disk,
                "--script",
                "mkpart",
                "ESP",
                "fat32",
                "1MiB",
                "513MiB",
            ],
            ["parted", cfg.target_disk, "--script", "set", "1", "boot", "on"],
            [
                "parted",
                cfg.target_disk,
                "--script",
                "mkpart",
                "primary",
                "ext4",
                "513MiB",
                "100%",
            ],
            # Filesystems
            ["mkfs.vfat", "-F32", boot_part],
            ["mkfs.ext4", root_part],
            # Mount points
            ["mkdir", "-p", GENTOO_ROOT],
            ["mount", root_part, GENTOO_ROOT],
            ["mkdir", "-p", os.path.join(GENTOO_ROOT, "boot")],
            ["mount", boot_part, os.path.join(GENTOO_ROOT, "boot")],
        ]
    else:
        print("Planning BIOS/MBR layout with single ext4 root on", cfg.target_disk)
        root_part = part_name(cfg.target_disk, 1)

        cmds = [
            ["parted", cfg.target_disk, "--script", "mklabel", "msdos"],
            [
                "parted",
                cfg.target_disk,
                "--script",
                "mkpart",
                "primary",
                "ext4",
                "1MiB",
                "100%",
            ],
            ["mkfs.ext4", root_part],
            ["mkdir", "-p", GENTOO_ROOT],
            ["mount", root_part, GENTOO_ROOT],
        ]

    for cmd in cmds:
        run_cmd(cmd, dry_run)


def install_stage3(cfg: GentooInstallConfig, dry_run: bool) -> None:
    """Install Gentoo stage3 into GENTOO_ROOT.

    For safety and flexibility we expect the user (or environment) to provide
    the path to a stage3 tarball via the GENTOO_STAGE3_TARBALL environment
    variable. This keeps URL logic out of the installer and works with both
    locally downloaded and mirrored tarballs.
    """

    print("\n[STEP] Installing stage3")

    # Ensure target directory exists
    os.makedirs(GENTOO_ROOT, exist_ok=True)

    # Determine tarball source: config path/URL, env var as fallback.
    source = cfg.stage3_source or os.environ.get("GENTOO_STAGE3_TARBALL")
    if not source:
        print(
            "[WARN] No stage3 source configured. "
            "Set it in the TUI (Stage3 source) or via GENTOO_STAGE3_TARBALL. Skipping extraction.",
        )
        return

    tarball = source

    # If it's a URL, download it to a temporary location first.
    if source.startswith("http://") or source.startswith("https://"):
        dest = os.path.join("/tmp", os.path.basename(source) or "gentoo-stage3.tar.xz")
        print(f"[STEP] Downloading stage3 from {source} to {dest}")
        run_cmd(["wget", "-O", dest, source], dry_run=dry_run)
        tarball = dest

    if not os.path.exists(tarball):
        print(f"[ERROR] Stage3 tarball not found: {tarball}")
        sys.exit(1)

    # If directory is not empty, warn but continue – user might be resuming.
    if not dry_run and os.listdir(GENTOO_ROOT):
        print(f"[WARN] {GENTOO_ROOT} is not empty – stage3 will be extracted on top.")

    cmd = [
        "tar",
        "xpf",
        tarball,
        "-C",
        GENTOO_ROOT,
        "--xattrs-include=*",
        "--numeric-owner",
    ]
    run_cmd(cmd, dry_run=dry_run)

    # After root filesystem is in place and mounts exist, generate fstab skeleton.
    generate_fstab(cfg, dry_run=dry_run, root=GENTOO_ROOT)


def configure_base_system(cfg: GentooInstallConfig, dry_run: bool) -> None:
    print("\n[STEP] Configuring base system (partial implementation)")
    print("Setting up chroot mounts and preparing for further configuration.")

    # Bind-mount /dev, /proc, /sys, /run into the stage3 root so that
    # subsequent chrooted commands behave like a normal system.
    setup_chroot_mounts(dry_run=dry_run, root=GENTOO_ROOT)

    # TODO: inside the chroot we should configure make.conf, profiles, locale,
    # timezone, kernel build method based on cfg.kernel, and network based on
    # cfg.network_mode. For now we just log the intended choices.
    print(f"Selected kernel mode: {cfg.kernel}")
    print(f"Network mode: {cfg.network_mode}")
    run_cmd(["echo", "would configure base Gentoo system"], dry_run)


def install_bootloader(cfg: GentooInstallConfig, dry_run: bool) -> None:
    print("\n[STEP] Installing bootloader (stub)")
    print(f"Selected bootloader: {cfg.bootloader}")
    print("TODO: install and configure the chosen bootloader.")
    run_cmd(["echo", "would install bootloader", cfg.bootloader], dry_run)


def install_desktop_environment(cfg: GentooInstallConfig, dry_run: bool) -> None:
    print("\n[STEP] Installing desktop environment (stub)")
    profile = DESKTOP_PROFILES.get(cfg.desktop_profile)
    if not profile:
        print(f"Unknown desktop profile: {cfg.desktop_profile}, skipping.")
        return

    if profile.name == "none":
        print("No desktop environment selected; leaving system console-only.")
        return

    print(f"Selected profile: {profile.name} - {profile.description}")
    print("Packages to install:", ", ".join(profile.packages))
    print("Services to enable:", ", ".join(profile.services) if profile.services else "(none)")

    run_cmd(["echo", "would emerge", *profile.packages], dry_run)
    for svc in profile.services:
        run_cmd(["echo", f"would enable service {svc}"], dry_run)


def finalize_install(cfg: GentooInstallConfig, dry_run: bool) -> None:
    print("\n[STEP] Finalizing installation (stub)")
    print("TODO: set root password, create user, add user to wheel if requested, enable basic services, configure swap and unmount.")
    if cfg.swap_partition:
        print(f"Swap partition selected: {cfg.swap_partition}")
    if cfg.user_is_sudoer:
        print("User will be added to wheel/sudo group.")
    run_cmd(["echo", "would finalize install, set passwords, configure swap and reboot"], dry_run)


def run_install(cfg: GentooInstallConfig, dry_run: bool) -> None:
    print("\n=== Starting installation pipeline ===")
    prepare_disks(cfg, dry_run=dry_run)
    install_stage3(cfg, dry_run=dry_run)
    configure_base_system(cfg, dry_run=dry_run)
    install_bootloader(cfg, dry_run=dry_run)
    install_desktop_environment(cfg, dry_run=dry_run)
    finalize_install(cfg, dry_run=dry_run)
    print("\n=== Pipeline finished ===")
    if dry_run:
        print("Note: this was a dry-run. No real changes were made.")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gentoo text installer (early prototype)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run commands instead of dry-run.",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    dry_run = not args.execute

    if dry_run:
        print("[INFO] Running in dry-run mode. Use --execute to perform real actions (dangerous!).")

    # Start with empty config and run the curses TUI, similar in spirit to archinstall.
    cfg = GentooInstallConfig()
    try:
        should_install = run_tui(cfg, dry_run=dry_run)
    except Exception as exc:  # fallback if TUI fails for some reason
        print(f"[WARN] TUI failed ({exc!r}), falling back to line-based prompts.")
        cfg = collect_config()
        should_install = True

    if not should_install:
        print("Installation aborted from TUI.")
        return 1

    run_install(cfg, dry_run=dry_run)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

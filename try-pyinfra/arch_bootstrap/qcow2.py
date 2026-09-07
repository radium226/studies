"""
Build an Arch Linux qcow2 image and bootstrap it via pyinfra.
"""

import subprocess
from pathlib import Path

from arch_bootstrap.image import (
    bootstrap_via_chroot,
    extract_boot_files,
    log,
    mounted_rootfs_image,
    prepare_rootfs_image,
)


def create(
    *,
    build_dir: Path,
    size: str = "4G",
    user: str = "arch",
    public_key_file: Path,
    recreate: bool = False,
) -> Path:
    """
    Build (or, if it already exists, update in place) an Arch Linux qcow2
    image bootstrapped via pyinfra.

    By default an existing image is just re-mounted and re-bootstrapped --
    the pyinfra deploy is idempotent (already-satisfied operations report
    "No changes", as seen on the Vagrant target), so this is a cheap way
    to re-apply bootstrap.py after editing it. Pass recreate=True to wipe
    and rebuild the rootfs from scratch instead.
    """
    image_dir = build_dir / "qcow2"
    image_dir.mkdir(parents=True, exist_ok=True)
    image = image_dir / "arch.qcow2"
    mountpoint = image_dir / "mnt"

    if image.exists() and recreate:
        log(f"Removing existing {image}")
        image.unlink()

    if not image.exists():
        log(f"Creating {image} ({size})...")
        subprocess.run(["qemu-img", "create", "-f", "qcow2", str(image), size], check=True)
        prepare_rootfs_image(image, cache_dir=build_dir / "cache")
    else:
        log(f"Updating existing {image}...")

    with mounted_rootfs_image(image, mountpoint):
        bootstrap_via_chroot(
            mountpoint,
            user=user,
            public_key_file=public_key_file,
            extra_data={"install_kernel": "true"},
        )

    log(f"Done: {image}")
    log("Boot it with: mise run qcow2:boot")
    return image


def boot(*, build_dir: Path, memory: str = "2G", ssh_port: int = 2222, ui: bool = False) -> None:
    """
    Boot the qcow2 image with qemu (rootless -- /dev/kvm is
    world-accessible). Boots via direct kernel/initrd passthrough rather
    than a bootloader: the image has a single plain ext4 partition (no
    ESP, no GRUB/systemd-boot installed), and -kernel/-initrd sidesteps
    needing either.

    ui=True opens a graphical qemu window (its own virtual VGA console,
    with a normal login prompt -- systemd starts a getty on tty1
    regardless of which console is on the kernel command line) instead of
    attaching the serial console to this terminal.
    """
    image_dir = build_dir / "qcow2"
    image = image_dir / "arch.qcow2"
    if not image.exists():
        raise RuntimeError(f"{image} does not exist -- run `mise run qcow2:create` first")

    vmlinuz, initramfs = extract_boot_files(image, image_dir / "boot")

    log(f"Booting {image}...")
    log(f"SSH will be reachable at localhost:{ssh_port} once it's up.")
    if ui:
        log("Opening a qemu window; close it (or power off the VM) to quit.")
    else:
        log("Serial console is attached to this terminal; quit qemu with Ctrl-A X.")
    subprocess.run(
        [
            "qemu-system-x86_64",
            "-machine",
            "q35,accel=kvm:tcg",
            "-cpu",
            "max",
            "-m",
            memory,
            "-drive",
            f"file={image},format=qcow2,if=virtio",
            "-kernel",
            str(vmlinuz),
            "-initrd",
            str(initramfs),
            "-append",
            # systemd.firstboot=no: without hostname/locale/timezone/root
            # password configured during bootstrap, systemd-firstboot
            # blocks the whole boot on an interactive wizard over the
            # serial console (confirmed: sat there prompting for a
            # timezone). This disables that wizard outright rather than
            # trying to pre-satisfy every condition it checks.
            "root=/dev/vda1 rw console=ttyS0 systemd.firstboot=no",
            "-nic",
            f"user,model=virtio-net-pci,hostfwd=tcp::{ssh_port}-:22",
            *([] if ui else ["-nographic"]),
        ],
    )

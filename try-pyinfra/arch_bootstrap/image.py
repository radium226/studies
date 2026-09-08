"""
Build a bootable-enough Arch Linux rootfs image (qcow2, and later SD card)
and bootstrap it via the shared pyinfra deploy, using a rootless
guestfish/guestmount build combined with a sudo-scoped chroot step.

Why sudo is needed only for the chroot step: the disk image itself is
built entirely rootless via guestfish (its appliance runs as root only
inside a disposable VM). But actually configuring the rootfs (installing
packages, running package hooks) needs a real chroot with /proc, /sys,
/dev, /run bound in, and chroot() itself requires CAP_SYS_CHROOT. An
unprivileged user namespace can't be granted that here because
guestmount's FUSE mount is locked against nested namespaces (bind- or
fresh-mounting anything under it from a child userns is refused by the
kernel), and systemd-tmpfiles (a mandatory pacman hook) hard-requires a
working /proc. sudo is scoped to just the chroot step; image creation and
partitioning stay rootless.
"""

import shutil
import subprocess
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

DEPLOY_SCRIPT = Path(__file__).resolve().parent / "deploy.py"

DEFAULT_MIRROR = "https://geo.mirror.pkgbuild.com"


def log(message: str) -> None:
    print(f">> {message}", flush=True)


def pyinfra_binary() -> str:
    binary = shutil.which("pyinfra")
    if not binary:
        raise RuntimeError("pyinfra not found on PATH (is the venv active / installed?)")
    return binary


def fetch_bootstrap_tarball(cache_dir: Path, *, mirror: str = DEFAULT_MIRROR) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    tarball = cache_dir / "archlinux-bootstrap-x86_64.tar.zst"
    if not tarball.exists() or tarball.stat().st_size == 0:
        log("Downloading Arch bootstrap tarball...")
        tmp = tarball.with_suffix(tarball.suffix + ".tmp")
        subprocess.run(
            [
                "curl",
                "-fSL",
                "--connect-timeout",
                "10",
                "-o",
                str(tmp),
                f"{mirror}/iso/latest/archlinux-bootstrap-x86_64.tar.zst",
            ],
            check=True,
        )
        tmp.rename(tarball)
    return tarball


def _guestfish(image: Path, *args: str, read_only: bool = False) -> subprocess.CompletedProcess:
    cmd = ["guestfish"]
    cmd += ["--ro"] if read_only else ["--rw"]
    cmd += ["-a", str(image), "--", *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def prepare_rootfs_image(image: Path, *, cache_dir: Path, mirror: str = DEFAULT_MIRROR) -> None:
    """
    Partition (single ext4 partition, MBR), extract the bootstrap tarball,
    and apply the minimal fixups needed to make pacman usable in a chroot.
    Everything here runs inside libguestfs's disposable appliance --
    rootless.
    """
    tarball = fetch_bootstrap_tarball(cache_dir, mirror=mirror)

    log(f"Partitioning and formatting {image}...")
    _guestfish(
        image,
        "run",
        ":",
        "part-disk",
        "/dev/sda",
        "mbr",
        ":",
        "mkfs",
        "ext4",
        "/dev/sda1",
        ":",
        "mount",
        "/dev/sda1",
        "/",
        ":",
        "tar-in",
        str(tarball),
        "/",
        "compress:zstd",
    )

    # The tarball wraps everything in a root.x86_64/ directory; move its
    # contents up to /. Moved entry-by-entry (not glob) because guestfish's
    # glob expansion appends a trailing slash to symlinks like
    # bin -> usr/bin, which then fails as ENOTDIR.
    result = _guestfish(
        image, "run", ":", "mount-ro", "/dev/sda1", "/", ":", "ls", "/root.x86_64", read_only=True
    )
    entries = result.stdout.split()

    mv_args: list[str] = []
    for entry in entries:
        mv_args += [":", "mv", f"/root.x86_64/{entry}", f"/{entry}"]
    _guestfish(image, "run", ":", "mount", "/dev/sda1", "/", *mv_args, ":", "rmdir", "/root.x86_64")

    log("Applying chroot-usability fixups...")
    mirrorlist_line = f"echo 'Server = {mirror}/$repo/os/$arch' >> /etc/pacman.d/mirrorlist"
    # NOTE: SigLevel=Never skips package signature verification. This is a
    # deliberate PoC shortcut (avoids seeding a pacman-key keyring) -- not
    # something to carry into a real deployment.
    pacman_conf_fixup = (
        r"sed -i -e '/^\[options\]/a DisableSandbox' -e 's/^SigLevel.*/SigLevel = Never/' "
        "/etc/pacman.conf"
    )
    _guestfish(
        image,
        "run",
        ":",
        "mount",
        "/dev/sda1",
        "/",
        ":",
        "mknod-c",
        "0666",
        "1",
        "3",
        "/dev/null",
        ":",
        "mknod-c",
        "0666",
        "1",
        "5",
        "/dev/zero",
        ":",
        "mknod-c",
        "0666",
        "1",
        "7",
        "/dev/full",
        ":",
        "mknod-c",
        "0666",
        "1",
        "8",
        "/dev/random",
        ":",
        "mknod-c",
        "0666",
        "1",
        "9",
        "/dev/urandom",
        ":",
        "mknod-c",
        "0666",
        "5",
        "0",
        "/dev/tty",
        ":",
        "mknod-c",
        "0600",
        "5",
        "1",
        "/dev/console",
        ":",
        "mknod-c",
        "0666",
        "5",
        "2",
        "/dev/ptmx",
        ":",
        "sh",
        mirrorlist_line,
        ":",
        "sh",
        pacman_conf_fixup,
    )


def extract_boot_files(image: Path, dest_dir: Path) -> tuple[Path, Path]:
    """
    Pull the kernel and initramfs out of the image (rootless, via
    guestfish -- no mount needed for a couple of file reads).

    mkinitcpio's "autodetect" hook (the default HOOKS=) only bundles
    drivers for hardware present where it *runs* -- our build host
    (bind-mounted /sys via bootstrap_via_chroot), not the qemu VM this
    ends up booted in. bootstrap/operations.py works around this by
    forcing the virtio modules into MODULES= before installing the
    kernel, so the one (autodetect-built) initramfs-linux.img still ends
    up with what it needs. (Arch's linux.preset no longer builds a
    fallback image by default -- just PRESETS=('default') -- so there
    isn't one to use instead.)
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    vmlinuz = dest_dir / "vmlinuz-linux"
    initramfs = dest_dir / "initramfs-linux.img"
    _guestfish(
        image,
        "run",
        ":",
        "mount-ro",
        "/dev/sda1",
        "/",
        ":",
        "download",
        "/boot/vmlinuz-linux",
        str(vmlinuz),
        ":",
        "download",
        "/boot/initramfs-linux.img",
        str(initramfs),
        read_only=True,
    )
    return vmlinuz, initramfs


@contextmanager
def mounted_rootfs_image(image: Path, mountpoint: Path) -> Generator[Path]:
    """
    Mount `image`'s root partition at `mountpoint` via a rootless FUSE
    mount, and guarantee it's unmounted again on the way out (success or
    not) -- unlike the old bash version's trap-based cleanup, a Python
    context manager's `finally` reliably runs even when the code inside
    raises.

    allow_other: lets `sudo` + pyinfra (root) enter this FUSE mount at all
    (requires `user_allow_other` in /etc/fuse.conf).
    default_permissions: makes the KERNEL enforce permission bits using
    guestmount's reported stat() data, instead of trusting guestmount's
    own FUSE callback logic -- without it, root does not reliably get its
    usual DAC-bypass (e.g. `mkdir -p` under an existing 0700 dir owned by
    another uid fails with EACCES on the *existing* parent, because
    guestmount's opendir/mkdir callback doesn't special-case root; a
    single flat `mkdir` on the same path succeeds either way, since
    libguestfs relays that as one RPC that doesn't need to opendir() the
    parent first -- only multi-level -p style access hits this).
    """
    mountpoint.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "guestmount",
            "-o",
            "allow_other",
            "-o",
            "default_permissions",
            "-a",
            str(image),
            "-m",
            "/dev/sda1",
            str(mountpoint),
        ],
        check=True,
    )
    try:
        yield mountpoint
    finally:
        subprocess.run(["guestunmount", str(mountpoint)])
        with suppress(OSError):
            mountpoint.rmdir()


@contextmanager
def bound_kernel_mounts(mountpoint: Path) -> Generator[None]:
    """
    Bind /proc, /sys, /dev, /run into `mountpoint` (like arch-chroot
    does), for the duration of the `with` block -- needed for pacman's
    package hooks (systemd-tmpfiles etc.) to work inside the chroot.
    """
    abs_mountpoint = mountpoint.resolve()
    for sub in ("proc", "sys", "dev", "run"):
        subprocess.run(["sudo", "mount", "--bind", f"/{sub}", str(abs_mountpoint / sub)], check=True)
    subprocess.run(
        ["sudo", "cp", "/etc/resolv.conf", str(abs_mountpoint / "etc" / "resolv.conf")], check=True
    )
    try:
        yield
    finally:
        for sub in ("proc", "sys", "dev", "run"):
            subprocess.run(["sudo", "umount", str(abs_mountpoint / sub)])


def bootstrap_via_chroot(
    mountpoint: Path,
    *,
    user: str,
    public_key_file: Path,
    extra_data: dict[str, str] | None = None,
) -> None:
    """
    Run the shared pyinfra bootstrap deploy against a mounted rootfs via
    pyinfra's @chroot connector. Runs pyinfra itself as root (via sudo) so
    its own bare `chroot` calls succeed -- chroot() requires
    CAP_SYS_CHROOT, which we don't have rootlessly here (see module
    docstring).
    """
    abs_mountpoint = mountpoint.resolve()
    chroot_target = f"@chroot{abs_mountpoint}"

    data = {"user": user, "public_key_file": str(public_key_file), "chrooted": "true"}
    data.update(extra_data or {})
    data_args = [arg for key, value in data.items() for arg in ("--data", f"{key}={value}")]

    with bound_kernel_mounts(mountpoint):
        subprocess.run(
            ["sudo", pyinfra_binary(), "-y", chroot_target, str(DEPLOY_SCRIPT), *data_args],
            check=True,
        )

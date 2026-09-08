"""
Shared pyinfra deploy: the bare minimum to make a fresh Arch Linux rootfs
bootable and SSH-reachable.

    - a non-root user with an authorized SSH key
    - openssh installed and sshd enabled
    - systemd-networkd/-resolved enabled with DHCP on any wired interface

Used identically against all three targets (qcow2 chroot, SD card chroot,
Vagrant over SSH) so there is exactly one place that defines "bootstrapped".
"""

import io

from pyinfra import host
from pyinfra.api import QuoteString, StringCommand, deploy, operation
from pyinfra.facts.pacman import PacmanPackages
from pyinfra.operations import files, pacman, server, systemd

from arch_bootstrap.facts import SystemdUnitEnabled


@operation()
def ensure_service_enabled(service: str):
    """
    `systemd.service(enabled=True)`'s own idempotency check is broken
    under chroot (see facts.SystemdUnitEnabled docstring), so this uses
    the working `is-enabled`-based fact instead -- genuinely idempotent
    on every target.
    """
    if host.get_fact(SystemdUnitEnabled, service=service):
        host.noop(f"{service} is already enabled")
        return
    yield StringCommand("systemctl", "enable", QuoteString(service))


@deploy("Bootstrap Arch Linux")
def bootstrap(
    user: str, public_key_file: str, chrooted: bool = False, install_kernel: bool = False
) -> None:
    # install_kernel: qcow2/SD-card need an actual kernel (+ initramfs) to
    # be bootable under qemu; Vagrant boxes already have one and don't
    # need this.
    packages = ["openssh"]
    if install_kernel:
        packages.append("linux")

    # pacman.update() (`pacman -Sy`) is hardcoded is_idempotent=False in
    # pyinfra -- it always reports as a change, since syncing a mirror
    # isn't something pyinfra can know is a no-op ahead of time. `pacman
    # -Q` (PacmanPackages) is a pure local query though, so we can check
    # ourselves whether these are already installed and only sync (and
    # therefore only touch the network) when we're actually about to
    # install something -- genuinely "No change" once everything's present.
    installed = host.get_fact(PacmanPackages)
    needs_sync = any(package not in installed for package in packages)

    if install_kernel and needs_sync:
        # mkinitcpio isn't in the base tarball -- it only comes in as a
        # dependency of `linux`, and pacman installs a package's whole
        # dependency tree, hooks included, as one atomic transaction. So
        # /etc/mkinitcpio.conf doesn't exist to edit until mkinitcpio
        # itself is actually installed, and by the time `linux` pulls it
        # in as a dependency below, linux's own post-install hook has
        # already run mkinitcpio with it. Installing mkinitcpio as its
        # own explicit step first is what makes "edit the config, then
        # install linux (which finds mkinitcpio already present and just
        # runs it against our edited config)" actually work.
        pacman.packages(name="Install mkinitcpio", packages=["mkinitcpio"], update=True)

        # mkinitcpio's "autodetect" hook (the default HOOKS=) only bundles
        # drivers for hardware present where mkinitcpio *runs* -- here,
        # our build host (bind-mounted /sys), not the qemu VM this image
        # ends up booted in. Explicitly forcing the virtio modules into
        # MODULES= sidesteps that: they're included regardless of what
        # autodetect sees. (Arch's default linux.preset no longer builds
        # a fallback image at all -- only PRESETS=('default') -- so
        # there's no fallback to fall back on instead.)
        #
        # Also drop the kms hook: it's for early graphics setup, which a
        # headless serial-console boot doesn't use, and without real GPU
        # firmware present it prints "possibly missing firmware" warnings
        # for every autodetected driver (nouveau/xe/i915 here) -- noise,
        # not a problem, but avoided at the source instead of leaving it.
        #
        # Deliberately a plain sed via server.shell, not files.line: the
        # latter corrupted the file (truncated it down to just the
        # replacement line -- some edge case in its "line not found yet,
        # about to replace" handling) when tried here.
        server.shell(
            name="Configure mkinitcpio for a headless VM",
            commands=[
                "sed -i "
                "'s/^MODULES=.*/MODULES=(virtio_pci virtio_blk virtio_scsi virtio_net virtio_console)/' "
                "/etc/mkinitcpio.conf",
                "sed -i "
                "'s/^HOOKS=.*/HOOKS=(base systemd autodetect microcode modconf keyboard sd-vconsole "
                "block filesystems fsck)/' /etc/mkinitcpio.conf",
            ],
        )

    pacman.packages(
        name=f"Install {', '.join(packages)}",
        packages=packages,
        # Already synced just above when install_kernel needed the
        # mkinitcpio pre-step; avoid a second, redundant `pacman -Sy`.
        update=needs_sync and not install_kernel,
    )

    if install_kernel and needs_sync:
        # Only reached when we just (re)installed something above, so
        # this can't yet be idempotent by itself (there's no cheap way to
        # inspect the existing initramfs' contents to know whether a
        # rebuild is actually needed) -- but it's at least scoped to only
        # the runs that could plausibly require it, rather than always.
        #
        # mkinitcpio exits 1 here even on a fully successful build
        # ("errors were encountered ... may not be complete") -- verified
        # directly: some of its own hook warnings (e.g. "no fsck helpers
        # found", missing vconsole.conf) get tallied into its error count
        # despite being harmless and printed as WARNING, not ERROR. So
        # check what actually matters -- did it write the image -- rather
        # than trusting its exit code.
        server.shell(
            name="Rebuild initramfs with virtio modules",
            commands=["mkinitcpio -p linux || true", "test -s /boot/initramfs-linux.img"],
        )

    server.user(
        name=f"Create user {user}",
        user=user,
        shell="/bin/bash",
        create_home=True,
        public_keys=[public_key_file],
    )

    # running: on a live target (Vagrant) True is correct -- the service
    # should actually be started/kept running, and pyinfra can see its
    # real state via `systemctl is-active`. Under chroot (qcow2/SD card)
    # there's no live systemd to query at all: `systemctl is-active`
    # returns nothing parseable there (confirmed: SystemdStatus fact is
    # just `{}`), so pyinfra can never see the service as "already
    # running" and would issue `start` on every single run -- harmless
    # (chroot gracefully no-ops it: exit 0, "Running in chroot, ignoring
    # command ...") but never reported as "No change", i.e. not
    # idempotent in pyinfra's own output. Passing running=None instead
    # skips the running/stopped check entirely (see
    # handle_service_control: running=True/False are the only branches
    # that yield a command).
    #
    # Enabling is handled separately below via ensure_service_enabled,
    # not via systemd.service(enabled=True): that built-in check is
    # *also* broken under chroot for the same reason (SystemdEnabled is
    # the same `systemctl show`-based fact), so it would unconditionally
    # re-run `systemctl enable` every time too.
    #
    # Do NOT hardcode running=False here -- on a live target that stops
    # an already-running service (e.g. it once cut the Vagrant SSH
    # connection by stopping sshd mid-deploy).
    running = None if chrooted else True

    systemd.service(
        name="Enable sshd",
        service="sshd.service",
        running=running,
    )
    ensure_service_enabled(name="Enable sshd (enabled=)", service="sshd.service")

    files.put(
        name="Configure DHCP on wired interfaces",
        src=io.StringIO(
            "[Match]\n"
            "Name=en* eth*\n"
            "\n"
            "[Network]\n"
            "DHCP=yes\n"
        ),
        dest="/etc/systemd/network/20-wired-dhcp.network",
        mode="644",
    )

    systemd.service(
        name="Enable systemd-networkd",
        service="systemd-networkd.service",
        running=running,
    )
    ensure_service_enabled(
        name="Enable systemd-networkd (enabled=)", service="systemd-networkd.service"
    )

    systemd.service(
        name="Enable systemd-resolved",
        service="systemd-resolved.service",
        running=running,
    )
    ensure_service_enabled(
        name="Enable systemd-resolved (enabled=)", service="systemd-resolved.service"
    )

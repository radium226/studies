"""
Boot the Arch Vagrant box and bootstrap it via pyinfra.

Unlike qcow2/sd-card, this needs no chroot/mount/sudo at all: pyinfra's
@vagrant connector reads `vagrant ssh-config` and talks to the
already-running box over plain SSH, using --sudo for the operations that
need root (generic/arch's default `vagrant` user has passwordless sudo).
"""

import subprocess
from pathlib import Path

from arch_bootstrap.image import DEPLOY_SCRIPT, log, pyinfra_binary


def create(*, vm_dir: Path, user: str = "arch", public_key_file: Path) -> None:
    log("Bringing up the Vagrant box...")
    subprocess.run(["vagrant", "up", "--provider=libvirt"], cwd=vm_dir, check=True)

    log("Bootstrapping over SSH via pyinfra...")
    subprocess.run(
        [
            pyinfra_binary(),
            "-y",
            "--sudo",
            "@vagrant",
            str(DEPLOY_SCRIPT),
            "--data",
            f"user={user}",
            "--data",
            f"public_key_file={public_key_file}",
        ],
        cwd=vm_dir,
        check=True,
    )

    log("Done. SSH in with:")
    log(f"  ssh -i {public_key_file.with_suffix('')} {user}@<vm ip, see `vagrant ssh-config`>")

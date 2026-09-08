from pathlib import Path

import click

DEFAULT_PUBLIC_KEY_FILE = str(Path.home() / ".ssh" / "id_rsa-life.pub")

_public_key_file_option = click.option(
    "--public-key-file",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
    default=DEFAULT_PUBLIC_KEY_FILE,
    show_default=True,
    help="SSH public key to authorize for the bootstrapped user.",
)
_user_option = click.option(
    "--user",
    default="arch",
    show_default=True,
    help="Name of the user to create.",
)


@click.group()
def cli() -> None:
    """Bootstrap Arch Linux images/VMs via pyinfra."""


@cli.group()
def qcow2() -> None:
    """qcow2 image target."""


@qcow2.command("create")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=lambda: Path.cwd() / "build",
    show_default="./build",
    help="Where to put the built image (and the downloaded rootfs tarball cache).",
)
@click.option("--size", default="4G", show_default=True, help="qcow2 image size (only used when creating).")
@click.option(
    "--recreate",
    is_flag=True,
    default=False,
    help="Wipe and rebuild the image from scratch, even if it already exists "
    "(default: update an existing image in place by re-running the bootstrap deploy against it).",
)
@_user_option
@_public_key_file_option
def qcow2_create(
    build_dir: Path, size: str, recreate: bool, user: str, public_key_file: Path
) -> None:
    """Build an Arch Linux qcow2 image and bootstrap it via pyinfra."""
    from arch_bootstrap import qcow2 as qcow2_mod

    qcow2_mod.create(
        build_dir=build_dir, size=size, user=user, public_key_file=public_key_file, recreate=recreate
    )


@qcow2.command("boot")
@click.option(
    "--build-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=lambda: Path.cwd() / "build",
    show_default="./build",
    help="Where the built image lives.",
)
@click.option("--memory", default="2G", show_default=True, help="RAM to give the VM.")
@click.option(
    "--ssh-port",
    type=int,
    default=2222,
    show_default=True,
    help="Host port to forward to the VM's SSH (22).",
)
@click.option(
    "--ui",
    is_flag=True,
    default=False,
    help="Open a graphical qemu window instead of attaching the serial console to this terminal.",
)
def qcow2_boot(build_dir: Path, memory: str, ssh_port: int, ui: bool) -> None:
    """Boot the qcow2 image with qemu."""
    from arch_bootstrap import qcow2 as qcow2_mod

    qcow2_mod.boot(build_dir=build_dir, memory=memory, ssh_port=ssh_port, ui=ui)


@cli.group()
def vm() -> None:
    """Vagrant VM target."""


@vm.command("create")
@click.option(
    "--vm-dir",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=lambda: Path.cwd() / "vm",
    show_default="./vm",
    help="Directory containing the Vagrantfile.",
)
@_user_option
@_public_key_file_option
def vm_create(vm_dir: Path, user: str, public_key_file: Path) -> None:
    """Boot the Arch Vagrant box and bootstrap it via pyinfra."""
    from arch_bootstrap import vm as vm_mod

    vm_mod.create(vm_dir=vm_dir, user=user, public_key_file=public_key_file)

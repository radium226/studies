"""
Entry point handed to the `pyinfra` CLI (see image.run_bootstrap_deploy /
vm.create). Not meant to be run directly -- it just reads the --data
pyinfra was given and calls the actual bootstrap() deploy.
"""

from pyinfra import host

from arch_bootstrap.operations import bootstrap

bootstrap(
    user=host.data.get("user"),
    public_key_file=host.data.get("public_key_file"),
    chrooted=host.data.get("chrooted", False),
    install_kernel=host.data.get("install_kernel", False),
)

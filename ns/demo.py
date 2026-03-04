#!/usr/bin/env python3
"""Manage ephemeral Linux network + mount namespaces via CLI."""

import asyncio
import os
import shutil
from pathlib import Path

import click

NS_DIR = Path("./ns")


@click.group()
def cli() -> None:
    """Namespace management demo."""


@cli.command()
@click.argument("name")
def create(name: str) -> None:
    """Create ephemeral network + mount namespaces kept alive as a foreground process.

    Ctrl+C tears everything down.
    """

    async def _create() -> None:
        NS_DIR.mkdir(parents=True, exist_ok=True)
        pid_file = NS_DIR / f"{name}.pid"

        proc = await asyncio.create_subprocess_exec(
            "unshare", "--net", "--mount", "--propagation", "private",
            "tail", "-f", "/dev/null",
        )
        pid_file.write_text(str(proc.pid))

        # Bind-mount a per-namespace copy of example.conf
        example_file = NS_DIR / f"{name}.example.conf"
        if not example_file.exists():
            shutil.copy2("./example.conf", example_file)

        ns_path = f"/proc/{proc.pid}/ns/mnt"

        def enter_mnt_ns() -> None:
            fd = os.open(ns_path, os.O_RDONLY)
            os.setns(fd, os.CLONE_NEWNS)
            os.close(fd)

        private_proc = await asyncio.create_subprocess_exec(
            "mount", "--make-rprivate", "/", preexec_fn=enter_mnt_ns,
        )
        await private_proc.wait()

        bind_proc = await asyncio.create_subprocess_exec(
            "mount", "--bind",
            str(example_file.resolve()), str(Path("./example.conf").resolve()),
            preexec_fn=enter_mnt_ns,
        )
        await bind_proc.wait()

        print(f"Namespace {name!r} alive (pid {proc.pid}). Ctrl+C to tear down.")

        try:
            await proc.wait()
        except asyncio.CancelledError:
            proc.terminate()
            await proc.wait()
        finally:
            pid_file.unlink(missing_ok=True)
            print(f"Namespace {name!r} torn down.")

    try:
        asyncio.run(_create())
    except KeyboardInterrupt:
        pass


@cli.command()
@click.argument("name")
def shell(name: str) -> None:
    """Enter a bash shell inside NAME's namespaces."""

    async def _shell() -> None:
        pid_file = NS_DIR / f"{name}.pid"
        if not pid_file.exists():
            raise SystemExit(f"Namespace {name!r} not found. Run `create {name}` first.")

        pid = pid_file.read_text().strip()

        def enter_ns() -> None:
            for ns_type, flag in [("mnt", os.CLONE_NEWNS), ("net", os.CLONE_NEWNET)]:
                fd = os.open(f"/proc/{pid}/ns/{ns_type}", os.O_RDONLY)
                os.setns(fd, flag)
                os.close(fd)

        proc = await asyncio.create_subprocess_exec(
            "bash",
            stdin=None,
            stdout=None,
            stderr=None,
            preexec_fn=enter_ns,
        )
        await proc.wait()

    asyncio.run(_shell())


if __name__ == "__main__":
    cli()

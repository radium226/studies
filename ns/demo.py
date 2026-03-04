#!/usr/bin/env python3
"""Manage ephemeral Linux network + mount namespaces via CLI."""

import asyncio
import functools
import pickle
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import click

NS_DIR = Path("./ns")


class Namespace:
    def __init__(self, name: str):
        """Look up an existing namespace by name (reads pid file)."""
        pid_file = NS_DIR / f"{name}.pid"
        if not pid_file.exists():
            raise FileNotFoundError(f"Namespace {name!r} not found.")
        self.name = name
        self.pid = int(pid_file.read_text().strip())

    @classmethod
    @asynccontextmanager
    async def create(cls, name: str):
        """Create ephemeral net+mount namespaces; tear down on exit."""
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

        try:
            yield cls(name)
        finally:
            proc.terminate()
            await proc.wait()
            pid_file.unlink(missing_ok=True)
            print(f"Namespace {name!r} torn down.")

    def _enter_ns(self) -> None:
        """Enter this namespace's mount + net namespaces (call in preexec_fn or after fork)."""
        for ns_type, flag in [("mnt", os.CLONE_NEWNS), ("net", os.CLONE_NEWNET)]:
            fd = os.open(f"/proc/{self.pid}/ns/{ns_type}", os.O_RDONLY)
            os.setns(fd, flag)
            os.close(fd)

    def enter(self, timeout: float = 30.0):
        """Decorator: run an async function inside this namespace with its own event loop.

        Forks a child process so that setns(CLONE_NEWNS) works (requires single-threaded).
        """
        def decorator(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                read_fd, write_fd = os.pipe()

                cwd = os.getcwd()

                pid = os.fork()
                if pid == 0:
                    os.close(read_fd)
                    try:
                        self._enter_ns()
                        os.chdir(cwd)
                        result = asyncio.run(
                            asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
                        )
                        os.write(write_fd, pickle.dumps(("ok", result)))
                    except Exception as exc:
                        os.write(write_fd, pickle.dumps(("err", exc)))
                    finally:
                        os.close(write_fd)
                        os._exit(0)
                else:
                    os.close(write_fd)
                    loop = asyncio.get_running_loop()
                    data = await loop.run_in_executor(None, os.read, read_fd, 1 << 20)
                    os.close(read_fd)
                    await loop.run_in_executor(None, os.waitpid, pid, 0)

                    tag, value = pickle.loads(data)
                    if tag == "err":
                        raise value
                    return value

            return wrapper
        return decorator


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
        async with Namespace.create(name) as ns:
            print(f"Namespace {name!r} alive (pid {ns.pid}). Ctrl+C to tear down.")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(_create())
    except KeyboardInterrupt:
        pass


@cli.command()
@click.argument("name")
def shell(name: str) -> None:
    """Enter a bash shell inside NAME's namespaces."""

    async def _shell() -> None:
        ns = Namespace(name)
        cwd = os.getcwd()

        def enter_ns() -> None:
            ns._enter_ns()
            os.chdir(cwd)

        proc = await asyncio.create_subprocess_exec(
            "bash",
            stdin=None,
            stdout=None,
            stderr=None,
            preexec_fn=enter_ns,
        )
        await proc.wait()

    asyncio.run(_shell())


@cli.command("write-example")
@click.argument("name")
def write_example(name: str) -> None:
    """Write the namespace name into example.conf inside NAME's namespaces."""
    ns = Namespace(name)

    @ns.enter(timeout=5.0)
    async def _write():
        loop = asyncio.get_event_loop()
        config_path = Path("./example.conf").resolve()
        await loop.run_in_executor(None, config_path.write_text, name)

    asyncio.run(_write())
    print(f"Wrote {name!r} to example.conf")


@cli.command("read-example")
@click.argument("name")
def read_example(name: str) -> None:
    """Read example.conf from inside NAME's namespaces."""
    ns = Namespace(name)

    @ns.enter(timeout=5.0)
    async def _read():
        loop = asyncio.get_event_loop()
        config_path = Path("./example.conf").resolve()
        return await loop.run_in_executor(None, config_path.read_text)

    content = asyncio.run(_read())
    print(f"Content: {content!r}")


if __name__ == "__main__":
    cli()

import pytest
from radium226.run.daemon import Execution, Executor, ExecutorConfig, ExecutionContext, FileDescriptor, ExitCode, Command
from pathlib import Path
import os
from typing import Callable
import asyncio
from asyncio.streams import StreamReader, StreamReaderProtocol
from asyncio import TaskGroup
from loguru import logger
from signal import SIGTERM


@pytest.fixture
def executor_config() -> ExecutorConfig:
    return ExecutorConfig.default()


@pytest.fixture
def executor(executor_config: ExecutorConfig) -> Executor:
    return Executor(config=executor_config)


@pytest.fixture
def sh_with_trap_and_for_loop_command() -> Command:
    return [
        "sh", 
        "-c", 
        """
            trap 'echo "Interrupted! "; exit 2' TERM
            for i in 1 2 3; do
                echo 'Hello... ${i}! ' 
                echo 'World... ${i}! ' >&2 
                sleep 1
            done
            exit 1
        """,
    ]

@pytest.fixture
def yes_command() -> Command:
    return [
        "yes",
    ]


@pytest.fixture
def tr_command() -> Command:
    return [
        "tr",
        "[:lower:]",
        "[:upper:]",
    ]


async def read_lines_from(fd: FileDescriptor) -> list[str]:
    lines = []
    reader = StreamReader()
    transport, _ = await asyncio.get_event_loop().connect_read_pipe(
        lambda: StreamReaderProtocol(reader), os.fdopen(fd, 'rb')
    )

    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            lines.append(data.decode('utf-8').strip())
    except:
        pytest.fail(f"Error reading from pipe: {fd}")
    finally:
        transport.close()
    return lines


async def write_lines_to(fd: FileDescriptor, lines: list[str], sleep_time_between_lines: int = 1) -> None:
    output_writer_transport, output_writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
        lambda: asyncio.streams.FlowControlMixin(), os.fdopen(fd, 'wb')
    )
    writer = asyncio.streams.StreamWriter(output_writer_transport, output_writer_protocol, None, asyncio.get_event_loop())
    try:
        for line in lines:
            writer.write((line + '\n').encode('utf-8'))
            await writer.drain()
            await asyncio.sleep(sleep_time_between_lines)
    finally:
        writer.close()


async def wait_for_execution(execution: Execution) -> tuple[ExitCode, list[str], list[str]]:
    return await asyncio.gather(
        execution.wait_for(),
        read_lines_from(execution.stdout),
        read_lines_from(execution.stderr),
    )


async def test_to_read_from_stderr_and_stdout(executor: Executor, sh_with_trap_and_for_loop_command: Command):
    execution = await executor.execute(context=ExecutionContext(
        command=sh_with_trap_and_for_loop_command,
    ))
    exit_code, stdout_lines, stderr_lines = await wait_for_execution(execution)
    assert exit_code == 1
    assert len(stdout_lines) == 3
    assert len(stderr_lines) == 3


async def test_kill(executor: Executor, sh_with_trap_and_for_loop_command: Command):
    execution = await executor.execute(context=ExecutionContext(
        command=sh_with_trap_and_for_loop_command,
    ))
    await asyncio.sleep(1)
    await execution.kill(signal=SIGTERM)
    exit_code = await execution.wait_for()
    assert exit_code == 2


async def test_to_write_to_stdin(executor: Executor, tr_command: Command):
    execution = await executor.execute(context=ExecutionContext(
        command=tr_command,
    ))
    await write_lines_to(execution.stdin, ["foo", "bar", "baz"])

    exit_code, stdout_lines, stderr_lines = await wait_for_execution(execution)
    assert exit_code == 0
    assert stdout_lines == ["FOO", "BAR", "BAZ"]
    assert stderr_lines == []


async def test_yes(executor: Executor, yes_command: Command):
    execution = await executor.execute(context=ExecutionContext(
        command=yes_command,
    ))

    async def read_from_stdout():
        reader = StreamReader()
        transport, _ = await asyncio.get_event_loop().connect_read_pipe(
            lambda: StreamReaderProtocol(reader), os.fdopen(execution.stdout, 'rb')
        )

        number_of_lines = 0

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                number_of_lines += 1
                if number_of_lines % 1_000_000 == 0:
                    logger.info(f"Read {number_of_lines} lines from stdout")
        finally:
            transport.close()

    read_from_stdout_task = asyncio.create_task(read_from_stdout())
    try:
        await asyncio.sleep(5)  # Let it run for a while
    finally:
        await execution.kill(signal=SIGTERM)
        await execution.wait_for()
        await read_from_stdout_task           
import asyncio
import os
import tempfile
from pathlib import Path

from radium226.run.daemon import RunnerManager, RunnerStatus
from radium226.run.shared.types import RunnerContext


async def test_simple_server_completion(runner_manager: RunnerManager):
    print("RunnerManager is running:", runner_manager)
    await asyncio.sleep(2)
    print("RunnerManager test completed.")


async def test_to_prepare_runner_and_run_it(runner_manager: RunnerManager):
    command = ["sh", "-c", "sleep 1; echo 'Hello, World!'; sleep 1; exit 0"]
    context = RunnerContext(
        command=command,
        user_id=os.getuid(),
        working_folder_path=Path.cwd(),
        environment_variables=dict(os.environ)
    )
    runner = await runner_manager.prepare_runner(context)
    
    assert runner.status == RunnerStatus.PREPARED
    assert runner.context.command == command
    
    # Simulate running the runner
    run_control = await runner.run()
    wait_for_task = asyncio.create_task(run_control.wait_for())
    await asyncio.sleep(1)  # Give some time for the runner to start

    assert runner.status == RunnerStatus.RUNNING

    await asyncio.wait_for(wait_for_task, timeout=None)

    assert runner.status == RunnerStatus.COMPLETED


async def test_runner_with_custom_working_directory(runner_manager: RunnerManager):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_folder_path = Path(temp_dir)
        
        context = RunnerContext(
            command=["pwd"],
            user_id=os.getuid(),
            working_folder_path=temp_folder_path,
            environment_variables=dict(os.environ)
        )
        
        runner = await runner_manager.prepare_runner(context)
        
        assert runner.status == RunnerStatus.PREPARED
        assert runner.context.working_folder_path == temp_folder_path
        
        # Capture stdout to verify pwd output
        captured_output = []
        
        class TestRunHandler:
            def on_stdout(self, runner, stdout: bytes) -> None:
                captured_output.append(stdout.decode().strip())
            
            def on_stderr(self, runner, stderr: bytes) -> None:
                pass
            
            def on_completed(self, runner, exit_code) -> None:
                pass
        
        run_control = await runner.run(TestRunHandler())
        exit_code = await run_control.wait_for()
        
        assert runner.status == RunnerStatus.COMPLETED
        assert exit_code == 0
        assert len(captured_output) == 1
        assert captured_output[0] == str(temp_folder_path)
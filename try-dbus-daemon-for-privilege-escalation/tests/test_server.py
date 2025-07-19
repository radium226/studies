import asyncio

from radium226.run.daemon import RunnerManager, RunnerStatus


async def test_simple_server_completion(runner_manager: RunnerManager):
    print("RunnerManager is running:", runner_manager)
    await asyncio.sleep(2)
    print("RunnerManager test completed.")


async def test_to_prepare_runner_and_run_it(runner_manager: RunnerManager):
    command = ["sh", "-c", "sleep 1; echo 'Hello, World!'; sleep 1; exit 0"]
    runner = await runner_manager.prepare_runner(command)
    
    assert runner.status == RunnerStatus.PREPARED
    assert runner.command == command
    
    # Simulate running the runner
    run_control = await runner.run()
    wait_for_task = asyncio.create_task(run_control.wait_for())
    await asyncio.sleep(1)  # Give some time for the runner to start

    assert runner.status == RunnerStatus.RUNNING

    await asyncio.wait_for(wait_for_task, timeout=None)

    assert runner.status == RunnerStatus.COMPLETED
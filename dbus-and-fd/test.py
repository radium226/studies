import sys
from subprocess import Popen
import signal
import asyncio
from dataclasses import dataclass
import os
import termios
import tty


def close_fd(fd: int, name: str) -> None:
    try:
        print(f"Closing file descriptor {name} ({fd})...")
        os.close(fd)
    except OSError as e:
        print(f"Error closing file descriptor {name} ({fd}): {e}")


async def redirect(source_fd: int, target_fd: int, context: str) -> None:
    try:
        loop = asyncio.get_event_loop()
        while True:
            data = await loop.run_in_executor(None, os.read, source_fd, 1024)
            print(f"[{context}] Data received ({data})...")
            if not data:
                break
            await loop.run_in_executor(None, os.write, target_fd, data)
    except asyncio.CancelledError as e:
        print(f"[{context}] Redirection task cancelled.")
        raise e
    finally:
        pass
        

async def main():
    stdout_read_fd, stdout_write_fd = os.pipe()
    stdin_read_fd, stdin_write_fd = os.pipe()

    process = await asyncio.create_subprocess_exec(
        "./tty.py",
        stdin=stdin_read_fd,
        stdout=stdout_write_fd,
    )
    # os.close(stdout_write_fd)  # Close the write end after passing it to the process
    

    async def wait_for_process():
        print("Waiting for process to finish...")
        exit_code = await process.wait()
        print(f"Process finished with exit code: {exit_code}")
        close_fd(stdout_read_fd, "stdout_read_fd")
        close_fd(stdout_write_fd, "stdout_write_fd")
        return exit_code
    
    async def redirect_stdin():
        print("Redirecting stdin...")
        await redirect(sys.stdin.fileno(), stdin_write_fd, "STDIN")
        print("Stdin has been closed! ")
        close_fd(stdin_write_fd, "stdin_write_fd")
        close_fd(stdin_read_fd, "stdin_read_fd")

    async def redirect_stdout():
        print("Redirecting stdout...")
        await redirect(stdout_read_fd, sys.stdout.fileno(), "STDOUT")
        print("Stdout has been closed! ")
        
    redirect_io_task = asyncio.gather(
        redirect_stdin(),
        redirect_stdout(),
    )

    def handle_sigint_signal():
        print("SIGINT signal received, sending signal to process...")
        process.send_signal(signal.SIGINT)

    # asyncio.get_event_loop().add_signal_handler(signal.SIGINT, handle_sigint_signal)

    exit_code = await wait_for_process()
    
    redirect_io_task.cancel()
    try:
        await redirect_io_task
    except asyncio.CancelledError:
        print("Redirection tasks were cancelled.")

    if sys.stdin.isatty():
        print("Restoring terminal settings...")
        # old_settings = termios.tcgetattr(sys.stdin.fileno())
        # termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        tty.setcbreak(0)
        termios.tcflush(sys.stdin.fileno(), termios.TCIOFLUSH)
    return exit_code
    


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"Exiting with code: {exit_code}")
    # sys.exit(exit_code)
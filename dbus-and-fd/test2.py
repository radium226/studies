import sys
import asyncio



async def main():

    process = await asyncio.create_subprocess_exec(
        "./tty.py",
        stdin=sys.stdin.fileno(),
        stdout=sys.stdout.fileno(),
    )

    await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
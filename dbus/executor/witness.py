#!/usr/bin/env python3

import sys


def app():
    if sys.stdin.isatty():
        print("[witness][STDIN] We are in TTY mode")
    else:
        print("[witness][STDIN] We are in pipe mode")

    if sys.stdout.isatty():
        print("[witness][STDOUT] We are in TTY mode")
    else:
        print("[witness][STDOUT] We are in pipe mode")


    try:
        # Read line by line and echo
        for line in sys.stdin:
            print(f"[witness] {line}", end='')  # end='' prevents double newlines
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        print("\n[witness][Exiting...", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"[witness][Error: {e}]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    app()
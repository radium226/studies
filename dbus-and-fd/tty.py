#!/usr/bin/env python3

import sys


if __name__ == "__main__":
    is_a_tty = sys.stdin.isatty()
    print(f"Is stdin a TTY? {is_a_tty}")
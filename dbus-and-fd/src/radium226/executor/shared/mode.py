from enum import StrEnum, auto
import sys

class Mode(StrEnum):

    TTY = auto()
    PIPE = auto()

    @classmethod
    def auto(cls):
        """Automatically determine the mode based on the environment."""
        if sys.stdin.isatty() and sys.stdout.isatty():
            return Mode.TTY
        else:
            return Mode.PIPE
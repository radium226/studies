from enum import StrEnum, auto
import sys

class Mode(StrEnum):

    TTY = auto()
    PIPE = auto()

    @classmethod
    def for_stdin(cls):
        """Automatically determine the mode based on the environment."""
        if sys.stdin.isatty():
            return Mode.TTY
        else:
            return Mode.PIPE
        
    @classmethod
    def for_stdout(cls):
        """Automatically determine the mode based on the environment."""
        if sys.stdout.isatty():
            return Mode.TTY
        else:
            return Mode.PIPE
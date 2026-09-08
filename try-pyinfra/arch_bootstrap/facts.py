"""
pyinfra's built-in SystemdEnabled/SystemdStatus facts both run
`systemctl show --property ...`, which needs a live systemd D-Bus
connection and returns nothing parseable under chroot (confirmed: `{}`
against a chrooted qcow2/SD-card target, even though the unit really is
enabled). `systemctl is-enabled <unit>` is a pure unit-file/symlink
query -- no daemon needed -- so it works correctly both live and
chrooted. This fact wraps that, so "enable this service" can be
genuinely idempotent on every target, not just live ones.
"""

from pyinfra.api import FactBase, QuoteString, StringCommand

_ENABLED_STATES = {"enabled", "enabled-runtime", "static", "alias", "indirect"}


class SystemdUnitEnabled(FactBase[bool]):
    @staticmethod
    def default() -> bool:
        return False

    def command(self, service: str) -> StringCommand:
        return StringCommand("systemctl", "is-enabled", QuoteString(service), "2>/dev/null", "||", "true")

    def process(self, output: list[str]) -> bool:
        state = output[0].strip() if output else ""
        return state in _ENABLED_STATES

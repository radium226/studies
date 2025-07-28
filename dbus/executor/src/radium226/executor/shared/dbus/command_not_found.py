from sdbus import DbusFailedError


class CommandNotFoundError(DbusFailedError):
    dbus_error_name = "radium226.CommandNotFound"
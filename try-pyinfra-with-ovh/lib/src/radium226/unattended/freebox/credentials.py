"""
Register this application on the Freebox and print its app token.

    python -m radium226.unattended.freebox.credentials

Someone has to confirm the request on the box's front panel, within about a
minute. The token can then be stored as `FREEBOX_APP_TOKEN`.
"""

import time

from .client import FreeboxClient


def main(timeout: int = 90) -> None:
    client = FreeboxClient(app_token="unused")
    app_token, track_id = client.authorize()
    print("Confirm the request on the Freebox front panel (physical button / screen)...")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.authorization_status(track_id)
        if status == "granted":
            print("Granted. Set it as FREEBOX_APP_TOKEN in mise.local.toml:")
            print(f'   FREEBOX_APP_TOKEN = "{app_token}"')
            return
        if status != "pending":
            raise SystemExit(f"Authorization {status}")
        time.sleep(2)
    raise SystemExit("Timed out waiting for the confirmation on the box")


if __name__ == "__main__":
    main()

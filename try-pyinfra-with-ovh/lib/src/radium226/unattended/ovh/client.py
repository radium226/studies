from ovh import Client


def make_client() -> Client:
    """
    Build an OVH client. Credentials are resolved by the SDK itself: first the
    `OVH_ENDPOINT`, `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` and
    `OVH_CONSUMER_KEY` environment variables, then `./ovh.conf`, `~/.ovh.conf`
    and `/etc/ovh.conf`.
    """
    return Client()

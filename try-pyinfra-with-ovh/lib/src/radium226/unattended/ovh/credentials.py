"""
Request an OVH consumer key allowed to manage DNS zones.

    python -m radium226.unattended.ovh.credentials

Needs `OVH_ENDPOINT` and `OVH_APPLICATION_KEY` (and the application secret).
The consumer key only becomes usable once its validation URL has been opened
and confirmed in a browser.
"""

from .client import make_client

METHODS = ["GET", "POST", "PUT", "DELETE"]


def request_consumer_key() -> dict:
    request = make_client().new_consumer_key_request()
    request.add_recursive_rules(METHODS, "/domain/zone")
    return request.request()


def main() -> None:
    result = request_consumer_key()
    print("1. Open this URL and validate the request (choose 'Unlimited' validity):")
    print(f"   {result['validationUrl']}")
    print("2. Then set it as OVH_CONSUMER_KEY in mise.local.toml:")
    print(f"   OVH_CONSUMER_KEY = \"{result['consumerKey']}\"")


if __name__ == "__main__":
    main()

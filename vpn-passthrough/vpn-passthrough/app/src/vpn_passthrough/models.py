from typing import NewType
from dataclasses import dataclass


User = NewType("User", str)


Password = NewType("Password", str)


@dataclass
class Auth:
    user: User
    password: Password


RegionID = NewType("RegionID", str)


Payload = NewType("Payload", str)


Signature = NewType("Signature", str)


@dataclass
class PayloadAndSignature:
    payload: Payload
    signature: Signature


AppName = NewType("AppName", str)
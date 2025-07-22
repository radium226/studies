"""CLI for echo0d daemon."""

import asyncio
import sys
from typing import NoReturn

from .service import Ech0Service


def app() -> NoReturn:
    print("Starting echo0d daemon...")
    
    service = Ech0Service()
    
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        print("\nDaemon stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"Error running daemon: {e}")
        sys.exit(1)
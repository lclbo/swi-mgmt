"""Application entry point — launches the HTTP API backend."""

from swi_mgmt.api.server import main

__all__ = ["main"]

if __name__ == "__main__":
    main()

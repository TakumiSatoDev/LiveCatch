"""Compatibility entry point: python livecatch.py [--background]."""
from livecatch_core.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())

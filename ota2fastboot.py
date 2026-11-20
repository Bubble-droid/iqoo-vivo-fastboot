#!/usr/bin/env python3
"""Executable entry point for the ota2fastboot package.

The implementation lives in the ``ota2fastboot/`` package; this file only
keeps ``./ota2fastboot.py`` working from a source checkout.
"""

from ota2fastboot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

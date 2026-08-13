#!/usr/bin/env python3
from __future__ import annotations

import sys

from install_claude import main


if __name__ == "__main__":
    raise SystemExit(main(["--validate", *sys.argv[1:]]))


"""`python -m desk run` and `python -m desk report`."""

from __future__ import annotations

import sys

from desk.daemon import main

if __name__ == "__main__":
    sys.exit(main())

"""`python -m layout_guard ...`: the same as the `layout-guard` command."""
import sys

from layout_guard.cli import main

if __name__ == "__main__":
    sys.exit(main())

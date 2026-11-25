"""Entry point of the standalone Nervus application (PyInstaller)."""

import sys

from nervus.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))

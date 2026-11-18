"""PyInstaller entry point for the command-line tool (mcsm.exe)."""
import sys

from mcsm.cli import main

sys.exit(main())

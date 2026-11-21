"""PyInstaller entry point for the command-line tool (mcsm.exe)."""
import multiprocessing
import sys

from mcsm.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()   # terrain is read in worker processes
    sys.exit(main())

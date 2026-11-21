"""PyInstaller entry point for the windowed app (Minecraft Server Master.exe)."""
import multiprocessing
import sys

from mcsm.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()   # terrain is read in worker processes
    sys.exit(main())

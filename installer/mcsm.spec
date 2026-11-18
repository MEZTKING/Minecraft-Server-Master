# PyInstaller spec: builds two programs that share one bundled Python runtime.
#
#   dist/MinecraftServerMaster/Minecraft Server Master(.exe)   windowed app (mcsm.gui)
#   dist/MinecraftServerMaster/mcsm(.exe)                      command line (mcsm.cli)
#
# Build (from the repository root):
#     pip install pyinstaller tzdata
#     pyinstaller installer/mcsm.spec --noconfirm
#
# See docs/INSTALLER.md.

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "installer", "icon.ico")

# Windows Python has no system time-zone database; bundle the `tzdata` package
# so `--tz Europe/London` works. Harmless elsewhere.
datas = [(os.path.join(ROOT, "mcsm", "assets"), os.path.join("mcsm", "assets"))]
hidden = []
try:
    datas += collect_data_files("tzdata")
    hidden.append("tzdata")
except Exception:
    pass

common = dict(pathex=[ROOT], datas=datas, hiddenimports=hidden,
              excludes=["unittest", "pydoc", "pytest"], noarchive=False)

gui_a = Analysis([os.path.join(ROOT, "installer", "launch_gui.py")], **common)
cli_a = Analysis([os.path.join(ROOT, "installer", "launch_cli.py")], **common)

gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)

gui_exe = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True,
              name="Minecraft Server Master", console=False, icon=ICON,
              version=os.path.join(ROOT, "installer", "version_info.txt") if sys.platform == "win32" else None)
cli_exe = EXE(cli_pyz, cli_a.scripts, [], exclude_binaries=True,
              name="mcsm", console=True, icon=ICON)

coll = COLLECT(gui_exe, cli_exe,
               gui_a.binaries, gui_a.datas,
               cli_a.binaries, cli_a.datas,
               name="MinecraftServerMaster")

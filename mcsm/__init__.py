"""mcsm -- Minecraft Server Master.

Parse a downloaded Minecraft server (folder or zip) into statistics, an
event timeline, and editor-ready markers for replay videos and music.

Entry points:
    mcsm.analyzer.analyze(root)  -> ServerData   (library use)
    mcsm.cli.main()                               (command line: ``mcsm``)
"""

__version__ = "0.2.0"

# Which build this is: written by the installer build (installer/build_windows.ps1) into
# mcsm/_build.py; empty when running from source.
try:
    from ._build import BUILD  # type: ignore
except ImportError:  # running from a source checkout
    BUILD: dict = {}


def version_string() -> str:
    """``0.2.0`` from source, ``0.2.0 (build 57, 3913aae, 2026-11-22)`` in an installed build."""
    bits = [f"build {BUILD['number']}" if BUILD.get("number") else "", BUILD.get("commit", ""), BUILD.get("date", "")]
    bits = [b for b in bits if b]
    return __version__ + (f" ({', '.join(bits)})" if bits else "")

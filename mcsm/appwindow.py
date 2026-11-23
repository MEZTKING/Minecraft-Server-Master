"""Open the app's pages (video editor, history viewer, live map) in a window of their own.

The pages are web pages served by the app on this computer. Instead of a tab in your everyday browser, they
open in a window of their own: no tabs, no address bar, its own taskbar entry, and a separate profile in
``%LOCALAPPDATA%/mcsm/app-window`` so your browser's extensions, zoom level and settings don't get in the way
(the window remembers its size there).

- **Edge or Chrome** (Chromium): their ``--app`` window.
- **Firefox** (no app mode): a separate profile whose ``userChrome.css`` hides the tab bar and toolbars, with
  start pages, default-browser checks and data reporting switched off in its ``user.js``.

Without any of them, or with the setting off (``app_window`` in the app's settings), the page opens in your
default browser as before.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser


def find_firefox() -> str | None:
    cands = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands.append(os.path.join(base, "Mozilla Firefox", "firefox.exe"))
    elif sys.platform == "darwin":
        cands.append("/Applications/Firefox.app/Contents/MacOS/firefox")
    for c in cands:
        if os.path.isfile(c):
            return c
    return shutil.which("firefox")


def find_browser() -> str | None:
    """Edge or Chrome (Chromium) on this computer, for app windows."""
    cands = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in cands:
        if os.path.isfile(c):
            return c
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def enabled() -> bool:
    from . import updater
    return bool(updater.load_settings().get("app_window", True))


def set_enabled(on: bool) -> None:
    from . import updater
    s = updater.load_settings()
    s["app_window"] = bool(on)
    updater.save_settings(s)


def profile_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "app-window")


def command(url: str, browser: str) -> list[str]:
    return [browser, f"--app={url}", f"--user-data-dir={profile_dir()}", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--disable-sync"]


# Firefox: the profile's look (no tabs or toolbars) and quiet settings
_USER_CHROME = """/* written by Minecraft Server Master: this profile only shows the app's pages */
#TabsToolbar, #nav-bar, #toolbar-menubar, #PersonalToolbar { visibility: collapse !important; }
#sidebar-box, #sidebar-main, #sidebar-splitter { display: none !important; }
"""
_USER_JS = """// written by Minecraft Server Master: this profile only shows the app's pages
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("browser.startup.page", 0);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("app.update.auto", false);
user_pref("extensions.autoDisableScopes", 15);
user_pref("browser.translations.automaticallyPopup", false);
user_pref("sidebar.revamp", false);
user_pref("browser.tabs.inTitlebar", 0);              // the normal Windows title bar: move, minimise, close
"""


def firefox_profile() -> str:
    d = os.path.join(profile_dir(), "firefox")
    os.makedirs(os.path.join(d, "chrome"), exist_ok=True)
    for path, text in ((os.path.join(d, "chrome", "userChrome.css"), _USER_CHROME), (os.path.join(d, "user.js"), _USER_JS)):
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return d


def firefox_command(url: str, firefox: str) -> list[str]:
    return [firefox, "-profile", firefox_profile(), "-new-window", url]


def open_page(url: str) -> str:
    """Open *url* (one of the app's own pages) in an app window, else the default browser. Returns how."""
    if enabled():
        browser = find_browser()
        firefox = None if browser else find_firefox()
        if browser or firefox:
            try:
                os.makedirs(profile_dir(), exist_ok=True)
                cmd = command(url, browser) if browser else firefox_command(url, firefox)
                flags = 0x00000008 if sys.platform.startswith("win") else 0      # DETACHED_PROCESS: outlives the app
                subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
                return "window"
            except OSError:
                pass
    webbrowser.open(url)
    return "browser"

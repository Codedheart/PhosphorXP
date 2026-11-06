"""Phosphor XP - a tiny always-on-top EXP/hour overlay for MapleStory Worlds (Classic)."""
import sys
import traceback


def _fatal(msg: str) -> None:
    """Show a message box (works under pythonw, where there is no console)."""
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk()
        r.withdraw()
        messagebox.showerror("Phosphor XP", msg)
        r.destroy()
    except Exception:
        print(msg, file=sys.stderr)


def _check_env() -> bool:
    if sys.version_info < (3, 10):
        _fatal("Phosphor XP needs Python 3.10 or newer (this is %d.%d).\n\n"
               "Install the latest Python from python.org (tick 'Add python.exe to PATH' and the "
               "'py launcher'), then start Phosphor XP with run.bat." % sys.version_info[:2])
        return False
    missing = []
    for mod, pkg in (("numpy", "numpy"), ("mss", "mss"), ("PIL", "pillow")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        _fatal("Missing packages: %s\n\nStart Phosphor XP with run.bat (it installs them), or run:\n"
               "  py -3 -m pip install %s" % (", ".join(missing), " ".join(missing)))
        return False
    return True


def main() -> int:
    if not _check_env():
        return 1
    try:
        from phosphor.ui import Overlay
        Overlay().run()
    except Exception:
        from phosphor.config import data_dir
        (data_dir() / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        _fatal("Phosphor XP crashed:\n\n" + traceback.format_exc(limit=3))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

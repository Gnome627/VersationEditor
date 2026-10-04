"""VersationEditor: dialog, quest and environment editor for Ex Machina.

    python main.py                  # window; game root is searched upwards
    python main.py --shot out.png   # open offscreen, save a screenshot, exit
"""
import sys


def main() -> int:
    if "--shot" in sys.argv:
        # build check without a window: open everything and save a screenshot
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        if os.name == "nt":
            os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
        from ve.app import MainWindow, make_app, open_game
        qapp = make_app()
        app = open_game(qapp)
        if app is None:
            return 2
        win = MainWindow(app)
        win.resize(1440, 880)
        win.show()
        for i in (1, 2, 0):
            win.show_tab(i)
            qapp.processEvents()
        win.grab().save(sys.argv[sys.argv.index("--shot") + 1])
        return 0
    from ve.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())

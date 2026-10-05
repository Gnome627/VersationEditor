"""VersationEditor: dialog, quest and environment editor for Ex Machina.

    python main.py                  # window; game root is searched upwards
    python main.py --shot out.png   # open offscreen, save a screenshot, exit
    python main.py --model-shot uralCab01 out.png   # render one model through OpenGL, no window
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
        for i in (1, 2, 3, 0):
            win.show_tab(i)
            qapp.processEvents()
        win.grab().save(sys.argv[sys.argv.index("--shot") + 1])
        return 0
    if "--model-shot" in sys.argv:
        # build check of the OpenGL path: nothing is put on screen
        i = sys.argv.index("--model-shot")
        from ve import glview
        from ve.app import make_app
        from ve.game import Game, find_root
        from ve.models import ModelLib
        make_app()
        root = find_root()
        if root is None:
            return 2
        lib = ModelLib(Game(root))
        rel = lib.file_of(sys.argv[i + 1])
        if rel is None:
            return 3
        img = glview.render_image(lib.doc(rel).gam, lambda n: lib.find_texture(rel, n), size=(900, 640))
        if img is None or not img.save(sys.argv[i + 2]):
            return 4
        return 0
    from ve.app import run
    return run()


if __name__ == "__main__":
    sys.exit(main())

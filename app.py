import sys
import main
import settings_gui

if __name__ == "__main__":
    if "--background" in sys.argv:
        main.main()
    else:
        app = settings_gui.App()
        app.mainloop()

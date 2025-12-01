import sys
import os
import asyncio
import qasync
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon # Import QIcon
from umdt.gui.sniffer_window import SnifferWindow

def _get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def main():
    app = QApplication(sys.argv)
    
    # Set application icon
    icon_path = _get_resource_path("umdt-sniff.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    # Setup qasync loop integration
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = SnifferWindow()
    # Set window icon (redundant with app icon, but good practice)
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    
    with loop:
        loop.run_forever()

if __name__ == "__main__":
    main()

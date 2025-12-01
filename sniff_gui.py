import sys
import asyncio
import qasync
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon # Import QIcon
from umdt.gui.sniffer_window import SnifferWindow

def main():
    app = QApplication(sys.argv)
    
    # Set application icon
    app.setWindowIcon(QIcon("umdt-sniff.ico"))
    
    # Setup qasync loop integration
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = SnifferWindow()
    # Set window icon (redundant with app icon, but good practice)
    window.setWindowIcon(QIcon("umdt-sniff.ico"))
    window.show()
    
    with loop:
        loop.run_forever()

if __name__ == "__main__":
    main()

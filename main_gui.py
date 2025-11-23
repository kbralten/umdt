import sys
import os
import asyncio
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QLabel, QTextEdit
from PySide6.QtGui import QIcon
import qasync
from umdt.core.controller import CoreController

# project icon (placed next to main scripts)
ICON_PATH = os.path.join(os.path.dirname(__file__), "umdt.ico")

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UMDT POC-03: Mock Loopback")
        self.resize(400, 300)

        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.label = QLabel("Status: Disconnected")
        self.layout.addWidget(self.label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.layout.addWidget(self.log_view)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self.on_connect)
        self.layout.addWidget(self.btn_connect)

        self.btn_send = QPushButton("Send Mock Data (0xDEADBEEF)")
        self.btn_send.clicked.connect(self.on_send)
        self.btn_send.setEnabled(False)
        self.layout.addWidget(self.btn_send)

        # Initialize Core using ConnectionManager (URI)
        self.controller = CoreController(uri="mock://")
        self.controller.add_observer(self.on_log_update)

    @qasync.asyncSlot()
    async def on_connect(self):
        if not self.controller.running:
            await self.controller.start()
            self.label.setText("Status: Connected (Mock)")
            self.btn_connect.setText("Disconnect")
            self.btn_send.setEnabled(True)
        else:
            await self.controller.stop()
            self.label.setText("Status: Disconnected")
            self.btn_connect.setText("Connect")
            self.btn_send.setEnabled(False)

    @qasync.asyncSlot()
    async def on_send(self):
        data = bytes.fromhex("DEADBEEF")
        await self.controller.send_data(data)

    def on_log_update(self, entry):
        # This is called from the async loop, but PySide allows thread-safe updates 
        # if we are in the same thread (which we are, thanks to qasync)
        msg = f"[{entry['direction']}] {entry['data']}"
        self.log_view.append(msg)

def main():
    app = QApplication(sys.argv)
    # set application icon early so it becomes the taskbar icon on Windows
    if os.path.exists(ICON_PATH):
        try:
            app.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    if os.path.exists(ICON_PATH):
        try:
            window.setWindowIcon(QIcon(ICON_PATH))
        except Exception:
            pass
    window.show()

    with loop:
        loop.run_forever()

if __name__ == "__main__":
    main()

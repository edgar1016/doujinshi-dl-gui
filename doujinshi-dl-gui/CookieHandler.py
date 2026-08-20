import os
import sys

from PyQt6.QtWidgets import (
    QVBoxLayout, QLabel, QPushButton, 
    QLineEdit,QWidget,QMessageBox
)
from PyQt6.QtCore import Qt

from CustomTitleBar import CustomTitleBar


def _res(name: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources", name)

class CookieHandler(QWidget):

    def __init__(self, main_window, settings):
        super().__init__()
        self.main_window = main_window
        self.settings = settings

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.MSWindowsFixedSizeDialogHint | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('API Token')
        self.initUI()

    def initUI(self):
        # Create a custom title bar
        custom_title_bar = CustomTitleBar(self, self.settings)
        try:
            with open(_res("styles.qss"), encoding="utf-8") as f:
                _qss = f.read()
            _res_uri = os.path.dirname(_res("x")).replace("\\", "/")
            self.setStyleSheet(_qss.replace(":/resources", _res_uri))
        except OSError:
            pass

        layout = QVBoxLayout()

        # Add the custom title bar to the layout
        layout.addWidget(custom_title_bar)

        self.api_token_label = QLabel('API Token:', self)
        self.api_token_le = QLineEdit(self)
        self.api_token_le.setPlaceholderText('Enter your API token')

        self.btn = QPushButton('Submit', self)
        self.btn.clicked.connect(self.set_api_token)

        layout.addWidget(self.api_token_label)
        layout.addWidget(self.api_token_le)
        layout.addWidget(self.btn)

        self.setLayout(layout)
        self.setGeometry(300, 300, 360, 140)
        self.center()
        self.show()
    
    def center(self):
        qr = self.frameGeometry()
        cp = self.screen().availableGeometry().center()

        qr.moveCenter(cp)
        self.move(qr.topLeft())

    
    def set_cookie(self):
        self.set_api_token()

    def show_empty_fields_dialog(self):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText("API token is empty. Please enter your token.")
        msg.setWindowTitle("Empty Fields")
        msg.exec()

    def set_api_token(self):
        token_value = self.api_token_le.text().strip()
        if not token_value:
            self.show_empty_fields_dialog()
            return

        token_command = f' --token "{token_value}"'
        print(token_command)
        self.main_window.run_specific_command(token_command)
        self.close()

import os
import sys

from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QLabel,
    QPushButton, QWidget, QMenu,QHBoxLayout, 
    QSpacerItem, QSizePolicy, QMenuBar, QToolButton
)

from PyQt6.QtGui import QAction, QMouseEvent, QPixmap
from PyQt6.QtCore import Qt, QSize
from functools import partial


def _res(name: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources", name)


class PasteToolButton(QToolButton):
    def __init__(self, click_handler):
        super().__init__()
        self.setText("Paste")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._click_handler = click_handler

    def mousePressEvent(self, event: QMouseEvent):
        if self._click_handler:
            self._click_handler(event)
        super().mousePressEvent(event)

class CustomTitleBar(QWidget):
    def __init__(self, main_window, settings):
        super().__init__()
        self.parent = main_window
        self.main_window = main_window
        self.settings = settings
        self.remember_position = self.settings.value("remember_main_windows_position", False, type=bool)

        self.layout = QVBoxLayout()  # Use QHBoxLayout for horizontal layout

        self.title_layout = QHBoxLayout()

        version = '1.0'

        # Title label with style
        if isinstance(self.parent, QMainWindow):
            self.title_label = QLabel('Doujinshi-dl-gui' + f' <span style="color: #ed2553;">v{version}</span>')
            self.title_label.setTextFormat(Qt.TextFormat.RichText)
            self.title_label.setStyleSheet("font-size: 18px;")
        else:
            dialog_title = self.parent.windowTitle() if hasattr(self.parent, "windowTitle") else "Dialog"
            self.title_label = QLabel(dialog_title)

        self.title_label.setObjectName("TitleLabel")  # Apply a style object name

        # Icon for the title label
        self.title_icon = QLabel()
        self.title_icon.setFixedSize(30, 30)
        
        # Icon for the title label
        self.title_icon = QLabel()
        icon = QPixmap(_res("logo.svg"))
        self.title_icon.setPixmap(icon.scaled(QSize(50, 50)))  # Set the size of the icon

        # Adjust the margins and spacing for the title_label and icon
        self.title_layout.setContentsMargins(0, 0, 0, 0)
        self.title_layout.setSpacing(-5)

        # Add the title_icon and title_label
        self.title_layout.addWidget(self.title_icon)
        self.title_layout.addStretch(0)
        self.title_layout.addWidget(self.title_label)

        # Add the title_label
        self.title_layout.addStretch(1)
        self.title_layout.addWidget(self.title_label)

        # Spacer to push buttons to the right
        self.title_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed))

        # Minimize button with style
        self.minimize_button = QPushButton("─")
        self.minimize_button.setObjectName("MinimizeButton")
        self.minimize_button.setFixedHeight(30)
        self.minimize_button.setFixedWidth(30)
        self.minimize_button.clicked.connect(self.minimize_window)
        self.title_layout.addWidget(self.minimize_button)

        # # Maximize/Restore button with style 
        # Reszing is disabled! TODO add proper scaling
        # self.maximize_restore_button = QPushButton("□")
        # self.maximize_restore_button.setObjectName("MaximizeRestoreButton")
        # self.maximize_restore_button.setFixedHeight(30)
        # self.maximize_restore_button.setFixedWidth(30)
        # self.maximize_restore_button.clicked.connect(self.toggle_maximize_restore)
        # self.title_layout.addWidget(self.maximize_restore_button)

        # Close button with style
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("CloseButton")
        self.close_button.setFixedHeight(30)
        self.close_button.setFixedWidth(30)
        self.close_button.clicked.connect(self.close_window)
        self.title_layout.addWidget(self.close_button)

        self.layout.addLayout(self.title_layout)

        if isinstance(self.parent, QMainWindow):
            # Create the menu bar
            menubar = QMenuBar(self)
            menubar.setNativeMenuBar(False)
            file_menu = menubar.addMenu("File")
            options_menu = QMenu("Options", self)
            file_menu.addMenu(options_menu)

            # Add action for manage preset
            set_manage_presets = QAction("Manage Presets", self)
            set_manage_presets.triggered.connect(self.main_window.manage_presets)
            options_menu.addAction(set_manage_presets)

            # Add clean language action
            clean_download_history_action = QAction("Clean Download History", self)
            clean_download_history_action.triggered.connect(self.clean_download_history)
            options_menu.addAction(clean_download_history_action)

            # Add clean language action 
            # Disabled since it currently doesn't do anything TODO
            # clean_language_action = QAction("Clean Language", self)
            # clean_language_action.triggered.connect(self.clean_language)
            # options_menu.addAction(clean_language_action)

            # Add action for setting cookie or API token depending on CLI mode
            self.auth_action = QAction("", self)
            self.auth_action.triggered.connect(self.set_cookie)
            options_menu.addAction(self.auth_action)
            self._sync_auth_action_text()

            # Add an action for setting default directory
            set_default_dir_action = QAction("Set Default Directory", self)
            set_default_dir_action.triggered.connect(self.set_default_directory)
            options_menu.addAction(set_default_dir_action)

            # Add an action for setting Language
            set_language_action = QAction("Set Language", self)
            set_language_action.triggered.connect(self.set_language)
            options_menu.addAction(set_language_action)

            # Add am action for remembering window position
            toggle_remember_window_position_action = QAction("Remember Window Position", self)
            toggle_remember_window_position_action.setCheckable(True)
            toggle_remember_window_position_action.setChecked(self.remember_position)
            toggle_remember_window_position_action.triggered.connect(self.toggle_remember_window_position)
            options_menu.addAction(toggle_remember_window_position_action)

            toggle_extension_bridge_action = QAction("Enable Extension Bridge", self)
            toggle_extension_bridge_action.setCheckable(True)
            toggle_extension_bridge_action.setChecked(self.main_window.extension_bridge_enabled)
            toggle_extension_bridge_action.triggered.connect(self.toggle_extension_bridge)
            options_menu.addAction(toggle_extension_bridge_action)

            self.external_console_action = QAction("Open External Console", self)
            self.external_console_action.setCheckable(True)
            self.external_console_action.setChecked(not self.main_window.hide_external_console)
            self.external_console_action.triggered.connect(self.main_window.set_external_console_visible)
            options_menu.addAction(self.external_console_action)

            self.show_terminal_action = QAction("Show Embedded Terminal", self)
            self.show_terminal_action.setCheckable(True)
            self.show_terminal_action.setChecked(self.main_window.show_terminal)
            self.show_terminal_action.triggered.connect(self.main_window.set_terminal_panel_visible)
            options_menu.addAction(self.show_terminal_action)

            # Add action for opening default directory
            open_default_directory_action = QAction("Open Default Directory", self)
            open_default_directory_action.triggered.connect(self.open_default_directory)
            file_menu.addAction(open_default_directory_action)

            # Adds presets to the menu bar
            self.preset_menu = menubar.addMenu("Presets")

            # Get preset names from settings
            self.populate_preset_menu()

            self.menuLayout = QHBoxLayout()
            self.menuLayout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            self.menuLayout.addWidget(menubar)

            # Adds paste action to the menu bar
            self.paste_button = PasteToolButton(self.paste_id)

            self.paste_button.setStyleSheet("""
                QToolButton#PasteButton {
                    background-color: #ED2553;
                    color: white;
                    border: 1px solid #ED2553;
                    border-radius: 4px;
                    padding: 6px 12px;
                }
                QToolButton#PasteButton:hover {
                    background-color: #f15478;
                    border-color: #f15478;
                }
            """)

            self.paste_button.setObjectName("PasteButton")
            self.menuLayout.addWidget(self.paste_button)

            self.layout.addLayout(self.menuLayout)
    
        self.setLayout(self.layout)

    def set_terminal_action_checked(self, checked: bool):
        if hasattr(self, "show_terminal_action"):
            self.show_terminal_action.setChecked(bool(checked))

    def set_console_action_checked(self, checked: bool):
        if hasattr(self, "external_console_action"):
            self.external_console_action.setChecked(bool(checked))

    def _sync_auth_action_text(self):
        if hasattr(self, "auth_action"):
            self.auth_action.setText("Set API Token")

    def load_preset(self, preset_name):
        self.main_window.load_preset(preset_name)

    def populate_preset_menu(self):
        self.preset_menu.clear()

        preset_groups = [g for g in self.settings.childGroups() if g.startswith("Preset_")]
        preset_displays = {g: g.replace("Preset_", "", 1).replace("-", " ") for g in preset_groups}

        if not preset_groups:  # Check if the list is empty
            # Add a placeholder action
            placeholder_action = QAction("No Presets", self.preset_menu)
            placeholder_action.setEnabled(False)
            self.preset_menu.addAction(placeholder_action)
            return

        custom_order = self.settings.value("PresetOrder", type=list)
        ordered_displays = []

        if custom_order and set(custom_order) == set(preset_groups):
            # Use custom order
            ordered_displays = [preset_displays[g] for g in custom_order if g in preset_displays]
        else:
            # Fallback to sorted
            ordered_displays = [preset_displays[g] for g in sorted(preset_groups, key=lambda x: x.lower())]

        for cleaned_preset_name in ordered_displays:
            preset_menu_item = QAction(cleaned_preset_name, self.preset_menu)
            preset_menu_item.triggered.connect(partial(self.load_preset, cleaned_preset_name))
            self.preset_menu.addAction(preset_menu_item)

    # Connected Actions
    def clean_language(self):
        self.main_window.clean_language()

    def clean_download_history(self):
        self.main_window.clean_download_history()

    def close_window(self):
        self.parent.close()

    def minimize_window(self):
        self.parent.showMinimized()

    def open_default_directory(self):
        self.main_window.open_default_directory()

    def paste_id(self, event: QMouseEvent):
        self.main_window.paste_and_append_text(event)

    def set_cookie(self):
        self.main_window.set_cookie()

    def set_default_directory(self):
        self.main_window.set_default_directory()

    def set_language(self):
        self.main_window.set_language()


    def toggle_maximize_restore(self):
        if self.parent().isMaximized():
            self.parent().showNormal()
        else:
            self.parent().showMaximized()

    def toggle_remember_window_position(self, checked):
        self.remember_position = checked
        self.settings.setValue("remember_main_windows_position", self.remember_position)

    def toggle_extension_bridge(self, checked):
        self.main_window.set_extension_bridge_enabled(checked)

    def contextMenuEvent(self, event):

        # Get the QMenuBar under the cursor
        menubar = self.findChild(QMenuBar)
        if not menubar:
            return

        # Map event position to global to get exact location
        global_pos = event.globalPos()
        local_pos = menubar.mapFromGlobal(global_pos)

        # Find which action was under the right-click
        action = menubar.actionAt(local_pos)

        if action and action.text() == "Presets":
            context_menu = QMenu(self)
            manage_action = context_menu.addAction("Manage Presets")

            selected_action = context_menu.exec(global_pos)
            if selected_action == manage_action:
                self.main_window.manage_presets()

        else:
            event.ignore()
    
    # Implement mouse event handlers to enable window dragging
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_position = event.globalPosition().toPoint()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.drag_start_position
            new_pos = self.parent.pos() + delta
            self.parent.move(new_pos)
            self.drag_start_position = event.globalPosition().toPoint()  # Update the drag start position
            event.accept()

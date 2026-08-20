from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton,
    QInputDialog, QMessageBox, QAbstractItemView, QListWidgetItem
)
from PyQt6.QtCore import Qt
import os
import sys

def _res(name: str) -> str:
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources", name)

class ManagePresetsDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.settings = main_window.settings
        self.setWindowTitle("Manage Presets")
        self.setWindowIcon(main_window.icon)
        self.resize(350, 400)

        # Try to apply main window styles
        try:
            with open(_res("styles.qss"), encoding="utf-8") as f:
                _qss = f.read()
            _res_uri = os.path.dirname(_res("x")).replace("\\", "/")
            self.setStyleSheet(_qss.replace(":/resources", _res_uri))
        except OSError:
            pass

        layout = QVBoxLayout(self)

        # List Widget (Drag and drop reordering)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self.list_widget)

        # Reload initially
        self.refresh_list()

        # Side Buttons (Add, Update, Rename, Delete, Sort)
        button_layout = QHBoxLayout()

        self.btn_add = QPushButton("Add")
        self.btn_add.clicked.connect(self.add_preset)
        button_layout.addWidget(self.btn_add)

        self.btn_update = QPushButton("Update")
        self.btn_update.clicked.connect(self.update_preset)
        self.btn_update.setToolTip("Update selected preset with current UI stats")
        button_layout.addWidget(self.btn_update)
        
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self.delete_preset)
        button_layout.addWidget(self.btn_delete)

        layout.addLayout(button_layout)

        bottom_layout = QHBoxLayout()
        self.btn_sort = QPushButton("Sort Alphabetically")
        self.btn_sort.clicked.connect(self.sort_alphabetically)
        bottom_layout.addWidget(self.btn_sort)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        bottom_layout.addWidget(self.btn_close)

        layout.addLayout(bottom_layout)

    def refresh_list(self):
        self.list_widget.clear()
        
        # Check for custom sorting order in settings
        custom_order = self.settings.value("PresetOrder", type=list)
        
        # Load all presets
        preset_groups = [g for g in self.settings.childGroups() if g.startswith("Preset_")]
        preset_displays = {g: g.replace("Preset_", "", 1).replace("-", " ") for g in preset_groups}

        ordered_displays = []
        if custom_order and set(custom_order) == set(preset_groups):
            # Use custom order
            ordered_displays = [preset_displays[g] for g in custom_order if g in preset_displays]
        else:
            # Fallback to sorted
            ordered_displays = [preset_displays[g] for g in sorted(preset_groups, key=lambda x: x.lower())]

        for display in ordered_displays:
            item = QListWidgetItem(display)
            self.list_widget.addItem(item)
            
    def _save_custom_order(self):
        # Read the current items
        current_displays = [self.list_widget.item(i).text() for i in range(self.list_widget.count())]
        ordered_groups = [f"Preset_{d.replace(' ', '-')}" for d in current_displays]
        self.settings.setValue("PresetOrder", ordered_groups)
        self.main_window.custom_title_bar.populate_preset_menu()

    def accept(self):
        self._save_custom_order()
        super().accept()

    def add_preset(self):
        preset_name, ok = QInputDialog.getText(self, 'New Preset', 'Enter your preset name:')
        if not ok or not preset_name.strip():
            return

        cleaned_preset_name = preset_name.strip().replace(" ", "-")
        group_name = f"Preset_{cleaned_preset_name}"
        
        if group_name in self.settings.childGroups():
            overwrite = QMessageBox.question(
                self, "Preset Exists",
                f"A preset named \"{preset_name.strip()}\" already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return

        self.settings.beginGroup(group_name)
        self.main_window.save_ui_states()
        self.settings.endGroup()
        
        # Try finding exist and just select if overwrite
        items = self.list_widget.findItems(preset_name.strip(), Qt.MatchFlag.MatchExactly)
        if not items:
            self.list_widget.addItem(QListWidgetItem(preset_name.strip()))
            self._save_custom_order()
        else:
            self.list_widget.setCurrentItem(items[0])

    def update_preset(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a preset to update.")
            return

        display_name = item.text()
        cleaned_preset_name = display_name.replace(" ", "-")

        confirm = QMessageBox.question(
            self, "Confirm Update",
            f"Are you sure you want to update the preset \"{display_name}\" with the current UI settings?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self.settings.beginGroup(f"Preset_{cleaned_preset_name}")
        self.main_window.save_ui_states()
        self.settings.endGroup()
        QMessageBox.information(self, "Updated", f"Preset \"{display_name}\" updated successfully.")

    def delete_preset(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a preset to delete.")
            return

        display_name = item.text()

        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete the preset \"{display_name}\"? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        cleaned_preset_name = display_name.replace(" ", "-")
        self.settings.beginGroup(f"Preset_{cleaned_preset_name}")
        self.settings.remove("")
        self.settings.endGroup()
        
        # Remove from UI
        self.list_widget.takeItem(self.list_widget.row(item))
        self._save_custom_order()

    def sort_alphabetically(self):
        self.list_widget.sortItems(Qt.SortOrder.AscendingOrder)
        self._save_custom_order()

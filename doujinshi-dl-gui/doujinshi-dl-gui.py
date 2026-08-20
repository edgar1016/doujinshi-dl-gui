import sys
import subprocess
import re
import os
import json
import queue
import shutil
import site
import sysconfig
import glob
from collections import deque
from typing import Optional

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QCheckBox, QInputDialog,
    QFileDialog, QFrame, QHBoxLayout, QGridLayout, QMessageBox,
    QComboBox, QPlainTextEdit, QStyle
)
from PyQt6.QtCore import QSettings, Qt, QTimer, QProcess, QProcessEnvironment, QStandardPaths
from PyQt6.QtGui import QIcon, QTextCursor

from CustomTitleBar import CustomTitleBar
from CookieHandler import CookieHandler
from ManagePresetsDialog import ManagePresetsDialog


def _res(name: str) -> str:
    """Resolve a resource file path for dev and packaged exe."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources", name)


BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 31363


class ExtensionBridgeServer(threading.Thread):
    def __init__(self, queue_ref, host=BRIDGE_HOST, port=BRIDGE_PORT):
        super().__init__(daemon=True)
        self.queue_ref = queue_ref
        self.host = host
        self.port = port
        self.httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())

    def _make_handler(self):
        queue_ref = self.queue_ref

        class BridgeRequestHandler(BaseHTTPRequestHandler):
            def _set_cors_headers(self, code=204):
                self.send_response(code)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_OPTIONS(self):
                self._set_cors_headers()

            def do_GET(self):
                if self.path == "/ping":
                    response = json.dumps({"status": "ready"}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                else:
                    self.send_error(404, "Not Found")

            def _read_json_payload(self):
                content_length = int(self.headers.get("Content-Length", 0))
                if not content_length:
                    return {}
                raw_data = self.rfile.read(content_length)
                if not raw_data:
                    return {}
                try:
                    return json.loads(raw_data.decode("utf-8"))
                except json.JSONDecodeError:
                    self.send_error(400, "Invalid JSON payload")
                    return None

            def do_POST(self):
                if self.path == "/gallery-id":
                    payload = self._read_json_payload()
                    if payload is None:
                        return

                    gallery_id = str(payload.get("galleryId", "")).strip()
                    clear_before_insert = bool(payload.get("clearBeforeInsert", False))

                    if not re.fullmatch(r"\d{1,7}", gallery_id):
                        self.send_error(400, "Invalid gallery ID")
                        return

                    queue_ref.put({
                        "type": "gallery-id",
                        "galleryId": gallery_id,
                        "clearBeforeInsert": clear_before_insert,
                    })
                    self._set_cors_headers()
                    return

                if self.path == "/run-command":
                    payload = self._read_json_payload()
                    if payload is None:
                        return

                    queue_ref.put({
                        "type": "run-command",
                        "clearIds": bool(payload.get("clearIds", False)),
                    })
                    self._set_cors_headers()
                    return

                self.send_error(404, "Not Found")

            def log_message(self, format, *args):
                # Silence default HTTP server logging
                return

        return BridgeRequestHandler

    def run(self):
        self.httpd.serve_forever()

    def shutdown(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setMenuBar(None)
        self.cookieHandler = None
        self.extension_bridge_server = None
        self.extension_bridge_queue = queue.Queue()
        self.extension_bridge_timer = None
        self.extension_bridge_enabled = True
        self.process = None
        self.command_queue = deque()
        self.current_command_text = None
        self.copy_feedback_timer = None
        
        # Store settings in %APPDATA%/doujinshi-dl-gui/
        settings_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        os.makedirs(settings_dir, exist_ok=True)
        settings_file = os.path.join(settings_dir, "settings.ini")
        self.settings = QSettings(settings_file, QSettings.Format.IniFormat)  # Create a QSettings instance

        self.extension_bridge_enabled = self.settings.value("extension_bridge_enabled", True, type=bool)
        self.show_terminal = self.settings.value("show_terminal_panel", False, type=bool)
        self.hide_external_console = self.settings.value("hide_external_console", True, type=bool)
        self._last_height_with_terminal = None
        self._last_height_without_terminal = None

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.init_ui()
        if self.extension_bridge_enabled:
            self.start_extension_bridge()
        else:
            self._set_bridge_status("Extension bridge disabled")

    def _candidate_script_paths(self):
        paths = []
        try:
            scripts_path = sysconfig.get_path("scripts")
            if scripts_path:
                paths.append(scripts_path)
        except Exception:
            pass

        try:
            user_base = site.getuserbase()
            if user_base:
                paths.append(os.path.join(user_base, "Scripts"))
        except Exception:
            pass

        for prefix in (sys.prefix, sys.base_prefix):
            scripts_dir = os.path.join(prefix, "Scripts")
            paths.append(scripts_dir)

        # Also scan common Windows install locations (e.g., LOCALAPPDATA\Programs\Python\Python311\Scripts)
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                pattern = os.path.join(local_appdata, "Programs", "Python", "*", "Scripts")
                paths.extend(glob.glob(pattern))

        return [p for p in paths if p]

    def _build_path_with_scripts(self, original_path):
        extra_paths = []
        for candidate in self._candidate_script_paths():
            if candidate and candidate not in original_path:
                extra_paths.append(candidate)
        combined_paths = os.pathsep.join(extra_paths + [original_path]) if original_path else os.pathsep.join(extra_paths)
        return combined_paths

    def _creation_flags(self):
        if os.name != "nt":
            return 0
        if self.hide_external_console:
            return subprocess.CREATE_NO_WINDOW
        return subprocess.CREATE_NEW_CONSOLE

    def _create_process_environment(self, env_vars: dict) -> QProcessEnvironment:
        process_env = QProcessEnvironment.systemEnvironment()
        for key, value in (env_vars or {}).items():
            if value is None:
                continue
            process_env.insert(str(key), str(value))
        return process_env

    def _refresh_layout_height(self, adjust_size: bool = False):
        layout = self.centralWidget().layout() if self.centralWidget() else None
        if layout:
            layout.activate()
        if adjust_size:
            current_width = self.width()
            self.adjustSize()
            self.resize(current_width, self.height())
        else:
            size_hint = self.minimumSizeHint()
            target_height = size_hint.height()
            self.resize(self.width(), target_height)

    def _update_queue_display(self):
        if not hasattr(self, "queue_display"):
            return
        if not self.command_queue:
            self.queue_display.setPlainText("Queue: empty")
            self.queue_display.setVisible(False)
            return
        max_show = 20
        pending = list(self.command_queue)
        lines = [f"Queue ({len(pending)} waiting):"]
        for cmd, _ in pending[:max_show]:
            lines.append(f"- {self._format_queue_command(cmd)}")
        if len(pending) > max_show:
            lines.append(f"… and {len(pending) - max_show} more")
        self.queue_display.setPlainText("\n".join(lines))
        self.queue_display.setVisible(True)

    def _append_terminal_text(self, text: str):
        if not text:
            return
        cursor = self.terminal_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.terminal_output.setTextCursor(cursor)
        self.terminal_output.insertPlainText(text)
        if not text.endswith("\n"):
            self.terminal_output.insertPlainText("\n")

    def _scroll_terminal_to_end(self):
        cursor = self.terminal_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.terminal_output.setTextCursor(cursor)
        self.terminal_output.ensureCursorVisible()

    def _read_process_output(self):
        if not self.process:
            return
        stdout = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        stderr = bytes(self.process.readAllStandardError()).decode(errors="replace")
        self._append_terminal_text(f"{stdout}{stderr}")
        self._scroll_terminal_to_end()
        if "main: All done." in stdout or "main: All done." in stderr:
            # Marker seen; if process already ended, ensure the next queued command starts.
            if not self.process or self.process.state() == QProcess.ProcessState.NotRunning:
                self._start_next_queued_command()

    def _process_finished(self, exit_code: int, status: QProcess.ExitStatus):
        status_text = "NormalExit" if status == QProcess.ExitStatus.NormalExit else "CrashExit"
        # Suppress the exit banner to keep output clean for the user
        self.process = None
        self.current_command_text = None
        self._start_next_queued_command()
        self._update_queue_display()

    def _start_next_queued_command(self):
        if self.process:
            return
        if not self.command_queue:
            return
        command, env = self.command_queue.popleft()
        self._start_embedded_process(command, env)
        self._update_queue_display()

    def copy_command_to_clipboard(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.command_preview.text())
        self._show_copy_feedback()

    def _copy_icon(self) -> QIcon:
        icon = QIcon.fromTheme("edit-copy")
        if not icon or icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        return icon

    def _copy_success_icon(self) -> QIcon:
        icon = QIcon.fromTheme("emblem-ok")
        if not icon or icon.isNull():
            icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        return icon

    def _find_duplicate_command_state(self, command: str):
        normalized = (command or "").strip()
        if not normalized:
            return None
        if self.current_command_text and self.current_command_text.strip() == normalized:
            return "running"
        for queued_command, _ in self.command_queue:
            if queued_command.strip() == normalized:
                return "queued"
        return None

    def _confirm_duplicate_command(self, command: str, state: str) -> bool:
        if state == "running":
            message = "This command is already running. Queue another copy anyway?"
        else:
            message = "This command is already waiting in the queue. Queue another copy anyway?"
        result = QMessageBox.question(
            self,
            "Duplicate Command",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def _show_copy_feedback(self):
        if not hasattr(self, "copy_command_button") or not self.copy_command_button:
            return
        if self.copy_button_success_icon:
            self.copy_command_button.setIcon(self.copy_button_success_icon)
        if self.copy_feedback_timer:
            self.copy_feedback_timer.start(1200)

    def _reset_copy_button_icon(self):
        if not hasattr(self, "copy_command_button") or not self.copy_command_button:
            return
        self.copy_command_button.setIcon(self.copy_button_default_icon or self._copy_icon())

    def _sync_copy_button_height(self):
        if not hasattr(self, "copy_command_button") or not self.copy_command_button:
            return
        target_height = max(self.command_preview.height(), self.command_preview.sizeHint().height())
        if target_height > 0:
            self.copy_command_button.setFixedHeight(target_height)

    def _friendly_cli_token(self, token: str) -> Optional[str]:
        token_name = os.path.basename((token or "").strip().strip('"')).lower()
        if token_name in ("doujinshi-dl", "doujinshi-dl.exe", "doujinshi-dl-script.py"):
            return "doujinshi-dl"
        return None

    def _format_display_command(self, command: str) -> str:
        match = re.match(r'^\s*(?:"([^"]+)"|(\S+))(.*)$', command or "")
        if not match:
            return command
        first_token = match.group(1) or match.group(2) or ""
        remainder = match.group(3) or ""
        friendly_token = self._friendly_cli_token(first_token)
        if not friendly_token:
            return command
        return f"{friendly_token}{remainder}"

    def _format_queue_command(self, command: str, limit: Optional[int] = None) -> str:
        display_command = self._format_display_command(command)
        if not limit or limit <= 0 or len(display_command) <= limit:
            return display_command
        return f"{display_command[:limit - 3]}..."

    def _stop_embedded_process(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        self.process = None
        self.current_command_text = None

    def _start_embedded_process(self, command: str, env: dict):
        self._stop_embedded_process()
        if self.terminal_output.toPlainText():
            self._append_terminal_text("\n---\n")
        display_command = self._format_display_command(command)
        self.command_preview.setText(display_command)
        self._append_terminal_text(f"$ {display_command}")
        self._scroll_terminal_to_end()

        self.process = QProcess(self)
        proc_env = self._create_process_environment(env)
        proc_env.insert("PYTHONIOENCODING", "utf-8")
        proc_env.insert("PYTHONUTF8", "1")
        self.process.setProcessEnvironment(proc_env)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.readyReadStandardError.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
        self.process.startCommand(command)
        self.current_command_text = command
        # Ensure visibility if the user toggled to hide the terminal
        if not self.show_terminal:
            self.terminal_frame.setVisible(False)
        self._update_queue_display()

    def _launch_command(self, command: str, env: dict):
        duplicate_state = self._find_duplicate_command_state(command)
        if duplicate_state and not self._confirm_duplicate_command(command, duplicate_state):
            return
        if self.process:
            self.command_queue.append((command, env))
            self.command_preview.setText(f"Queued: {self._format_display_command(command)}")
            self._update_queue_display()
            return
        # Always use embedded QProcess so we can track completion and run queued commands
        self._start_embedded_process(command, env)
        self._update_queue_display()

    def launch_detached_command(self, command: str, env: dict):
        """Run a command without showing an external console window."""
        subprocess.Popen(command, shell=True, env=env, creationflags=self._creation_flags())

    def set_terminal_panel_visible(self, visible: bool):
        self.show_terminal = bool(visible)
        self.settings.setValue("show_terminal_panel", self.show_terminal)
        if self.show_terminal:
            if self.terminal_frame.parent() is None and self.centralWidget():
                self.centralWidget().layout().insertWidget(self.terminal_frame_index, self.terminal_frame)
            self.terminal_frame.setMaximumHeight(self.terminal_frame_size_hint.height())
            self.terminal_frame.setMinimumHeight(self.terminal_frame_size_hint.height())
            self.terminal_frame.setVisible(True)
            self._last_height_without_terminal = self.minimumSizeHint().height()
            if self._last_height_with_terminal:
                self.resize(self.width(), self._last_height_with_terminal)
            else:
                self.resize(self.width(), self.height() + self.terminal_frame_size_hint.height())
        else:
            # Collapse the terminal area so the UI snaps back immediately
            self._last_height_with_terminal = self.height()
            self.terminal_frame.setVisible(False)
            self.terminal_frame.setMaximumHeight(0)
            self.terminal_frame.setMinimumHeight(0)
            self.terminal_frame_size_hint = self.terminal_frame.sizeHint()
            if self.centralWidget():
                self.centralWidget().layout().removeWidget(self.terminal_frame)
                self.terminal_frame.setParent(None)
            if not self._last_height_without_terminal:
                self._last_height_without_terminal = self.minimumSizeHint().height()
            self.resize(self.width(), max(self.minimumSizeHint().height(), self.height() - self.terminal_frame_size_hint.height()))
        if hasattr(self, "custom_title_bar") and hasattr(self.custom_title_bar, "set_terminal_action_checked"):
            self.custom_title_bar.set_terminal_action_checked(self.show_terminal)
        # If the embedded terminal is disabled, fall back to the external console behavior
        if not self.show_terminal and os.name == "nt":
            self.set_external_console_visible(True)
        self._refresh_layout_height(adjust_size=True)

    def set_external_console_visible(self, show_console: bool):
        self.hide_external_console = not bool(show_console)
        self.settings.setValue("hide_external_console", self.hide_external_console)
        if hasattr(self, "custom_title_bar") and hasattr(self.custom_title_bar, "set_console_action_checked"):
            self.custom_title_bar.set_console_action_checked(show_console)

    def _quote_if_needed(self, command):
        return f'"{command}"' if " " in command and not command.startswith("\"") else command

    def resolve_cli_invocation(self):
        if hasattr(self, "_cli_invocation"):
            return self._cli_invocation

        env = os.environ.copy()
        env["PATH"] = self._build_path_with_scripts(env.get("PATH", ""))

        cli_name = "doujinshi-dl"
        resolved_executable = None
        exe_path = os.path.abspath(sys.executable)

        candidate = shutil.which(cli_name, path=env.get("PATH", ""))
        if candidate and os.path.abspath(candidate) != exe_path:
            resolved_executable = candidate

        if not resolved_executable:
            for scripts_dir in self._candidate_script_paths():
                for name in ["doujinshi-dl.exe", "doujinshi-dl-script.py", "doujinshi-dl"]:
                    candidate = os.path.join(scripts_dir, name)
                    if os.path.isfile(candidate):
                        resolved_executable = candidate
                        break
                if resolved_executable:
                    break

        if resolved_executable:
            base_command = self._quote_if_needed(resolved_executable)
        else:
            QMessageBox.critical(
                self,
                "Command Not Found",
                "Could not locate 'doujinshi-dl'. Please install it and ensure it is on PATH.",
            )
            raise RuntimeError("CLI not found")

        self._cli_invocation = (base_command, env)
        return self._cli_invocation

    def _set_bridge_status(self, text: str):
        if hasattr(self, "bridge_status_label") and self.bridge_status_label:
            self.bridge_status_label.setText(text)

    def init_ui(self):
        # Create the custom title bar and add it to the layout
        self.custom_title_bar = CustomTitleBar(self, self.settings)
        self.file_name = None

        self.setWindowTitle("doujinshi-dl-gui")
        self.icon = QIcon(_res("favicon.ico"))
        self.setWindowIcon(self.icon)
        self.setGeometry(100, 100, 510, 300)
        self.center()

        layout = QVBoxLayout()

        layout.addWidget(self.custom_title_bar)

        # Create a container widget (QFrame) for the central widget
        central_container = QFrame()
        central_container.setObjectName("centralContainer")
        central_container.setLayout(layout)  # Set the layout for the container
        self.setCentralWidget(central_container)  # Set the container as the central widget

        self.run_button = QPushButton("Run Commands")
        self.run_button.clicked.connect(self.run_commands)

        self.bridge_status_label = QLabel("")
        self.bridge_status_label.setObjectName("bridge_status_label")
        self.bridge_status_label.setStyleSheet("color: #bbb; font-size: 11px;")

        self.file_button = QPushButton("Select File")
        self.file_button.setObjectName("file_button")
        self.file_button.clicked.connect(self.use_file)
        self.file_button.setEnabled(False)

        self.ids_input = QLineEdit("")
        self.ids_input.setObjectName("ids_input")
        self.ids_input.setPlaceholderText("IDs (e.g., 317039 or #317039)")

        # Chech Boxes
        self.rm_origin_dir_checkbox = QCheckBox("Remove Original Directory")
        self.rm_origin_dir_checkbox.setObjectName("rm_origin_dir_checkbox")
        self.rm_origin_dir_checkbox.setToolTip("Remove downloaded doujinshi dir when generated CBZ or PDF file")
        self.rm_origin_dir_checkbox.setMinimumWidth(220)

        self.save_history_checkbox = QCheckBox("Save Download History")
        self.save_history_checkbox.setObjectName("save_history_checkbox")
        self.save_history_checkbox.setToolTip("Save downloaded doujinshis, whose will be skipped if you re-download them")
        self.save_history_checkbox.setMaximumWidth(214)
        
        self.artist_checkbox = QCheckBox("Artist")
        self.artist_checkbox.setObjectName("artist_checkbox")
        self.artist_checkbox.setToolTip("List doujinshi by artist name")
        self.artist_checkbox.setMinimumWidth(90)
        self.artist_checkbox.stateChanged.connect(self.artist_checkbox_state_changed)

        self.favorites_checkbox = QCheckBox("Favorites")
        self.favorites_checkbox.setObjectName("favorites_checkbox")
        self.favorites_checkbox.setToolTip("List or download your favorites")
        self.favorites_checkbox.setMinimumWidth(104)

        self.download_checkbox = QCheckBox("Download")
        self.download_checkbox.setObjectName("download_checkbox")
        self.download_checkbox.setToolTip("Download doujinshi (from search results)")
        self.download_checkbox.setMinimumWidth(110)

        self.move_to_folder_checkbox = QCheckBox("Move to Folder")
        self.move_to_folder_checkbox.setObjectName("move_to_folder_checkbox")
        self.move_to_folder_checkbox.setToolTip("When generating CBZ or PDF file removes files in doujinshi directory then move new archive to a folder with same name")
        self.move_to_folder_checkbox.setMinimumWidth(130)

        self.cbz_checkbox = QCheckBox("CBZ")
        self.cbz_checkbox.setObjectName("cbz_checkbox")
        self.cbz_checkbox.setToolTip("Generate Comic Book CBZ File")
        self.cbz_checkbox.setMinimumWidth(70)

        self.pdf_checkbox = QCheckBox("PDF")
        self.pdf_checkbox.setObjectName("pdf_checkbox")
        self.pdf_checkbox.setToolTip("Generate PDF file")
        self.pdf_checkbox.setMaximumWidth(90)

        self.dry_run_checkbox = QCheckBox("Dry Run")
        self.dry_run_checkbox.setObjectName("dry_run_checkbox")
        self.dry_run_checkbox.setToolTip("Dry run, skips file download for reference")
        self.dry_run_checkbox.setMinimumWidth(130)

        self.show_checkbox = QCheckBox("Show")
        self.show_checkbox.setObjectName("show_checkbox")
        self.show_checkbox.setToolTip("Only shows the doujinshi information")
        self.show_checkbox.setMaximumWidth(90)

        self.no_html_checkbox = QCheckBox("No HTML")
        self.no_html_checkbox.setObjectName("no_html_checkbox")
        self.no_html_checkbox.setToolTip("Don't generate HTML after downloading")
        self.no_html_checkbox.setMaximumWidth(104)

        self.gen_main_checkbox = QCheckBox("Gen. Main")
        self.gen_main_checkbox.setObjectName("gen_main_checkbox")
        self.gen_main_checkbox.setToolTip("Generate a main viewer contain all the doujin in the folder") #TODO Might needs to reword this
        self.gen_main_checkbox.setMaximumWidth(110)

        self.meta_checkbox = QCheckBox("META")
        self.meta_checkbox.setObjectName("meta_checkbox")
        self.meta_checkbox.setToolTip("Generate a metadata file in doujinshi format")
        self.meta_checkbox.setMaximumWidth(70)

        self.regen_cbz_checkbox = QCheckBox("Regen CBZ")
        self.regen_cbz_checkbox.setObjectName("regen_cbz_checkbox")
        self.regen_cbz_checkbox.setToolTip("Regenerate the cbz or pdf file if exists")
        self.regen_cbz_checkbox.setMaximumWidth(135)

        self.search_checkbox = QCheckBox("Search")
        self.search_checkbox.setObjectName("search_checkbox")
        self.search_checkbox.setToolTip("Search doujinshi by keyword")
        self.search_checkbox.setMinimumWidth(90)
        self.search_checkbox.stateChanged.connect(self.search_checkbox_state_changed)

        self.file_checkbox = QCheckBox("File: ")
        self.file_checkbox.setObjectName("file_checkbox")
        self.file_checkbox.setToolTip("Read gallery IDs from file.")
        self.file_checkbox.stateChanged.connect(self.file_checkbox_state_changed)
        self.file_checkbox.setMaximumWidth(90)

        self.exit_on_fail_checkbox = QCheckBox("Exit on Fail")
        self.exit_on_fail_checkbox.setObjectName("exit_on_fail_checkbox")
        self.exit_on_fail_checkbox.setToolTip("Exit on fail to prevent generating incomplete files.")
        self.exit_on_fail_checkbox.setMinimumWidth(130)

        # QLabels
        self.page_input_label = QLabel("Page \nRange:")
        self.page_input_label.setFixedWidth(90)

        self.delay_input_label = QLabel("Delay \n(seconds):")
        self.delay_input_label.setFixedWidth(104)

        self.thereads_input_label = QLabel("\nThreads:")
        self.thereads_input_label.setFixedWidth(110)

        self.retry_input_label = QLabel("\nRetry:")
        self.retry_input_label.setFixedWidth(70)

        # QLineEdits
        self.page_input = QLineEdit("")
        self.page_input.setObjectName("page_input")
        self.page_input.setToolTip("(e.g. 1-6, 0 = all)")
        self.page_input.setFixedWidth(90)

        self.delay_input = QLineEdit("1")
        self.delay_input.setObjectName("delay_input")
        self.delay_input.setToolTip("Delay between downloading each doujinshi\n to avoid being timed out.")
        self.delay_input.setFixedWidth(104)

        self.threads_input = QLineEdit("")
        self.threads_input.setObjectName("threads_input")
        self.threads_input.setToolTip("Thread count for downloading doujinshi")
        self.threads_input.setFixedWidth(110)

        self.retry_input = QLineEdit("")
        self.retry_input.setObjectName("retry_input")
        self.retry_input.setToolTip("Retry times when downloading failed")
        self.retry_input.setFixedWidth(70)

        self.format_input = QLineEdit('')
        self.format_input.setObjectName("format_input")
        tooltip_text = (
            "%i: Doujinshi ID\n"
            "%f: Doujinshi favorite count\n"
            "%t: Doujinshi name\n"
            "%s: Doujinshi subtitle (translated name)\n"
            "%a: Doujinshi author(s)\n"
            "%g: Doujinshi group(s)\n"
            "%p: Doujinshi pretty name\n"
            "%ag: Doujinshi author(s) or group(s)"
        )
        self.format_input.setToolTip(tooltip_text)
        self.format_input.setPlaceholderText("[%ag] - %p (%i)")

        self.output_input = QLineEdit("")
        self.output_input.setObjectName("output_input")

        # QComboBox
        self.sorting_combo_box = QComboBox()
        self.sorting_combo_box.addItems(['-','Recent','Popular','Popular Today','Popular Week'])
        self.sorting_combo_box.setObjectName("sorting_combo_box")
        self.sorting_combo_box.setToolTip("Sorting order of doujinshi (recent / popular /popular-[today|week])")
        self.sorting_combo_box.setMinimumWidth(130)

        # Grid layout — columns anchored to fixed input widths
        controls_grid = QGridLayout()
        controls_grid.setHorizontalSpacing(6)
        controls_grid.setVerticalSpacing(6)
        controls_grid.setColumnMinimumWidth(0, 90)
        controls_grid.setColumnMinimumWidth(1, 104)
        controls_grid.setColumnMinimumWidth(2, 110)
        controls_grid.setColumnMinimumWidth(3, 70)
        controls_grid.setColumnStretch(4, 1)

        # Row 0: Search | IDs input (cols 1-3) | Sorting
        controls_grid.addWidget(self.search_checkbox, 0, 0)
        controls_grid.addWidget(self.ids_input, 0, 1, 1, 3)
        controls_grid.addWidget(self.sorting_combo_box, 0, 4)

        # Row 1: Column labels | Exit on Fail
        controls_grid.addWidget(self.page_input_label, 1, 0)
        controls_grid.addWidget(self.delay_input_label, 1, 1)
        controls_grid.addWidget(self.thereads_input_label, 1, 2)
        controls_grid.addWidget(self.retry_input_label, 1, 3)
        controls_grid.addWidget(self.exit_on_fail_checkbox, 1, 4)

        # Row 2: Numeric inputs | Dry Run
        controls_grid.addWidget(self.page_input, 2, 0)
        controls_grid.addWidget(self.delay_input, 2, 1)
        controls_grid.addWidget(self.threads_input, 2, 2)
        controls_grid.addWidget(self.retry_input, 2, 3)
        controls_grid.addWidget(self.dry_run_checkbox, 2, 4)

        # Row 3: Artist | Favorites | Download | CBZ | Move to Folder
        controls_grid.addWidget(self.artist_checkbox, 3, 0)
        controls_grid.addWidget(self.favorites_checkbox, 3, 1)
        controls_grid.addWidget(self.download_checkbox, 3, 2)
        controls_grid.addWidget(self.cbz_checkbox, 3, 3)
        controls_grid.addWidget(self.move_to_folder_checkbox, 3, 4)

        # Row 4: Show | No HTML | Gen. Main | META | Regen CBZ
        controls_grid.addWidget(self.show_checkbox, 4, 0)
        controls_grid.addWidget(self.no_html_checkbox, 4, 1)
        controls_grid.addWidget(self.gen_main_checkbox, 4, 2)
        controls_grid.addWidget(self.meta_checkbox, 4, 3)
        controls_grid.addWidget(self.regen_cbz_checkbox, 4, 4)

        # Row 5: PDF | Remove Original Dir (span 2) | Save History (span 2)
        controls_grid.addWidget(self.pdf_checkbox, 5, 0)
        controls_grid.addWidget(self.rm_origin_dir_checkbox, 5, 1, 1, 2)
        controls_grid.addWidget(self.save_history_checkbox, 5, 3, 1, 2)

        # Row 6: File checkbox | Select File button (span 4)
        controls_grid.addWidget(self.file_checkbox, 6, 0)
        controls_grid.addWidget(self.file_button, 6, 1, 1, 4)

        layout.addLayout(controls_grid)

        # Add inputs to the QVBoxLayout
        layout.addWidget(QLabel("Format:"))
        layout.addWidget(self.format_input)
        layout.addWidget(QLabel("Output Folder:"))
        layout.addWidget(self.output_input)
        layout.addWidget(self.run_button)
        layout.addWidget(self.bridge_status_label)
        
        # Embedded terminal panel
        self.command_preview = QLineEdit("")
        self.command_preview.setObjectName("command_preview")
        self.command_preview.setReadOnly(True)
        self.command_preview.setPlaceholderText("Assembled command will appear here")
        self.command_preview.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        self.copy_command_button = QPushButton()
        self.copy_command_button.setObjectName("copy_command_button")
        self.copy_command_button.setToolTip("Copy command to clipboard")
        self.copy_button_default_icon = self._copy_icon()
        self.copy_button_success_icon = self._copy_success_icon()
        self.copy_command_button.setIcon(self.copy_button_default_icon)
        self.copy_command_button.setFixedWidth(32)
        self.copy_command_button.clicked.connect(self.copy_command_to_clipboard)
        self.copy_feedback_timer = QTimer(self)
        self.copy_feedback_timer.setSingleShot(True)
        self.copy_feedback_timer.timeout.connect(self._reset_copy_button_icon)

        self.terminal_output = QPlainTextEdit()
        self.terminal_output.setObjectName("terminal_output")
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.terminal_output.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.terminal_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.terminal_output.setMinimumHeight(190)
        self.terminal_output.setMaximumBlockCount(2000)
        self.terminal_output.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        self.terminal_frame = QFrame()
        terminal_layout = QVBoxLayout()
        terminal_layout.setContentsMargins(0, 0, 0, 0)
        terminal_layout.addWidget(QLabel("Command Preview:"))
        command_preview_row = QHBoxLayout()
        command_preview_row.setContentsMargins(0, 0, 0, 0)
        command_preview_row.setSpacing(6)
        command_preview_row.addWidget(self.command_preview)
        command_preview_row.addWidget(self.copy_command_button)
        terminal_layout.addLayout(command_preview_row)
        self.queue_display = QPlainTextEdit()
        self.queue_display.setObjectName("queue_display")
        self.queue_display.setReadOnly(True)
        self.queue_display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.queue_display.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.queue_display.setFixedHeight(90)
        self.queue_display.setPlainText("Queue: empty")
        self.queue_display.setStyleSheet(
            """
QPlainTextEdit#queue_display {
    font-family: Consolas, 'Courier New', monospace;
    font-size: 11px;
    color: #ddd;
    background-color: #1e1f29;
    border: 1px solid #444;
    border-radius: 4px;
}
QPlainTextEdit#queue_display QScrollBar:vertical,
QPlainTextEdit#queue_display QScrollBar:horizontal {
    background: #1e1f29;
    border: 1px solid #444;
    border-radius: 3px;
    margin: 0px;
}
QPlainTextEdit#queue_display QScrollBar::handle:vertical,
QPlainTextEdit#queue_display QScrollBar::handle:horizontal {
    background: #ff2f70;
    border-radius: 3px;
}
QPlainTextEdit#queue_display QScrollBar::add-line,
QPlainTextEdit#queue_display QScrollBar::sub-line {
    height: 0px;
    width: 0px;
}
            """
        )
        self.queue_display.setVisible(False)
        terminal_layout.addWidget(self.queue_display)
        terminal_layout.addWidget(QLabel("Terminal Output:"))
        terminal_layout.addWidget(self.terminal_output)
        self.terminal_frame.setLayout(terminal_layout)
        self.terminal_frame_size_hint = self.terminal_frame.sizeHint()
        if not self.show_terminal:
            self.terminal_frame.setMaximumHeight(0)
            self.terminal_frame.setMinimumHeight(0)
            self.terminal_frame.setVisible(False)
        else:
            self.terminal_frame.setVisible(True)

        self.terminal_frame_index = layout.count()
        layout.addWidget(self.terminal_frame)

        self.load_ui_states()

        # Shrink the window if the terminal is hidden so there is no empty space
        if not self.show_terminal:
            self._refresh_layout_height()

        self._update_queue_display()
        self._sync_copy_button_height()

        try:
            with open(_res("styles.qss"), encoding="utf-8") as f:
                _qss = f.read()
            _res_uri = os.path.dirname(_res("x")).replace("\\", "/")
            self.setStyleSheet(_qss.replace(":/resources", _res_uri))
        except OSError:
            pass

    def center(self):
        if (not self.settings.value("remember_main_windows_position", False, type=bool)):
            qr = self.frameGeometry()
            cp = self.screen().availableGeometry().center()

            qr.moveCenter(cp)
            self.move(qr.topLeft())
        else:
            pos = self.settings.value("main_windows_position")
            self.move(pos)
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_copy_button_height()


    def closeEvent(self, event):
        self.save_ui_states()
        self._stop_embedded_process()
        self.stop_extension_bridge()
        event.accept()

    def load_ui_states(self):
        # Load checkbox states
        checkboxes = [
            self.rm_origin_dir_checkbox, self.save_history_checkbox, self.favorites_checkbox,
            self.download_checkbox, self.cbz_checkbox, self.move_to_folder_checkbox,
            self.pdf_checkbox, self.dry_run_checkbox, self.show_checkbox, self.no_html_checkbox,
            self.gen_main_checkbox, self.meta_checkbox, self.regen_cbz_checkbox, self.search_checkbox,
            self.file_checkbox, self.artist_checkbox, self.exit_on_fail_checkbox
        ]
        for checkbox in checkboxes:
            checkbox.setChecked(self.settings.value(checkbox.objectName(), False, type=bool))

        # Load QLineEdit states
        line_edits = [
            (self.ids_input, ""),
            (self.page_input, ""),
            (self.delay_input, "1"),
            (self.format_input, ""),
            (self.output_input, ""),
            (self.threads_input, ""),
            (self.retry_input, "")
        ]
        for line_edit, default_value in line_edits:
            line_edit.setText(self.settings.value(line_edit.objectName(), default_value, type=str))



        combo_boxes = [
            self.sorting_combo_box
        ]
        for cbBox in combo_boxes:
            cbBox.setCurrentIndex(self.settings.value(cbBox.objectName(), cbBox.setCurrentIndex(0), type=int))


    def save_ui_states(self):
        # Save checkbox states
        checkboxes = [
            self.rm_origin_dir_checkbox, self.save_history_checkbox, self.favorites_checkbox,
            self.download_checkbox, self.cbz_checkbox, self.move_to_folder_checkbox,
            self.pdf_checkbox, self.dry_run_checkbox, self.show_checkbox, self.no_html_checkbox,
            self.gen_main_checkbox, self.meta_checkbox, self.regen_cbz_checkbox, self.search_checkbox,
            self.file_checkbox, self.artist_checkbox, self.exit_on_fail_checkbox
        ]
        for checkbox in checkboxes:
            self.settings.setValue(checkbox.objectName(), checkbox.isChecked())

        # Save QLineEdit states
        line_edits = [
            self.ids_input, 
            self.page_input, 
            self.delay_input, 
            self.format_input, 
            self.output_input, 
            self.threads_input,
            self.retry_input
        ]
        for line_edit in line_edits:
            self.settings.setValue(line_edit.objectName(), line_edit.text())

        # Save QComboBox states
        combo_boxes = [
            self.sorting_combo_box
        ]
        for cbBox in combo_boxes:
            self.settings.setValue(cbBox.objectName(), cbBox.currentIndex())

        # Window Position values
        self.settings.setValue("main_windows_position", self.pos())

    def run_commands(self):
        # Assemble the CLI command based on user inputs
        base_command, env = self.resolve_cli_invocation()
        commands = base_command
        selected_sorting_option = self.sorting_combo_box.currentText()

        if self.ids_input.text() and not self.search_checkbox.isChecked() and not self.artist_checkbox.isChecked():
            cleaned_output_text = self.ids_input.text().replace("#","")
            commands += f" --id {cleaned_output_text}"
        if self.rm_origin_dir_checkbox.isChecked():
            commands += (" --rm-origin-dir")
        if self.save_history_checkbox.isChecked():
            commands += " --save-download-history"
        if self.favorites_checkbox.isChecked():
            commands += " --favorites"
        if self.pdf_checkbox.isChecked():
            commands += " --pdf"
        if self.show_checkbox.isChecked():
            commands += " --show"
        if self.no_html_checkbox.isChecked():
            commands += " --no-html"
        if self.dry_run_checkbox.isChecked():
            commands += " --dry-run"   
        if self.gen_main_checkbox.isChecked():
            commands += " --gen-main"
        if self.meta_checkbox.isChecked():
            commands += " --meta"     
        if self.regen_cbz_checkbox.isChecked():
            commands += " --regenerate" 
        if self.file_checkbox.isChecked() and self.file_name is not None:
            commands += f" --file=\"{self.file_name}\"" 
        if self.search_checkbox.isChecked():
            commands += f" --search \"{self.ids_input.text()}\""    
        if self.artist_checkbox.isChecked():
            cleaned_output_text = self.ids_input.text().replace(" ", "-")
            commands += f" --artist=\"{cleaned_output_text}\""
        if self.page_input.text():
            if self.page_input.text() == "0":
                commands += f" --page-all"
            else:
                commands += f" --page={self.page_input.text()}"
        if self.download_checkbox.isChecked():
            commands += " --download"
        if self.delay_input.text():
            commands += f" --delay {self.delay_input.text()}"
        if self.threads_input.text():
            commands += f" --threads={self.threads_input.text()}"
        if self.cbz_checkbox.isChecked():
            commands += " --cbz"
        if self.move_to_folder_checkbox.isChecked():
            commands += " --move-to-folder"
        if self.format_input.text():
            commands += f' --format "{self.format_input.text()}"'
        if self.retry_input.text():
            commands += f" --retry={self.retry_input.text()}"
        if self.exit_on_fail_checkbox.isChecked():
            commands += " --exit-on-fail"

        if not self.output_input.text() and not self.settings.value("default_doujins_folder"):
            QMessageBox.critical(self, "Error", "Please provide an output path or set the default doujins folder.")
            return
        else:
            commands += f' --output "{self.output_command()}"'

        if self.sorting_combo_box.currentText() != "-":
            if selected_sorting_option == 'Recent':
                commands += " --sorting=recent"
            elif selected_sorting_option == 'Popular':
                commands += " --sorting=popular"
            elif selected_sorting_option == 'Popular Today':
                commands += " --sorting=popular-today"
            elif selected_sorting_option == 'Popular Week':
                commands += " --sorting=popular-week"

        print(commands)
        self._launch_command(commands, env)

    def run_specific_command(self, command):
        # Open the terminal externally and run the commands
        base_command, env = self.resolve_cli_invocation()
        commands = (f"{base_command} {command}")
        print(commands)
        self._launch_command(commands, env)

    def output_command(self):
        cleaned_output_text = re.sub(r'[\\/*?:"<>|]', "-", self.output_input.text().strip())
        default_folder = self.settings.value("default_doujins_folder")
        output_path = default_folder if default_folder and not self.output_input.text() else self.output_input.text()
        
        if default_folder and self.output_input.text():
            output_path = f'{default_folder}/{cleaned_output_text}/'

        return output_path

    def start_extension_bridge(self):
        if self.extension_bridge_server:
            return
        if not self.extension_bridge_enabled:
            self._set_bridge_status("Extension bridge disabled")
            return
        try:
            self.extension_bridge_server = ExtensionBridgeServer(self.extension_bridge_queue)
            self.extension_bridge_server.start()
            self.extension_bridge_timer = QTimer(self)
            self.extension_bridge_timer.setInterval(200)
            self.extension_bridge_timer.timeout.connect(self.process_extension_bridge_queue)
            self.extension_bridge_timer.start()
            status = f"Extension bridge ready: http://{BRIDGE_HOST}:{BRIDGE_PORT}/gallery-id"
            print(status)
            self._set_bridge_status(status)
            self.extension_bridge_status = "ready"
        except OSError as exc:
            print(f"Failed to start extension bridge server: {exc}")
            self.extension_bridge_server = None
            self.extension_bridge_timer = None
            self.extension_bridge_status = f"{exc}"
            self._set_bridge_status(f"Extension bridge error: {exc}")
            QMessageBox.critical(self, "Extension Bridge Error", f"Failed to start bridge server:\n{exc}")

    def stop_extension_bridge(self):
        if self.extension_bridge_timer:
            self.extension_bridge_timer.stop()
            self.extension_bridge_timer = None
        if self.extension_bridge_server:
            self.extension_bridge_server.shutdown()
            self.extension_bridge_server.join(timeout=1)
            self.extension_bridge_server = None
        while not self.extension_bridge_queue.empty():
            try:
                self.extension_bridge_queue.get_nowait()
            except queue.Empty:
                break
        self.extension_bridge_status = "disabled"
        self._set_bridge_status("Extension bridge disabled")

    def process_extension_bridge_queue(self):
        while not self.extension_bridge_queue.empty():
            event = self.extension_bridge_queue.get()

            # Backward compatibility for legacy tuple payloads
            if not isinstance(event, dict):
                try:
                    gallery_id, clear_before = event
                    self.insert_text_into_ids(gallery_id, clear_before)
                except Exception:
                    pass
                continue

            event_type = event.get("type")
            if event_type == "gallery-id":
                gallery_id = event.get("galleryId", "")
                clear_before = bool(event.get("clearBeforeInsert", False))
                self.insert_text_into_ids(gallery_id, clear_before)
            elif event_type == "run-command":
                if event.get("clearIds"):
                    self.ids_input.clear()
                self.run_commands()

    def set_extension_bridge_enabled(self, enabled: bool):
        if enabled == self.extension_bridge_enabled:
            return
        self.extension_bridge_enabled = enabled
        self.settings.setValue("extension_bridge_enabled", enabled)
        if enabled:
            self.start_extension_bridge()
        else:
            self.stop_extension_bridge()
        
    def _preset_names(self):
        """Return (raw_group_name, display_name) pairs sorted alphabetically."""
        preset_groups = [g for g in self.settings.childGroups() if g.startswith("Preset_")]
        sorted_groups = sorted(preset_groups, key=lambda x: x.lower())
        return [(g, g.replace("Preset_", "", 1).replace("-", " ")) for g in sorted_groups]

    def load_preset(self, preset_name):
        cleaned_preset_name = preset_name.replace(" ", "-")

        # Load the presents group in the settings.ini
        self.settings.beginGroup(f"Preset_{cleaned_preset_name}")

        self.load_ui_states()

        self.settings.endGroup()

    def manage_presets(self):
        manage_dialog = ManagePresetsDialog(self)
        manage_dialog.exec()

    def set_default_directory(self):
        default_dir = QFileDialog.getExistingDirectory(self, "Select Default Directory")
        if default_dir:
            self.settings.setValue("default_doujins_folder", default_dir)

    def open_default_directory(self):
        default_folder = self.settings.value("default_doujins_folder")
        
        if not default_folder:
            QMessageBox.critical(self, "Error", "Default doujins folder is not set.")
            return
        
        if not os.path.exists(default_folder):
            QMessageBox.critical(self, "Error", "Default doujins folder does not exist.")
            return

        try:
            if sys.platform == "win32":
                os.startfile(default_folder)  # For Windows
            elif sys.platform == "Darwin": 
                subprocess.Popen(["open", default_folder]) # macOS
            else:  
                subprocess.Popen(["xdg-open", default_folder]) # Linux
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open directory: {str(e)}")
    
    def set_cookie(self):
        if self.cookieHandler is None or not self.cookieHandler.isVisible():
            self.cookieHandler = CookieHandler(self, self.settings)  # Pass MainWindow instance to CookieHandler
        self.cookieHandler.show()

    def set_language(self):
        items = ['english','chinese','japanese','translated',]

        # Combo Box to choose between the language constants
        language, ok = QInputDialog.getItem(self, 'Set Language', 'Enter Language:', items)

        if ok:
            command = (f" --language={language}")
            self.run_specific_command(command)
        else:
            return
        
    def search_checkbox_state_changed(self, state):
        self.ids_input.clear()

        if state == 2:
            if self.artist_checkbox.isChecked():
                self.artist_checkbox.setChecked(False)

            self.ids_input.setPlaceholderText("Search:")
        else:
            self.ids_input.setPlaceholderText("IDs (e.g., 302294 or #317039)")

    def artist_checkbox_state_changed(self, state):
        self.ids_input.clear()

        if state == 2:
            if self.search_checkbox.isChecked():
                self.search_checkbox.setChecked(False)
                
            self.ids_input.setPlaceholderText("Artist:")
        else:
            self.ids_input.setPlaceholderText("IDs (e.g., 302294 or #317039)")

    def file_checkbox_state_changed(self, state):
        if state == 2:
            self.file_button.setEnabled(True)
        else:
            self.file_button.setEnabled(False)

    def use_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Select .txt File", "", "Text Files (*.txt);;All Files (*)")

        if file_name:
            self.file_button.setText(f"\"{file_name}\"")
            self.file_name = file_name
        else:
            self.file_button.setText("Select File")
            self.file_name = None

    def clean_language(self):
        # TODO this is currently none functional in the nhentai package
        confirm = QMessageBox.question(self, "Confirmation", "Are you sure you want to do this?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                       QMessageBox.StandardButton.No)
        
        if confirm == QMessageBox.StandardButton.Yes:
            self.run_specific_command(" --clean-language")
        else:
            print("User canceled.")
            return
        
    def clean_download_history(self):
        confirm = QMessageBox.question(self, "Confirmation", 
                                       "Are you sure you want to do this?\nThis cannot be undone: Delete All Download History",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                       QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self.run_specific_command(" --clean-download-history")
        else:
            print("User canceled.")
            return
    
    def resource_path(self, relative_path):
        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")

        return os.path.join(base_path, relative_path)
    
    def insert_text_into_ids(self, text: str, clear_first: bool = False):
        text = (text or "").strip()
        if not text:
            return

        if clear_first:
            self.ids_input.clear()

        if self.search_checkbox.isChecked():
            current_text = self.ids_input.text().strip()
            if current_text:
                self.ids_input.setText(f"{current_text} {text}")
            else:
                self.ids_input.setText(text)
            return

        match = re.fullmatch(r"#?\d{1,7}", text)
        if not match:
            print("Provided text does not match the required format. Valid format: (`#12345` or `12345` with 1-7 digits)")
            return

        normalized_id = text.lstrip("#")
        current_text = self.ids_input.text().strip()
        existing_ids = {id.lstrip("#") for id in current_text.split() if re.fullmatch(r"#?\d{1,7}", id)}

        if normalized_id in existing_ids:
            print(f"Duplicate ID detected; not adding. ID: #{normalized_id}")
            return

        valid_text = text if text.startswith("#") else f"#{normalized_id}"
        if current_text:
            self.ids_input.setText(f"{current_text} {valid_text}")
        else:
            self.ids_input.setText(valid_text)

    def paste_and_append_text(self, event: None):
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        clear_before = event and event.button() == Qt.MouseButton.RightButton
        self.insert_text_into_ids(text, bool(clear_before))
        

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("doujinshi-dl-gui")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

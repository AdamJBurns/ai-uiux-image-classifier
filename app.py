"""AI UI/UX Image Classifier - desktop app (PySide6 / Qt).

Point it at a folder of UI/UX design screenshots, let an OpenAI vision model
read each one, preview the proposed names, then rename in one click. Includes
an Undo for the last batch.

Styled to Vercel's Geist (Light) design system.

Run:  python app.py
"""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI
from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import classifier as core

APP_TITLE = "AI UI/UX Image Classifier"
MAX_WORKERS = 4

# --- Geist (Light) tokens used in code (QSS holds the rest) ---------------
C_PRIMARY = "#171717"   # gray-1000 primary text
C_SECONDARY = "#4d4d4d"  # gray-900 secondary text
C_MUTED = "#8f8f8f"     # gray-700 tertiary text
C_SUCCESS = "#28a948"   # green-700
C_ERROR = "#ea001d"     # red-800
API_KEYS_URL = "https://platform.openai.com/api-keys"


def _pick_family(candidates: list[str], default: str) -> str:
    available = set(QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return default


def _build_stylesheet(sans: str) -> str:
    return f"""
    * {{ font-family: "{sans}"; }}
    QWidget#root {{ background: #ffffff; }}

    QLabel#title    {{ color: #171717; font-size: 24px; font-weight: 600; }}
    QLabel#subtitle {{ color: #4d4d4d; font-size: 14px; }}
    QLabel#fieldLabel {{ color: #4d4d4d; font-size: 14px; }}
    QLabel#hint     {{ color: #8f8f8f; font-size: 13px; }}
    QLabel#status   {{ color: #8f8f8f; font-size: 13px; }}
    QFrame#divider  {{ background: #eaeaea; }}

    QLineEdit {{
        background: #ffffff; color: #171717;
        border: 1px solid #eaeaea; border-radius: 6px;
        padding: 0 12px; min-height: 40px; font-size: 14px;
        selection-background-color: #cae7ff; selection-color: #171717;
    }}
    QLineEdit:focus {{ border: 1px solid #006bff; }}
    QLineEdit:disabled {{ background: #fafafa; color: #8f8f8f; }}

    QComboBox {{
        background: #ffffff; color: #171717;
        border: 1px solid #eaeaea; border-radius: 6px;
        padding: 0 12px; min-height: 40px; font-size: 14px;
    }}
    QComboBox:focus {{ border: 1px solid #006bff; }}
    QComboBox:hover {{ border: 1px solid #e0e0e0; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow {{
        image: none; width: 0; height: 0;
        border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-top: 5px solid #8f8f8f; margin-right: 12px;
    }}
    QComboBox QAbstractItemView {{
        background: #ffffff; color: #171717;
        border: 1px solid #eaeaea; border-radius: 8px; padding: 4px;
        outline: 0; selection-background-color: #f2f2f2; selection-color: #171717;
    }}

    QPushButton#primary {{
        background: #171717; color: #ffffff; border: none; border-radius: 6px;
        padding: 0 16px; min-height: 40px; font-size: 14px; font-weight: 500;
    }}
    QPushButton#primary:hover   {{ background: #383838; }}
    QPushButton#primary:pressed {{ background: #000000; }}
    QPushButton#primary:disabled {{ background: #f2f2f2; color: #8f8f8f; }}

    QPushButton#secondary {{
        background: #ffffff; color: #171717;
        border: 1px solid #eaeaea; border-radius: 6px;
        padding: 0 14px; min-height: 40px; font-size: 14px; font-weight: 500;
    }}
    QPushButton#secondary:hover   {{ background: #f2f2f2; border-color: #e0e0e0; }}
    QPushButton#secondary:pressed {{ background: #ebebeb; }}
    QPushButton#secondary:disabled {{ color: #c9c9c9; border-color: #f2f2f2; }}

    QPushButton#ghost {{
        background: transparent; color: #4d4d4d; border: none; border-radius: 6px;
        padding: 0 12px; min-height: 40px; font-size: 14px; font-weight: 500;
    }}
    QPushButton#ghost:hover {{ background: #f2f2f2; color: #171717; }}
    QPushButton#ghost:disabled {{ color: #c9c9c9; }}

    QCheckBox {{ color: #4d4d4d; font-size: 13px; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border: 1px solid #c9c9c9;
        border-radius: 4px; background: #ffffff;
    }}
    QCheckBox::indicator:hover {{ border-color: #8f8f8f; }}
    QCheckBox::indicator:checked {{ background: #171717; border-color: #171717; }}

    QTableWidget#table {{
        background: #ffffff; color: #171717; font-size: 14px;
        border: 1px solid #eaeaea; border-radius: 12px;
        gridline-color: transparent; outline: 0;
    }}
    QTableWidget#table::item {{ padding: 4px 10px; border: none; }}
    QTableWidget#table::item:selected {{ background: #f2f2f2; color: #171717; }}
    QHeaderView::section {{
        background: #fafafa; color: #8f8f8f; font-size: 12px; font-weight: 600;
        border: none; border-bottom: 1px solid #eaeaea; padding: 10px;
    }}
    QHeaderView::section:first {{ border-top-left-radius: 12px; }}
    QHeaderView::section:last  {{ border-top-right-radius: 12px; }}
    QTableCornerButton::section {{ background: #fafafa; border: none; }}

    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: #e0e0e0; border-radius: 4px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: #c9c9c9; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QProgressBar {{ background: #f2f2f2; border: none; border-radius: 3px; max-height: 6px; }}
    QProgressBar::chunk {{ background: #171717; border-radius: 3px; }}

    QToolTip {{
        background: #171717; color: #ffffff; border: none;
        padding: 6px 8px; border-radius: 6px; font-size: 12px;
    }}
    """


class AnalyzeWorker(QObject):
    """Runs vision analysis off the UI thread and reports via signals."""

    progress = Signal(int)
    row_done = Signal(object)
    row_error = Signal(object, str)
    row_skipped = Signal(object)
    finished = Signal()
    fatal = Signal(str)

    def __init__(self, api_key: str, model: str, plans: list, cancel_event: threading.Event):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.plans = plans
        self.cancel_event = cancel_event

    def run(self) -> None:
        try:
            client = OpenAI(api_key=self.api_key)
        except Exception as exc:  # noqa: BLE001
            self.fatal.emit(f"Could not initialize OpenAI client: {exc}")
            self.finished.emit()
            return

        def process(plan):
            if self.cancel_event.is_set():
                return plan, None, "cancelled"
            try:
                return plan, core.suggest_title(client, plan.source, model=self.model), None
            except Exception as exc:  # noqa: BLE001
                return plan, None, str(exc)

        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for plan, title, error in pool.map(process, self.plans):
                done += 1
                if error == "cancelled":
                    plan.status = "skipped"
                    self.row_skipped.emit(plan)
                elif error:
                    plan.status = "error"
                    plan.error = error
                    self.row_error.emit(plan, error)
                else:
                    plan.suggested_title = title
                    plan.status = "ready"
                    self.row_done.emit(plan)
                self.progress.emit(done)
        self.finished.emit()


class App(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle(APP_TITLE)
        self.resize(1040, 760)
        self.setMinimumSize(860, 600)

        self.config_data = core.load_config()
        self.plans: list[core.RenamePlan] = []
        self.last_undo: core.UndoRecord | None = None
        self.cancel_event = threading.Event()
        self.thread: QThread | None = None
        self.worker: AnalyzeWorker | None = None
        self._loading = False

        self._build_ui()
        self._load_config_into_fields()

    # --------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(0)

        title = QLabel(APP_TITLE)
        title.setObjectName("title")
        root.addWidget(title)

        subtitle = QLabel("Auto-rename UI/UX design examples based on what each screen shows.")
        subtitle.setObjectName("subtitle")
        root.addSpacing(4)
        root.addWidget(subtitle)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        root.addSpacing(20)
        root.addWidget(divider)
        root.addSpacing(20)

        # --- form grid
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)
        form.setColumnStretch(1, 1)

        form.addWidget(self._field_label("Folder"), 0, 0)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Choose a folder of UI/UX screenshots…")
        form.addWidget(self.folder_edit, 0, 1)
        browse_btn = QPushButton("Browse")
        browse_btn.setObjectName("secondary")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._browse)
        form.addWidget(browse_btn, 0, 2, 1, 2)

        form.addWidget(self._field_label("API Key"), 1, 0)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-…")
        form.addWidget(self.key_edit, 1, 1)
        self.show_key = QCheckBox("Show")
        self.show_key.toggled.connect(self._toggle_key)
        form.addWidget(self.show_key, 1, 2)
        key_btn = QPushButton("Get Key")
        key_btn.setObjectName("secondary")
        key_btn.setCursor(Qt.PointingHandCursor)
        key_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_KEYS_URL)))
        form.addWidget(key_btn, 1, 3)

        form.addWidget(self._field_label("Model"), 2, 0)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(12)
        self.model_box = QComboBox()
        self.model_box.addItems(["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"])
        self.model_box.setFixedWidth(180)
        model_row.addWidget(self.model_box)
        hint = QLabel("gpt-4o-mini is fast and inexpensive; gpt-4o is the most accurate.")
        hint.setObjectName("hint")
        model_row.addWidget(hint)
        model_row.addStretch(1)
        form.addLayout(model_row, 2, 1, 1, 3)

        root.addLayout(form)
        root.addSpacing(20)

        # --- actions
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.analyze_btn = QPushButton("Analyze Images")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.clicked.connect(self._start_analyze)
        actions.addWidget(self.analyze_btn)

        self.apply_btn = QPushButton("Apply Rename")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        actions.addWidget(self.apply_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_btn)

        actions.addStretch(1)
        self.undo_btn = QPushButton("Undo Last Rename")
        self.undo_btn.setObjectName("ghost")
        self.undo_btn.setCursor(Qt.PointingHandCursor)
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)
        actions.addWidget(self.undo_btn)
        root.addLayout(actions)
        root.addSpacing(16)

        # --- table card (with subtle elevation)
        card = QFrame()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("table")
        self.table.setHorizontalHeaderLabels(["STATUS", "ORIGINAL NAME", "NEW NAME"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 150)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setHighlightSections(False)
        self.table.itemChanged.connect(self._on_item_changed)
        card_layout.addWidget(self.table)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setXOffset(0)
        shadow.setYOffset(6)
        shadow.setColor(QColor(0, 0, 0, 18))
        card.setGraphicsEffect(shadow)
        root.addWidget(card, 1)

        # --- footer
        root.addSpacing(16)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = QLabel("Ready. Pick a folder of UI/UX screenshots to begin.")
        self.status.setObjectName("status")
        root.addSpacing(8)
        root.addWidget(self.status)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        label.setFixedWidth(72)
        return label

    # ------------------------------------------------------------- behaviour
    def _toggle_key(self, shown: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)

    def _browse(self) -> None:
        start = self.folder_edit.text().strip()
        if not Path(start or "").is_dir():
            start = str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder of UI/UX screenshots", start)
        if chosen:
            self.folder_edit.setText(chosen)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _set_busy(self, busy: bool) -> None:
        self.analyze_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.folder_edit.setEnabled(not busy)
        self.key_edit.setEnabled(not busy)
        self.model_box.setEnabled(not busy)
        if busy:
            self.apply_btn.setEnabled(False)

    # --------------------------------------------------------------- analyze
    def _start_analyze(self) -> None:
        folder = self.folder_edit.text().strip()
        api_key = self.key_edit.text().strip()
        if not folder or not Path(folder).is_dir():
            QMessageBox.warning(self, APP_TITLE, "Please choose a valid folder first.")
            return
        if not api_key:
            QMessageBox.warning(self, APP_TITLE, "Please enter your OpenAI API key.")
            return

        images = core.list_images(folder)
        if not images:
            QMessageBox.information(self, APP_TITLE, "No supported images found in that folder.")
            return

        self._persist_config()
        self._populate_table([core.RenamePlan(source=p, suggested_title="") for p in images])

        self.progress.setRange(0, len(self.plans))
        self.progress.setValue(0)
        self.cancel_event.clear()
        self._set_busy(True)
        self._set_status(f"Analyzing {len(self.plans)} image(s) with {self.model_box.currentText()}…")

        self.thread = QThread()
        self.worker = AnalyzeWorker(api_key, self.model_box.currentText(), self.plans, self.cancel_event)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.row_done.connect(self._refresh_row)
        self.worker.row_error.connect(lambda plan, _err: self._refresh_row(plan))
        self.worker.row_skipped.connect(self._refresh_row)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.fatal.connect(self._on_fatal)
        self.worker.finished.connect(self._on_analyze_finished)
        self.thread.start()

    def _on_fatal(self, message: str) -> None:
        QMessageBox.critical(self, APP_TITLE, message)
        self._set_status("Error. See dialog for details.")

    def _on_analyze_finished(self) -> None:
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
            self.thread = None
            self.worker = None
        self._set_busy(False)
        ready = sum(1 for p in self.plans if p.status == "ready")
        errors = sum(1 for p in self.plans if p.status == "error")
        if self.cancel_event.is_set():
            self._set_status(f"Cancelled. {ready} ready, {errors} failed.")
        else:
            self._set_status(
                f"Analysis done. {ready} ready to rename, {errors} failed. "
                "Double-click a name to edit it."
            )
        self.apply_btn.setEnabled(ready > 0)

    def _cancel(self) -> None:
        self.cancel_event.set()
        self._set_status("Cancelling… finishing in-flight requests.")

    # ----------------------------------------------------------------- apply
    def _apply(self) -> None:
        ready = [p for p in self.plans if p.status == "ready"]
        if not ready:
            QMessageBox.information(self, APP_TITLE, "Nothing to rename. Analyze a folder first.")
            return
        confirm = QMessageBox.question(
            self, APP_TITLE,
            f"Rename {len(ready)} file(s)? You can undo right after.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return

        used: set[str] = set()
        for plan in self.plans:
            if plan.status != "ready":
                used.add(plan.source.name.lower())
        for plan in ready:
            base = core.sanitize_title(plan.suggested_title)
            plan.target_name = core.build_unique_name(base, plan.source.suffix, used)

        record = core.apply_renames(self.plans)
        self.last_undo = record if record.moves else None

        renamed = sum(1 for p in self.plans if p.status == "renamed")
        errors = sum(1 for p in self.plans if p.status == "error")
        for plan in self.plans:
            self._refresh_row(plan)
        self.undo_btn.setEnabled(self.last_undo is not None)
        self.apply_btn.setEnabled(False)
        msg = f"Renamed {renamed} file(s)."
        if errors:
            msg += f" {errors} failed."
        self._set_status(msg)

    def _undo(self) -> None:
        if not self.last_undo:
            return
        restored = core.undo_renames(self.last_undo)
        for plan in self.plans:
            plan.source = plan.source.parent / plan.original_name
            plan.status = "pending"
            self._refresh_row(plan)
        self.last_undo = None
        self.undo_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self._set_status(f"Undo complete. Restored {restored} file(s). Re-analyze to rename again.")

    # ------------------------------------------------------------ table rows
    def _populate_table(self, plans: list) -> None:
        self._loading = True
        self.plans = plans
        self.table.setRowCount(0)
        self.table.setRowCount(len(plans))
        for row, plan in enumerate(plans):
            plan.row = row  # type: ignore[attr-defined]
            for col in range(3):
                item = QTableWidgetItem("")
                if col == 2:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
            self._write_row(plan)
        self._loading = False

    def _write_row(self, plan) -> None:
        row = getattr(plan, "row", None)
        if row is None:
            return
        status_label = {
            "pending": "Queued",
            "ready": "Ready",
            "error": "Error",
            "renamed": "Renamed",
            "skipped": "Skipped",
        }.get(plan.status, plan.status)
        if plan.status == "renamed":
            suggested = plan.source.name
            color = QColor(C_SUCCESS)
        elif plan.status == "error":
            suggested = plan.error or "error"
            color = QColor(C_ERROR)
        elif plan.status == "ready":
            suggested = plan.suggested_title or "…"
            color = QColor(C_PRIMARY)
        else:
            suggested = plan.suggested_title or "…"
            color = QColor(C_MUTED)

        values = (status_label, plan.original_name, suggested)
        for col, text in enumerate(values):
            item = self.table.item(row, col)
            if item is None:
                continue
            item.setText(text)
            if col == 0:
                item.setForeground(color)
            elif col == 1:
                item.setForeground(QColor(C_SECONDARY))
            else:
                item.setForeground(color)

    def _refresh_row(self, plan) -> None:
        self._loading = True
        self._write_row(plan)
        self._loading = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 2:
            return
        row = item.row()
        if row < 0 or row >= len(self.plans):
            return
        plan = self.plans[row]
        if plan.status == "renamed":
            return  # already applied; ignore edits
        new_val = item.text().strip()
        if not new_val:
            return
        plan.suggested_title = core.sanitize_title(new_val)
        plan.status = "ready"
        plan.error = None
        self._refresh_row(plan)
        self.apply_btn.setEnabled(True)

    # --------------------------------------------------------------- config
    def _load_config_into_fields(self) -> None:
        self.folder_edit.setText(self.config_data.get("last_folder", ""))
        self.key_edit.setText(self.config_data.get("api_key", ""))
        saved_model = self.config_data.get("model", core.DEFAULT_MODEL)
        index = self.model_box.findText(saved_model)
        if index >= 0:
            self.model_box.setCurrentIndex(index)

    def _persist_config(self) -> None:
        self.config_data.update({
            "last_folder": self.folder_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "model": self.model_box.currentText(),
        })
        core.save_config(self.config_data)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.cancel_event.set()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(2000)
        self._persist_config()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    sans = _pick_family(["Geist", "Geist Sans", "Inter", "Segoe UI"], "Segoe UI")
    app.setFont(QFont(sans, 10))
    app.setStyleSheet(_build_stylesheet(sans))
    window = App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

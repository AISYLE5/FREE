from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFontMetrics, QIntValidator, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import styles as _s
from .message_box import QMessageBox

from .config import (
    _sanitize_settings,
    load_json,
    load_settings,
    load_task_directory,
    order_tasks,
    resolve_path,
    save_settings,
)
from .models import RunResult, RunStatus, TaskDefinition
from .mumu import MuMuController, MuMuError, connect_to_mumu, mumu_cli_path
from .notifications import send_run_notification
from .onnx_ocr import OnnxOcrClient
from .pruning import clear_output_files
from .ocr_models import (
    AUTO_SOURCE,
    DET_MODELS,
    DownloadCancelled,
    MODEL_SOURCES,
    PP_OCR_MODELS,
    REC_MODELS,
    SOURCE_KEYS,
    delete_model,
    download_model,
    installed_models,
    model_root,
)

# Settings defaults. Shared between loading, change detection, and collection so
# the two comparison/collection paths cannot drift apart.
_DEFAULT_CLOSE_MUMU_AFTER_RUN = False
_DEFAULT_CLOSE_MUMU_APP_AFTER_RUN = False
_DEFAULT_EMAIL_ENABLED = False
_DEFAULT_TASK_EXECUTION_COUNT = 1
_DEFAULT_MAX_LOG_FILES = -1
_DEFAULT_MAX_SCREENSHOT_FILES = -1
_DEFAULT_CLEANUP_MODE = "recycle"
_DEFAULT_MUMU_DIRECTORY = ""
_DEFAULT_MUMU_VM_INDEX = 0
_DEFAULT_QQ_GROUP_NAME = "群聊-转发广告"
_DEFAULT_DET_MODEL = "PP-OCRv6_small_det"
_DEFAULT_REC_MODEL = "PP-OCRv6_small_rec"
_DEFAULT_SMTP_HOST = "smtp.qq.com"
_DEFAULT_SMTP_PORT = 465
_DEFAULT_SMTP_SECURITY = "ssl"
_DEFAULT_SMTP_USERNAME = ""
_DEFAULT_SMTP_PASSWORD = ""
_DEFAULT_SUBJECT_PREFIX = "FREE"
_DEFAULT_SMTP_TIMEOUT_SECONDS = 20
_DEFAULT_RECIPIENTS = ""


class ElidedLabel(QLabel):
    """QLabel that shows an ellipsis instead of clipping long text."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._full_text = text

    def setText(self, text: str) -> None:
        """Keep the full (non-elided) text in sync for the next resize."""
        self._full_text = str(text)
        super().setText(text)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(80, hint.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        metrics = QFontMetrics(self.font())
        # Call the base setText so the full text is never overwritten by the
        # elided display text.
        QLabel.setText(
            self,
            metrics.elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, max(1, self.width())
            ),
        )


class ModelRowCard(QFrame):
    """Clickable model row card; clicking anywhere selects the model."""

    clicked = Signal(str)

    def __init__(self, model_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._model_name = model_name

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._model_name)


class SettingsComboBox(QComboBox):
    """Unified combo box with a custom popup and no wheel switching."""

    class _PopupFrame(QFrame):
        """Translucent popup whose rounded border is painted exactly once."""

        def paintEvent(self, event) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#b9d3ca"), 1.0))
            painter.setBrush(QColor("#ffffff"))
            # Keep the stroke inside the window so the translucent corners stay
            # transparent and cannot show a second rectangular frame.
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 7, 7)

    _POPUP_STYLE = _s.COMBO_POPUP_QSS

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings_popup: QFrame | None = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor("#49615f"),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        center_x = self.width() - 16
        center_y = self.height() // 2
        painter.drawLine(center_x - 4, center_y - 2, center_x, center_y + 2)
        painter.drawLine(center_x, center_y + 2, center_x + 4, center_y - 2)

    def showPopup(self) -> None:
        self.hidePopup()
        popup = self._PopupFrame(
            self,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        popup.setObjectName("settingsComboPopup")
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        popup.setAutoFillBackground(False)
        popup.setStyleSheet(self._POPUP_STYLE)
        popup_layout = QVBoxLayout(popup)
        # Reserve the same visual breathing room on both sides of the list;
        # the extra right pixel also keeps a scrollbar from touching the frame.
        popup_layout.setContentsMargins(5, 5, 6, 5)
        popup_layout.setSpacing(0)
        option_list = QListWidget(popup)
        option_list.setObjectName("settingsComboPopupList")
        option_list.setFrameShape(QFrame.Shape.NoFrame)
        option_list.setLineWidth(0)
        option_list.setMidLineWidth(0)
        option_list.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        option_list.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        option_list.setAutoFillBackground(False)
        option_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        option_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Eleven 34px rows (0–10) fit as one complete popup.  Only longer
        # lists should become scrollable; otherwise the last item is clipped.
        has_overflow = self.count() > 11
        option_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if has_overflow
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        for index in range(self.count()):
            item = QListWidgetItem(self.itemText(index))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setSizeHint(QSize(0, 34))
            model_item = self.model().item(index)
            if model_item is not None and not model_item.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            option_list.addItem(item)
        option_list.setCurrentRow(max(0, self.currentIndex()))
        option_list.itemClicked.connect(self._accept_popup_item)
        option_list.itemActivated.connect(self._accept_popup_item)
        option_list.setFixedHeight(
            max(34, self.count() * 34) if not has_overflow else 350
        )
        popup_layout.addWidget(option_list)
        # Keep every popup edge exactly aligned with its combo box.  A minimum
        # popup width makes shorter controls grow to the right unexpectedly.
        popup.setFixedWidth(max(1, self.width()))
        popup.setFixedHeight(option_list.height() + 10)
        origin = self.mapToGlobal(QPoint(0, 0))
        below = QPoint(origin.x(), origin.y() + self.height())
        screen = QApplication.screenAt(origin) or QApplication.primaryScreen()
        popup_position = below
        if screen is not None:
            available = screen.availableGeometry()
            parent_window = self.window()
            if parent_window is not None and parent_window.isVisible():
                # The notification must stay inside the current application
                # window, not merely inside the physical monitor.
                available = available.intersected(parent_window.frameGeometry())
            x = max(available.left(), min(below.x(), available.right() - popup.width() + 1))
            popup_height = popup.height()
            below_y = below.y()
            above_y = origin.y() - popup_height
            if below_y + popup_height <= available.bottom() + 1:
                y = below_y
            elif above_y >= available.top():
                y = above_y
            else:
                # Keep the popup fully visible even when neither side has
                # enough room (for example on a short or scaled display).
                y = max(available.top(), available.bottom() - popup_height + 1)
            popup_position = QPoint(x, y)
        popup.move(popup_position)
        self._settings_popup = popup
        popup.show()
        # Windows may apply its own placement while a Qt.Popup is being shown;
        # apply the calculated in-window position once more afterwards.
        popup.move(popup_position)
        option_list.setFocus()

    def hidePopup(self) -> None:
        popup = self._settings_popup
        self._settings_popup = None
        if popup is not None:
            popup.close()
            popup.deleteLater()

    def _accept_popup_item(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int) and 0 <= index < self.count():
            self.setCurrentIndex(index)
            self.activated.emit(index)
        self.hidePopup()

    def wheelEvent(self, event) -> None:
        event.ignore()


class ExecutionCountComboBox(SettingsComboBox):
    """Dropdown for how many times a task runs during “执行全部”."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        for count in range(11):
            self.addItem(f"{count} 次", count)
        self.setValue(1)

    def value(self) -> int:
        return int(self.currentData())

    def setValue(self, value: int) -> None:
        index = self.findData(min(10, max(0, int(value))))
        self.setCurrentIndex(max(0, index))


class ModelDownloadWorker(QObject):
    """Download a PP-OCRv6 model in a background thread."""

    progress = Signal(str, int, int)
    succeeded = Signal(str)
    failed = Signal(str, str)
    cancelled = Signal(str)

    def __init__(self, name: str, root: Path, source: str = AUTO_SOURCE):
        super().__init__()
        self.name = name
        self.root = root
        self.source = source
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            download_model(
                self.name,
                self.root,
                progress_callback=lambda done, total: self.progress.emit(self.name, done, total),
                cancel_event=self._cancel_event,
                source=self.source,
            )
        except DownloadCancelled:
            self.cancelled.emit(self.name)
        except Exception as exc:
            self.failed.emit(self.name, str(exc))
        else:
            self.succeeded.emit(self.name)


def _build_confirm_message_box(parent: QWidget | None, title: str, text: str) -> QMessageBox:
    message_box = QMessageBox(parent)
    message_box.setWindowTitle(title)
    message_box.setText(text)
    cancel_button = message_box.addButton("取消", QMessageBox.ButtonRole.NoRole)
    confirm_button = message_box.addButton("确认", QMessageBox.ButtonRole.NoRole)
    cancel_button.setObjectName("messageBoxAction")
    confirm_button.setObjectName("messageBoxAction")
    message_box.setDefaultButton(confirm_button)
    message_box.setStyleSheet(_s.MESSAGE_BOX_QSS)
    return message_box


def confirm_dialog(parent: QWidget | None, title: str, text: str) -> bool:
    """Show a confirmation box with 取消 on the left and 确认 on the right."""

    message_box = _build_confirm_message_box(parent, title, text)
    message_box.exec()
    return message_box.clickedButton() == message_box.buttons()[1]



class SettingsDialog(QDialog):
    log_message = Signal(str)
    ocr_test_finished = Signal()

    def __init__(
        self,
        settings_path: Path,
        parent: QWidget | None = None,
        base_directory: Path | None = None,
        embedded: bool = False,
    ):
        super().__init__(parent)
        self.settings_path = settings_path
        self.base_directory = base_directory or settings_path.parent
        self.embedded = embedded
        self.settings = load_settings(settings_path)
        tasks_directory = self.base_directory / "config" / "tasks"
        raw_tasks, _config_errors = load_task_directory(
            tasks_directory,
            variables={"qq_group_name": self.settings.get("qq_group_name", "")},
        )
        self.tasks = order_tasks(raw_tasks, self.settings.get("task_order"))
        self._model_root = model_root(self.settings, self.base_directory)
        self._download_threads: dict[str, QThread] = {}
        self._download_workers: dict[str, ModelDownloadWorker] = {}
        self._model_radios: dict[str, QRadioButton] = {}
        self._model_action: dict[str, QPushButton] = {}
        self._model_cards: dict[str, ModelRowCard] = {}
        self._model_groups: dict[str, QButtonGroup] = {}
        self._task_execution_combos: dict[str, ExecutionCountComboBox] = {}
        self._discard_confirmed = False
        self._loading_settings = False
        self._build_ui()
        self._load_setting_values()
        self._load_model_values()

    def _build_ui(self) -> None:
        self.setWindowTitle("FREE · 设置")
        if not self.embedded:
            self.setMinimumSize(620, 620)
            self.resize(720, 700)

        root = QVBoxLayout(self)
        if self.embedded:
            root.setContentsMargins(0, 0, 0, 0)
        else:
            root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(16)

        nav_panel = QFrame()
        nav_panel.setObjectName("settingsNavPanel")
        nav_panel.setFixedWidth(176)
        nav_panel_layout = QVBoxLayout(nav_panel)
        nav_panel_layout.setContentsMargins(14, 14, 14, 14)
        nav_panel_layout.setSpacing(6)
        nav = QListWidget()
        nav.setObjectName("settingsNav")
        nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        nav.addItems(["运行设置", "执行次数", "OCR 模型", "邮件通知"])
        nav.setFrameShape(QFrame.Shape.NoFrame)
        nav.setSpacing(6)
        nav.setUniformItemSizes(True)
        nav.setCurrentRow(0)
        nav_panel_layout.addWidget(nav, 1)
        stacked = QStackedWidget()
        stacked.setObjectName("settingsStack")
        nav.currentRowChanged.connect(stacked.setCurrentIndex)
        content_row = QHBoxLayout()
        content_row.setSpacing(16)
        content_row.addWidget(nav_panel)
        content_row.addWidget(stacked, 1)

        stacked.addWidget(self._build_run_page())
        stacked.addWidget(self._build_retry_page())
        stacked.addWidget(self._build_ocr_page())
        stacked.addWidget(self._build_email_page())

        root.addLayout(content_row, 1)

        footer = QFrame()
        footer.setObjectName("settingsFooter")
        action_row = QHBoxLayout(footer)
        action_row.setContentsMargins(0, 14, 0, 0)
        action_row.setSpacing(10)
        action_row.addStretch(1)

        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("settingsCancelButton")
        cancel_button.clicked.connect(self.reject)

        save_button = QPushButton("保存")
        save_button.setObjectName("settingsSaveButton")
        save_button.setText("保存")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save)
        action_row.addWidget(cancel_button)
        action_row.addWidget(save_button)
        root.addWidget(footer)
        self.setStyleSheet(self._style_sheet())
        self._relayout_model_sections()

    def _build_form_layout(self, horizontal_spacing: int) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(horizontal_spacing)
        form.setVerticalSpacing(14)
        return form

    def _build_option_card(self, heading: str | None, form: QFormLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsOptionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(16)
        if heading:
            layout.addWidget(self._build_card_heading(heading))
        layout.addLayout(form)
        return card

    def _add_int_field(
        self,
        form: QFormLayout,
        attr: str,
        label: str,
        low: int,
        high: int,
        placeholder: str | None = None,
    ) -> QLineEdit:
        edit = QLineEdit()
        edit.setValidator(QIntValidator(low, high, edit))
        if placeholder is not None:
            edit.setPlaceholderText(placeholder)
        setattr(self, attr, edit)
        form.addRow(label, edit)
        return edit

    def _make_switch_button(self, attr: str) -> QPushButton:
        switch = QPushButton()
        switch.setObjectName("settingsSwitch")
        switch.setCheckable(True)
        switch.setFixedSize(76, 34)
        switch.toggled.connect(lambda checked: self._set_switch_text(switch, checked))
        setattr(self, attr, switch)
        return switch

    def _build_email_page(self) -> QWidget:
        container = QFrame()
        container.setObjectName("settingsTabPage")
        page = QVBoxLayout(container)
        page.setContentsMargins(20, 12, 20, 12)
        page.setSpacing(20)
        page.addWidget(self._build_page_heading("邮件通知"))

        form = self._build_form_layout(20)
        self.smtp_host = QLineEdit()
        form.addRow("SMTP 服务器", self.smtp_host)

        self._add_int_field(form, "smtp_port", "端口", 1, 65535)
        self.smtp_security = SettingsComboBox()
        self.smtp_security.addItem("SSL", "ssl")
        self.smtp_security.addItem("STARTTLS", "starttls")
        self.smtp_security.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.smtp_security.currentIndexChanged.connect(self._sync_security_port)
        form.addRow("安全方式", self.smtp_security)

        self.smtp_username = QLineEdit()
        form.addRow("邮箱地址", self.smtp_username)

        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("SMTP 授权码", self.smtp_password)

        self.recipients = QLineEdit()
        form.addRow("收件地址", self.recipients)

        self.subject_prefix = QLineEdit()
        form.addRow("邮件主题", self.subject_prefix)

        self._add_int_field(form, "smtp_timeout_seconds", "发送超时", 1, 120)
        page.addWidget(self._build_option_card(None, form))

        self.test_button = QPushButton("发送测试邮件")
        self.test_button.setObjectName("settingsTestButton")
        # Reserve slack beyond the text so the label never clips at scaled
        # display resolutions (a naked exact-fit button looks distorted
        # while pressed when its last glyphs are cut off).
        self.test_button.setMinimumWidth(120)
        self.test_button.clicked.connect(self._send_test_email)
        test_row = QHBoxLayout()
        test_row.setContentsMargins(2, 0, 2, 0)
        test_row.addWidget(self.test_button)
        test_row.addStretch(1)
        page.addLayout(test_row)
        page.addStretch(1)
        return container

    def _build_run_page(self) -> QWidget:
        container = QFrame()
        container.setObjectName("settingsTabPage")
        page = QVBoxLayout(container)
        page.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 12, 20, 12)
        content_layout.setSpacing(20)
        content_layout.addWidget(self._build_page_heading("运行设置"))

        runtime_form = self._build_form_layout(24)
        self.close_mumu_after_run = self._make_switch_button("close_mumu_after_run")
        runtime_form.addRow("任务结束后关闭 MuMu 实例", self.close_mumu_after_run)
        self.close_mumu_app_after_run = self._make_switch_button("close_mumu_app_after_run")
        runtime_form.addRow("任务结束后退出 MuMu 程序", self.close_mumu_app_after_run)
        self.email_enabled = self._make_switch_button("email_enabled")
        runtime_form.addRow("任务结束后发送邮件通知", self.email_enabled)

        self._add_int_field(
            runtime_form,
            "max_log_files_edit",
            "日志最大保存数",
            -1,
            1000,
            "-1 无限制 ; 0 不保存",
        )

        self._add_int_field(
            runtime_form,
            "max_screenshot_files_edit",
            "截图最大保存数",
            -1,
            1000,
            "-1 无限制 ; 0 不保存",
        )

        self.cleanup_mode_combo = SettingsComboBox()
        self.cleanup_mode_combo.addItem("删除至回收站", "recycle")
        self.cleanup_mode_combo.addItem("永久删除", "permanent")
        self.cleanup_mode_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        runtime_form.addRow("删除方式", self.cleanup_mode_combo)

        cleanup_row = QHBoxLayout()
        cleanup_row.setSpacing(8)
        self.clear_logs_button = QPushButton("清理全部日志")
        self.clear_logs_button.setObjectName("dangerButton")
        self.clear_logs_button.setMinimumWidth(118)
        self.clear_logs_button.clicked.connect(lambda: self._clear_output_files("logs"))
        self.clear_screenshots_button = QPushButton("清理全部截图")
        self.clear_screenshots_button.setObjectName("dangerButton")
        self.clear_screenshots_button.setMinimumWidth(118)
        self.clear_screenshots_button.clicked.connect(
            lambda: self._clear_output_files("screenshots")
        )
        cleanup_row.addWidget(self.clear_logs_button)
        cleanup_row.addWidget(self.clear_screenshots_button)
        cleanup_row.addStretch(1)
        runtime_form.addRow("清理文件", cleanup_row)
        settings_card = self._build_option_card(None, runtime_form)

        mumu_form = self._build_form_layout(24)
        self.mumu_directory_edit = QLineEdit()
        mumu_folder_row = QHBoxLayout()
        mumu_folder_row.setSpacing(8)
        mumu_folder_row.addWidget(self.mumu_directory_edit, 1)
        self.mumu_browse_button = QPushButton("浏览")
        self.mumu_browse_button.setObjectName("settingsBrowseButton")
        self.mumu_browse_button.clicked.connect(self._browse_mumu_directory)
        mumu_folder_row.addWidget(self.mumu_browse_button)
        mumu_form.addRow("MuMu 模拟器文件夹", mumu_folder_row)

        self.qq_group_name_edit = QLineEdit()
        mumu_form.addRow("QQ 转发对象名称", self.qq_group_name_edit)

        instance_row = QHBoxLayout()
        instance_row.setSpacing(8)
        self.mumu_vm_index_combo = SettingsComboBox()
        self.mumu_vm_index_combo.setMinimumWidth(180)
        instance_row.addWidget(self.mumu_vm_index_combo, 1)
        self.mumu_refresh_button = QPushButton("刷新实例")
        self.mumu_refresh_button.setObjectName("settingsBrowseButton")
        self.mumu_refresh_button.clicked.connect(self._refresh_mumu_instances)
        instance_row.addWidget(self.mumu_refresh_button)
        mumu_form.addRow("MuMu 实例编号", instance_row)
        card_layout = settings_card.layout()
        if isinstance(card_layout, QVBoxLayout):
            card_layout.addLayout(mumu_form)
        content_layout.addWidget(settings_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        page.addWidget(scroll)
        return container

    def _build_retry_page(self) -> QWidget:
        container = QFrame()
        container.setObjectName("settingsTabPage")
        page = QVBoxLayout(container)
        page.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(18)
        content_layout.addWidget(
            self._build_page_heading(
                "执行次数",
                note="“执行全部”时按设定的次数执行；0 次表示不执行。"
                "单独选择任务执行时，即使为 0 也会执行一次。",
            )
        )

        retry_card = QFrame()
        retry_card.setObjectName("settingsOptionCard")
        retry_card_layout = QVBoxLayout(retry_card)
        retry_card_layout.setContentsMargins(22, 20, 22, 20)
        retry_card_layout.setSpacing(10)
        for task in self.tasks:
            row = QFrame()
            row.setObjectName("settingsRetryRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(16, 12, 14, 12)
            row_layout.setSpacing(10)

            name_label = QLabel(task.name)
            name_label.setObjectName("settingsRunLabel")
            name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row_layout.addWidget(name_label, 1)

            id_label = QLabel(task.id)
            id_label.setObjectName("settingsRetryId")
            row_layout.addWidget(id_label)

            execution_combo = ExecutionCountComboBox()
            execution_combo.setFixedWidth(112)
            row_layout.addWidget(execution_combo)
            self._task_execution_combos[task.id] = execution_combo
            retry_card_layout.addWidget(row)

        content_layout.addWidget(retry_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        page.addWidget(scroll)
        return container

    def _build_ocr_page(self) -> QWidget:
        container = QFrame()
        container.setObjectName("settingsTabPage")
        page = QVBoxLayout(container)
        page.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        ocr_layout = QVBoxLayout(content)
        ocr_layout.setContentsMargins(20, 20, 20, 20)
        ocr_layout.setSpacing(18)
        ocr_header = QHBoxLayout()
        ocr_header.setSpacing(0)
        ocr_header.addWidget(self._build_page_heading("OCR 模型"))
        ocr_header.addSpacing(24)
        source_label = QLabel("下载源")
        source_label.setObjectName("settingsOcrFeedback")
        self.download_source_combo = SettingsComboBox()
        self.download_source_combo.addItem("自动", AUTO_SOURCE)
        for key in SOURCE_KEYS:
            self.download_source_combo.addItem(MODEL_SOURCES[key]["label"], key)
        ocr_header.addWidget(source_label)
        ocr_header.addWidget(self.download_source_combo)
        ocr_header.addStretch(1)

        ocr_action_row = QHBoxLayout()
        ocr_action_row.setSpacing(8)
        self.ocr_feedback_label = QLabel("")
        self.ocr_feedback_label.setObjectName("settingsOcrFeedback")
        ocr_action_row.addWidget(self.ocr_feedback_label)
        self.test_ocr_button = QPushButton("测试识别")
        self.test_ocr_button.setObjectName("settingsTestButton")
        self.test_ocr_button.clicked.connect(self._test_ocr)
        self.refresh_ocr_button = QPushButton("刷新")
        self.refresh_ocr_button.setObjectName("settingsTestButton")
        self.refresh_ocr_button.clicked.connect(self._refresh_model_status)
        ocr_action_row.addWidget(self.test_ocr_button)
        ocr_action_row.addWidget(self.refresh_ocr_button)
        ocr_header.addLayout(ocr_action_row)
        ocr_layout.addLayout(ocr_header)
        self._model_sections_layout = QGridLayout()
        self._model_sections_layout.setContentsMargins(0, 0, 0, 0)
        self._model_sections_layout.setHorizontalSpacing(12)
        self._model_sections_layout.setVerticalSpacing(16)
        self._model_section_widgets = (
            self._build_model_section("det", "检测模型 · Det"),
            self._build_model_section("rec", "识别模型 · Rec"),
        )
        self._model_sections_stacked: bool | None = None
        ocr_layout.addLayout(self._model_sections_layout)
        ocr_layout.addStretch(1)
        scroll.setWidget(content)
        page.addWidget(scroll)
        return container

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_model_sections_layout"):
            self._relayout_model_sections()

    def _relayout_model_sections(self) -> None:
        stacked = self.width() < 960
        if stacked == self._model_sections_stacked:
            return
        for widget in self._model_section_widgets:
            self._model_sections_layout.removeWidget(widget)
        if stacked:
            self._model_sections_layout.addWidget(self._model_section_widgets[0], 0, 0)
            self._model_sections_layout.addWidget(self._model_section_widgets[1], 1, 0)
            self._model_sections_layout.setColumnStretch(0, 1)
            self._model_sections_layout.setColumnStretch(1, 0)
        else:
            self._model_sections_layout.addWidget(self._model_section_widgets[0], 0, 0)
            self._model_sections_layout.addWidget(self._model_section_widgets[1], 0, 1)
            self._model_sections_layout.setColumnStretch(0, 1)
            self._model_sections_layout.setColumnStretch(1, 1)
        self._model_sections_stacked = stacked

    @staticmethod
    def _build_page_heading(
        title: str,
        subtitle: str | None = None,
        *,
        note: str | None = None,
    ) -> QWidget:
        heading = QWidget()
        layout = QVBoxLayout(heading)
        layout.setContentsMargins(2, 0, 2, 2)
        layout.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(12)
        title_label = QLabel(title)
        title_label.setObjectName("settingsSectionTitle")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        if note:
            note_label = QLabel(note)
            note_label.setObjectName("settingsSectionNote")
            note_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            title_row.addWidget(note_label)
        layout.addLayout(title_row)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("settingsSectionSubtitle")
            layout.addWidget(subtitle_label)
        return heading

    @staticmethod
    def _build_card_heading(title: str) -> QWidget:
        heading = QWidget()
        layout = QHBoxLayout(heading)
        layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setObjectName("settingsCardTitle")
        layout.addWidget(title_label)
        layout.addStretch(1)
        return heading

    def _build_model_section(self, kind: str, title: str) -> QFrame:
        names = DET_MODELS if kind == "det" else REC_MODELS
        section = QFrame()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("settingsGroupTitle")
        layout.addWidget(title_label)

        group = QButtonGroup(self)
        group.setExclusive(True)
        self._model_groups[kind] = group
        for name in names:
            layout.addWidget(self._build_model_row(name, group))
        return section

    def _build_model_row(self, name: str, group: QButtonGroup) -> QFrame:
        info = PP_OCR_MODELS[name]
        card = ModelRowCard(name)
        card.setObjectName("settingsModelRow")
        card.clicked.connect(self._select_model_card)
        self._model_cards[name] = card
        row = QHBoxLayout(card)
        row.setContentsMargins(26, 18, 26, 18)
        row.setSpacing(10)

        radio = QRadioButton()
        radio.setObjectName("settingsModelRadio")
        group.addButton(radio)
        self._model_radios[name] = radio
        row.addWidget(radio)

        name_label = ElidedLabel(info.name)
        name_label.setObjectName("settingsRunLabel")
        name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(name_label, 1)

        size_label = QLabel(f"{info.size_mb:.1f} MB")
        size_label.setObjectName("settingsModelSize")
        size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(size_label)

        action_button = QPushButton("下载")
        action_button.setObjectName("settingsModelAction")
        action_button.clicked.connect(lambda _checked=False, n=name: self._toggle_model_action(n))
        self._model_action[name] = action_button
        row.addWidget(action_button)
        return card

    def _select_model_card(self, name: str) -> None:
        radio = self._model_radios.get(name)
        if radio is not None and radio.isEnabled():
            radio.setChecked(True)

    def _toggle_model_action(self, name: str) -> None:
        if name in self._download_threads:
            self._cancel_download(name)
            return
        if self._model_action[name].property("downloaded"):
            self._delete_model(name)
        else:
            self._start_download(name)

    def _load_model_values(self) -> None:
        self._refresh_model_statuses()
        for kind, selected_key in (("det", "ocr_det_model"), ("rec", "ocr_rec_model")):
            names = DET_MODELS if kind == "det" else REC_MODELS
            preferred = str(self.settings.get(selected_key, "")).strip()
            enabled = [name for name in names if self._model_radios[name].isEnabled()]
            selected = preferred if preferred in enabled else (enabled[0] if enabled else "")
            if selected:
                self._model_radios[selected].setChecked(True)

    def _refresh_model_statuses(self) -> dict[str, bool]:
        installed = installed_models(self._model_root)
        for name, present in installed.items():
            action = self._model_action[name]
            action.setText("删除" if present else "下载")
            action.setProperty("downloaded", present)
            action.setEnabled(True)
            radio = self._model_radios[name]
            if not present and radio.isChecked():
                group = self._model_groups[PP_OCR_MODELS[name].kind]
                group.setExclusive(False)
                radio.setChecked(False)
                group.setExclusive(True)
            radio.setEnabled(present)
            card = self._model_cards.get(name)
            if card is not None:
                card.setCursor(
                    Qt.CursorShape.PointingHandCursor if present else Qt.CursorShape.ArrowCursor
                )
        self._refresh_style()
        return installed

    @Slot()
    def _refresh_model_status(self) -> None:
        """Rescan the local model directory and reapply the active selection."""

        self.refresh_ocr_button.setEnabled(False)
        self.refresh_ocr_button.setText("刷新中…")
        try:
            installed = self._refresh_model_statuses()
            self._reapply_model_selection()
        finally:
            self.refresh_ocr_button.setText("刷新")
            self.refresh_ocr_button.setEnabled(True)
        downloaded = sum(1 for present in installed.values() if present)
        self.ocr_feedback_label.setText(f"已刷新 · {downloaded} 个模型已下载")
        QTimer.singleShot(2500, self._clear_ocr_feedback)

    def _clear_ocr_feedback(self) -> None:
        self.ocr_feedback_label.clear()

    def rescan_model_status(self) -> None:
        """Silently rescan installed models when the settings page is reopened."""

        self._refresh_model_statuses()
        self._reapply_model_selection()

    def _reapply_model_selection(self) -> None:
        """Keep the current selection, or fall back when it is no longer installed."""

        for kind, selected_key in (("det", "ocr_det_model"), ("rec", "ocr_rec_model")):
            names = DET_MODELS if kind == "det" else REC_MODELS
            checked = next(
                (name for name in names if self._model_radios[name].isChecked()),
                "",
            )
            if checked and self._model_radios[checked].isEnabled():
                continue
            enabled = [name for name in names if self._model_radios[name].isEnabled()]
            if not enabled:
                group = self._model_groups[kind]
                group.setExclusive(False)
                for name in names:
                    self._model_radios[name].setChecked(False)
                group.setExclusive(True)
                continue
            preferred = str(self.settings.get(selected_key, "")).strip()
            selected = preferred if preferred in enabled else enabled[0]
            self._model_radios[selected].setChecked(True)

    def _selected_model(self, kind: str) -> str:
        names = DET_MODELS if kind == "det" else REC_MODELS
        for name in names:
            if self._model_radios[name].isChecked():
                return name
        key = "ocr_det_model" if kind == "det" else "ocr_rec_model"
        return str(self.settings.get(key, names[0]))

    def _start_download(self, name: str) -> None:
        if name in self._download_threads:
            return
        self._model_action[name].setEnabled(True)
        self._model_action[name].setText("下载中…")

        thread = QThread(self)
        source = str(self.download_source_combo.currentData() or AUTO_SOURCE)
        worker = ModelDownloadWorker(name, self._model_root, source)
        worker.moveToThread(thread)
        self._download_threads[name] = thread
        self._download_workers[name] = worker

        thread.started.connect(worker.run)
        worker.progress.connect(lambda n, done, total: self._on_download_progress(n, done, total))
        worker.succeeded.connect(lambda n: self._on_download_succeeded(n))
        worker.failed.connect(lambda n, error: self._on_download_failed(n, error))
        worker.cancelled.connect(lambda n: self._on_download_cancelled(n))
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda n=name: self._download_finished(n))
        thread.start()

    def _cancel_download(self, name: str) -> None:
        worker = self._download_workers.get(name)
        if worker is None:
            return
        action = self._model_action.get(name)
        if action is not None:
            action.setEnabled(False)
            action.setText("取消中…")
        worker.cancel()

    def _on_download_cancelled(self, name: str) -> None:
        self._refresh_model_statuses()
        self.ocr_feedback_label.setText(f"已取消 {name} 的下载")
        QTimer.singleShot(2500, self._clear_ocr_feedback)

    def _download_finished(self, name: str) -> None:
        thread = self._download_threads.pop(name, None)
        self._download_workers.pop(name, None)
        if thread is not None:
            thread.deleteLater()

    def _on_download_progress(self, name: str, done: int, total: int) -> None:
        button = self._model_action.get(name)
        if button is None:
            return
        if total > 0:
            percent = min(99, max(0, done * 100 // total))
            button.setText(f"下载中 {percent}%")
        else:
            button.setText("下载中…")

    def _on_download_succeeded(self, name: str) -> None:
        self._refresh_model_statuses()
        radio = self._model_radios.get(name)
        if radio is not None and radio.isEnabled():
            radio.setChecked(True)

    def _on_download_failed(self, name: str, error: str) -> None:
        self._refresh_model_statuses()
        QMessageBox.warning(self, "下载失败", f"{name}\n{error}")

    def _delete_model(self, name: str) -> None:
        mode = str(self.cleanup_mode_combo.currentData())
        mode_label = "永久删除" if mode == "permanent" else "删除至回收站"
        if not confirm_dialog(
            self,
            "删除模型",
            f"确定{mode_label}模型 {name}？",
        ):
            return
        try:
            delete_model(name, self._model_root, mode=mode)
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_model_statuses()
        self._reapply_model_selection()
        QMessageBox.information(self, "已删除", f"{name} 已{mode_label}。")

    def _test_ocr(self) -> None:
        det = self._selected_model("det")
        rec = self._selected_model("rec")
        client = OnnxOcrClient(self._model_root, det, rec)
        if not client.models_ready():
            QMessageBox.warning(self, "模型未就绪", f"请先下载并选择模型：\n{det}\n{rec}")
            return

        image_bytes, source = self._capture_test_image()
        if image_bytes is None:
            return

        self.log_message.emit(
            f"OCR 测试开始：模型 {det} + {rec}，来源 {source}"
        )
        self.test_ocr_button.setEnabled(False)
        self.test_ocr_button.setText("识别中…")
        QApplication.processEvents()
        try:
            started = time.monotonic()
            texts = client.recognize(image_bytes)
            elapsed = time.monotonic() - started
        except Exception as exc:
            self.log_message.emit(
                f"OCR 测试失败：模型 {det} + {rec}，来源 {source}，错误：{exc}"
            )
            self.ocr_test_finished.emit()
            return
        finally:
            self.test_ocr_button.setEnabled(True)
            self.test_ocr_button.setText("测试识别")

        status = "识别成功" if texts else "未识别到文字"
        self.log_message.emit(
            f"OCR 测试结果：{status}，模型 {det} + {rec}，来源 {source}，"
            f"耗时 {elapsed:.1f}s，共 {len(texts)} 行"
        )
        if texts:
            for text in texts:
                self.log_message.emit(f"OCR 识别: {text}")
        else:
            self.log_message.emit("OCR 识别: （未识别到文字）")
        self.ocr_test_finished.emit()

    def _capture_test_image(self) -> tuple[bytes | None, str]:
        adb_path = self.settings.get("adb_path")
        if isinstance(adb_path, str) and adb_path:
            try:
                adb = connect_to_mumu(self.settings)
                return adb.screenshot(), "模拟器截图"
            except Exception:
                pass

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择测试图片",
            "",
            "图片 (*.png *.jpg *.jpeg *.bmp)",
        )
        if not path:
            return None, ""
        try:
            return Path(path).read_bytes(), Path(path).name
        except OSError as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            return None, ""

    def _refresh_style(self) -> None:
        for name, button in self._model_action.items():
            self.style().unpolish(button)
            self.style().polish(button)

    def _shutdown_downloads(self) -> None:
        for worker in self._download_workers.values():
            worker.cancel()
        for name, thread in list(self._download_threads.items()):
            if thread.isRunning():
                thread.quit()
                thread.wait(3000)
            self._download_finished(name)

    def closeEvent(self, event) -> None:
        self._shutdown_downloads()
        if (
            not self._discard_confirmed
            and self._has_unsaved_changes()
            and not self._confirm_discard()
        ):
            event.ignore()
            return
        super().closeEvent(event)

    def _load_setting_values(self) -> None:
        self._loading_settings = True
        try:
            configuration = self.settings.get("email_notification", {})
            if not isinstance(configuration, dict):
                configuration = {}
            self.email_enabled.setChecked(bool(configuration.get("enabled", _DEFAULT_EMAIL_ENABLED)))
            self._set_switch_text(self.email_enabled, self.email_enabled.isChecked())
            self.close_mumu_after_run.setChecked(
                bool(self.settings.get("close_mumu_after_run", _DEFAULT_CLOSE_MUMU_AFTER_RUN))
            )
            self._set_switch_text(self.close_mumu_after_run, self.close_mumu_after_run.isChecked())
            self.close_mumu_app_after_run.setChecked(
                bool(self.settings.get("close_mumu_app_after_run", _DEFAULT_CLOSE_MUMU_APP_AFTER_RUN))
            )
            self._set_switch_text(
                self.close_mumu_app_after_run,
                self.close_mumu_app_after_run.isChecked(),
            )
            stored_execution_counts = self.settings.get("task_execution_counts", {})
            if not isinstance(stored_execution_counts, dict):
                stored_execution_counts = {}
            for task in self.tasks:
                value = stored_execution_counts.get(task.id, _DEFAULT_TASK_EXECUTION_COUNT)
                self._task_execution_combos[task.id].setValue(self._clamped_int(value, 1, 0, 10))
            self.max_log_files_edit.setText(str(self.settings.get("max_log_files", _DEFAULT_MAX_LOG_FILES)))
            self.max_screenshot_files_edit.setText(
                str(self.settings.get("max_screenshot_files", _DEFAULT_MAX_SCREENSHOT_FILES))
            )
            cleanup_index = self.cleanup_mode_combo.findData(
                self.settings.get("cleanup_mode", _DEFAULT_CLEANUP_MODE)
            )
            self.cleanup_mode_combo.setCurrentIndex(max(0, cleanup_index))
            source_index = self.download_source_combo.findData(
                str(self.settings.get("ocr_download_source", AUTO_SOURCE))
            )
            self.download_source_combo.setCurrentIndex(max(0, source_index))
            self.mumu_directory_edit.setText(
                str(self.settings.get("mumu_directory", _DEFAULT_MUMU_DIRECTORY) or "")
            )
            self.qq_group_name_edit.setText(str(self.settings.get("qq_group_name", _DEFAULT_QQ_GROUP_NAME)))
            self._refresh_mumu_instances()
            self.smtp_host.setText(str(configuration.get("smtp_host", _DEFAULT_SMTP_HOST)))
            self.smtp_port.setText(str(configuration.get("smtp_port", _DEFAULT_SMTP_PORT)))
            security = str(configuration.get("smtp_security", _DEFAULT_SMTP_SECURITY)).lower()
            security_index = self.smtp_security.findData(security)
            self.smtp_security.setCurrentIndex(max(0, security_index))
            self.smtp_username.setText(str(configuration.get("smtp_username", _DEFAULT_SMTP_USERNAME)))
            self.smtp_password.setText(str(configuration.get("smtp_password", _DEFAULT_SMTP_PASSWORD)))
            recipients = configuration.get("recipients", [])
            if isinstance(recipients, list):
                recipients = ", ".join(str(item) for item in recipients)
            self.recipients.setText(str(recipients))
            self.subject_prefix.setText(str(configuration.get("subject_prefix", _DEFAULT_SUBJECT_PREFIX)))
            self.smtp_timeout_seconds.setText(str(configuration.get("smtp_timeout_seconds", _DEFAULT_SMTP_TIMEOUT_SECONDS)))
        finally:
            self._loading_settings = False

    def _sync_security_port(self, _index: int) -> None:
        """Fill the standard port when switching security and the port is untouched."""

        if self._loading_settings:
            return
        security = self.smtp_security.currentData()
        default_port = {"ssl": 465, "starttls": 587}.get(security, 587)
        current = self.smtp_port.text().strip()
        if current in {"", "465", "587"}:
            self.smtp_port.setText(str(default_port))

    def _has_unsaved_changes(self) -> bool:
        """Compare every editable setting against the loaded configuration."""

        configuration = self.settings.get("email_notification", {})
        if not isinstance(configuration, dict):
            configuration = {}
        if self.email_enabled.isChecked() != bool(configuration.get("enabled", _DEFAULT_EMAIL_ENABLED)):
            return True
        if self.smtp_host.text().strip() != str(configuration.get("smtp_host", _DEFAULT_SMTP_HOST)):
            return True
        if self._clamped_int(self.smtp_port.text(), _DEFAULT_SMTP_PORT, 1, 65535) != int(
            configuration.get("smtp_port", _DEFAULT_SMTP_PORT)
        ):
            return True
        if (
            self.smtp_security.currentData()
            != str(configuration.get("smtp_security", _DEFAULT_SMTP_SECURITY)).lower()
        ):
            return True
        if (
            self.smtp_username.text().strip()
            != str(configuration.get("smtp_username", _DEFAULT_SMTP_USERNAME))
        ):
            return True
        if self.smtp_password.text() != str(configuration.get("smtp_password", _DEFAULT_SMTP_PASSWORD)):
            return True

        stored_recipients = configuration.get("recipients", [])
        if isinstance(stored_recipients, str):
            stored_recipients = stored_recipients.replace(";", ",").split(",")
        stored_recipients = [
            str(item).strip() for item in stored_recipients if str(item).strip()
        ]
        current_recipients = [
            value.strip()
            for value in self.recipients.text().replace(";", ",").split(",")
            if value.strip()
        ]
        if current_recipients != stored_recipients:
            return True
        if (self.subject_prefix.text().strip() or "FREE") != str(
            configuration.get("subject_prefix", _DEFAULT_SUBJECT_PREFIX)
        ):
            return True
        if self._clamped_int(self.smtp_timeout_seconds.text(), _DEFAULT_SMTP_TIMEOUT_SECONDS, 1, 120) != int(
            configuration.get("smtp_timeout_seconds", _DEFAULT_SMTP_TIMEOUT_SECONDS)
        ):
            return True
        if self.close_mumu_after_run.isChecked() != bool(
            self.settings.get("close_mumu_after_run", _DEFAULT_CLOSE_MUMU_AFTER_RUN)
        ):
            return True
        if self.close_mumu_app_after_run.isChecked() != bool(
            self.settings.get("close_mumu_app_after_run", _DEFAULT_CLOSE_MUMU_APP_AFTER_RUN)
        ):
            return True
        stored_execution_counts = self.settings.get("task_execution_counts", {})
        if not isinstance(stored_execution_counts, dict):
            stored_execution_counts = {}
        for task in self.tasks:
            expected = self._clamped_int(
                stored_execution_counts.get(task.id, _DEFAULT_TASK_EXECUTION_COUNT), 1, 0, 10
            )
            if self._task_execution_combos[task.id].value() != expected:
                return True
        if self._clamped_int(self.max_log_files_edit.text(), _DEFAULT_MAX_LOG_FILES, -1, 1000) != int(
            self.settings.get("max_log_files", _DEFAULT_MAX_LOG_FILES)
        ):
            return True
        if self._clamped_int(
            self.max_screenshot_files_edit.text(), 0, -1, 1000
        ) != int(self.settings.get("max_screenshot_files", _DEFAULT_MAX_SCREENSHOT_FILES)):
            return True
        if self.cleanup_mode_combo.currentData() != str(
            self.settings.get("cleanup_mode", _DEFAULT_CLEANUP_MODE)
        ):
            return True
        if self.mumu_directory_edit.text().strip() != str(
            self.settings.get("mumu_directory", _DEFAULT_MUMU_DIRECTORY) or ""
        ):
            return True
        if self.qq_group_name_edit.text().strip() != str(
            self.settings.get("qq_group_name", _DEFAULT_QQ_GROUP_NAME)
        ):
            return True
        try:
            current_vmindex = int(self.mumu_vm_index_combo.currentData())
        except (TypeError, ValueError):
            current_vmindex = 0
        if current_vmindex != int(self.settings.get("mumu_vm_index", _DEFAULT_MUMU_VM_INDEX)):
            return True
        if self._selected_model("det") != str(self.settings.get("ocr_det_model", _DEFAULT_DET_MODEL)):
            return True
        if self._selected_model("rec") != str(self.settings.get("ocr_rec_model", _DEFAULT_REC_MODEL)):
            return True
        if self.download_source_combo.currentData() != str(
            self.settings.get("ocr_download_source", AUTO_SOURCE)
        ):
            return True
        return False

    def _confirm_discard(self) -> bool:
        return confirm_dialog(self, "未保存的更改", "设置尚未保存，确定放弃更改吗？")

    def reject(self) -> None:
        if self._has_unsaved_changes() and not self._confirm_discard():
            return
        self._discard_confirmed = True
        super().reject()

    @Slot()
    def _browse_mumu_directory(self) -> None:
        initial = self.mumu_directory_edit.text().strip() or r"D:\APP\MuMu Player 12"
        directory = QFileDialog.getExistingDirectory(self, "选择 MuMu 安装目录", initial)
        if directory:
            self.mumu_directory_edit.setText(directory)
            self._refresh_mumu_instances()

    @Slot()
    def _refresh_mumu_instances(self) -> None:
        """Query the current MuMu CLI for player instances and repopulate the combo."""

        selected_index = self.mumu_vm_index_combo.currentData()
        self.mumu_vm_index_combo.clear()
        try:
            controller = MuMuController(self._cli_path_for_refresh(), command_timeout=10)
            instances = controller.list_instances()
        except (MuMuError, OSError, TypeError, ValueError):
            instances = {}

        if instances:
            for index in sorted(instances):
                name = instances[index]
                label = f"#{index} {name}" if name != str(index) else f"#{index}"
                self.mumu_vm_index_combo.addItem(label, index)
        else:
            for index in range(10):
                self.mumu_vm_index_combo.addItem(f"默认 #{index}", index)

        preferred = (
            int(self.settings.get("mumu_vm_index", 0))
            if selected_index is None
            else int(selected_index)
        )
        found = self.mumu_vm_index_combo.findData(preferred)
        self.mumu_vm_index_combo.setCurrentIndex(max(0, found))

    def _cli_path_for_refresh(self) -> Path:
        folder_value = self.mumu_directory_edit.text().strip()
        if folder_value:
            folder = Path(folder_value)
            candidates = [
                folder / "nx_main" / "mumu-cli.exe",
                folder / "mumu-cli.exe",
            ]
            existing = next((path for path in candidates if path.exists()), None)
            if existing:
                return existing
        return mumu_cli_path(self.settings)

    @staticmethod
    def _set_switch_text(switch: QPushButton, enabled: bool) -> None:
        switch.setText("开" if enabled else "关")

    def _collect_settings(self) -> dict[str, Any]:
        recipients = [
            value.strip()
            for value in self.recipients.text().replace(";", ",").split(",")
            if value.strip()
        ]
        task_execution_counts = {
            task_id: combo.value() for task_id, combo in self._task_execution_combos.items()
        }
        return {
            "close_mumu_after_run": self.close_mumu_after_run.isChecked(),
            "close_mumu_app_after_run": self.close_mumu_app_after_run.isChecked(),
            "task_execution_counts": task_execution_counts,
            "max_log_files": self._clamped_int(
                self.max_log_files_edit.text(), _DEFAULT_MAX_LOG_FILES, -1, 1000
            ),
            "max_screenshot_files": self._clamped_int(
                self.max_screenshot_files_edit.text(), _DEFAULT_MAX_SCREENSHOT_FILES, -1, 1000
            ),
            "cleanup_mode": self.cleanup_mode_combo.currentData(),
            "mumu_directory": self.mumu_directory_edit.text().strip(),
            "qq_group_name": self.qq_group_name_edit.text().strip(),
            "mumu_vm_index": int(
                0
                if self.mumu_vm_index_combo.currentData() is None
                else self.mumu_vm_index_combo.currentData()
            ),
            "ocr_det_model": self._selected_model("det"),
            "ocr_rec_model": self._selected_model("rec"),
            "ocr_download_source": self.download_source_combo.currentData(),
            "email_notification": {
                "enabled": self.email_enabled.isChecked(),
                "smtp_host": self.smtp_host.text().strip(),
                "smtp_port": self._clamped_int(self.smtp_port.text(), _DEFAULT_SMTP_PORT, 1, 65535),
                "smtp_security": self.smtp_security.currentData(),
                "smtp_username": self.smtp_username.text().strip(),
                "smtp_password": self.smtp_password.text(),
                "recipients": recipients,
                "subject_prefix": self.subject_prefix.text().strip() or _DEFAULT_SUBJECT_PREFIX,
                "notify_on": ["success", "failed", "stopped"],
                "smtp_timeout_seconds": self._clamped_int(
                    self.smtp_timeout_seconds.text(), _DEFAULT_SMTP_TIMEOUT_SECONDS, 1, 120
                ),
            }
        }

    @staticmethod
    def _clamped_int(value: object, default: int, low: int, high: int) -> int:
        """Parse ``value`` (a field text or a scalar) into an int, clamped to interval."""
        try:
            text = value.strip() if isinstance(value, str) else value
            parsed = int(float(text)) if isinstance(text, (int, float, str)) else 0
        except (TypeError, ValueError):
            return default
        return min(high, max(low, parsed))

    def _clear_output_files(self, output_type: str) -> None:
        """Clear all files in logs or screenshots using the current cleanup mode."""

        mode = str(self.cleanup_mode_combo.currentData())
        mode_label = "删除至回收站" if mode == "recycle" else "永久删除"
        if output_type == "logs":
            directory = resolve_path(self.settings.get("log_directory"), self.base_directory)
            title = "清理全部日志"
        else:
            directory = resolve_path(self.settings.get("screenshot_directory"), self.base_directory)
            title = "清理全部截图"
        if not confirm_dialog(
            self,
            title,
            f"将按“{mode_label}”方式清理 {directory} 下的全部文件，是否继续？",
        ):
            return
        removed = clear_output_files(directory, mode)
        QMessageBox.information(self, title, f"已清理 {removed} 个文件。")

    def _send_test_email(self) -> None:
        settings = self.settings.copy()
        settings.update(self._collect_settings())
        task = TaskDefinition("smtp_test", "FREE SMTP 测试", "local.test", ())
        result = RunResult("smtp_test", RunStatus.SUCCESS, 1, 1)
        logs: list[str] = []
        self.test_button.setEnabled(False)
        try:
            sent = send_run_notification(settings, result, [task], logs.append)
        finally:
            self.test_button.setEnabled(True)
        if sent:
            QMessageBox.information(self, "发送成功", "测试邮件已提交到 SMTP 服务器。")
        else:
            QMessageBox.warning(self, "发送失败", "\n".join(logs) or "邮件配置不完整。")

    def _save(self) -> None:
        collected = self._collect_settings()
        try:
            if self.settings_path.exists():
                main_settings = load_json(self.settings_path)
                if not isinstance(main_settings, dict):
                    raise ValueError("配置文件必须是对象")
            else:
                main_settings = {}
            main_settings = _sanitize_settings(main_settings)
            main_settings["email_notification"] = collected["email_notification"]
            main_settings["close_mumu_after_run"] = collected["close_mumu_after_run"]
            main_settings["close_mumu_app_after_run"] = collected["close_mumu_app_after_run"]
            main_settings["task_execution_counts"] = collected["task_execution_counts"]
            main_settings["max_log_files"] = collected["max_log_files"]
            main_settings["max_screenshot_files"] = collected["max_screenshot_files"]
            main_settings["cleanup_mode"] = collected["cleanup_mode"]
            main_settings["mumu_directory"] = collected["mumu_directory"]
            main_settings["qq_group_name"] = collected["qq_group_name"]
            main_settings["mumu_vm_index"] = collected["mumu_vm_index"]
            main_settings["ocr_det_model"] = collected["ocr_det_model"]
            main_settings["ocr_rec_model"] = collected["ocr_rec_model"]
            main_settings["ocr_download_source"] = collected["ocr_download_source"]

            folder_value = collected["mumu_directory"]
            if folder_value:
                folder = Path(folder_value)
                adb_candidates = [
                    folder / "nx_main" / "adb.exe",
                    folder / "shell" / "adb.exe",
                    folder / "adb.exe",
                ]
                cli_candidates = [
                    folder / "nx_main" / "mumu-cli.exe",
                    folder / "mumu-cli.exe",
                ]
                found_adb = next((path for path in adb_candidates if path.exists()), None)
                found_cli = next((path for path in cli_candidates if path.exists()), None)
                if found_adb or found_cli:
                    if found_adb:
                        main_settings["adb_path"] = str(found_adb)
                    if found_cli:
                        main_settings["mumu_cli_path"] = str(found_cli)
                else:
                    QMessageBox.warning(
                        self,
                        "未找到 MuMu 程序",
                        "所选文件夹下未找到 adb.exe / mumu-cli.exe，已保留原有程序路径。",
                    )
            save_settings(self.settings_path, main_settings)
            self.settings = load_settings(self.settings_path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()

    @staticmethod
    def _style_sheet() -> str:
        return (
            "\n"
            "            QDialog { background: #f2f6f4; color: #193331; }\n"
            "            QFrame#settingsNavPanel {\n"
            "                background: #e8f0ed;\n"
            "                border: 1px solid #d1e0db;\n"
            "                border-radius: 10px;\n"
            "            }\n"
            "            QListWidget#settingsNav {\n"
            "                background: transparent;\n"
            "                border: none;\n"
            "                outline: none;\n"
            "                padding: 0;\n"
            "            }\n"
            "            QListWidget#settingsNav::item {\n"
            "                min-height: 44px;\n"
            "                border: 1px solid transparent;\n"
            "                border-radius: 7px;\n"
            "                padding: 0 10px;\n"
            "                color: #48615f;\n"
            "                font-weight: 600;\n"
            "            }\n"
            "            QListWidget#settingsNav::item:hover {\n"
            "                background: #dcebe6;\n"
            "                color: #146e65;\n"
            "            }\n"
            "            QListWidget#settingsNav::item:selected {\n"
            "                background: #d4e9e3;\n"
            "                color: #0c6e63;\n"
            "                font-weight: 700;\n"
            "                outline: none;\n"
            "            }\n"
            "            QStackedWidget#settingsStack {\n"
            "                background: transparent;\n"
            "                border: none;\n"
            "            }\n"
            "            QFrame#settingsTabPage { background: transparent; }\n"
            "            QListWidget#settingsTaskList {\n"
            "                background: #fbfdfc;\n"
            "                border: 1px solid #dbe7e2;\n"
            "                border-radius: 6px;\n"
            "                padding: 4px;\n"
            "            }\n"
            "            QListWidget#settingsTaskList::item {\n"
            "                padding: 5px 8px;\n"
            "                border-radius: 4px;\n"
            "            }\n"
            "            QListWidget#settingsTaskList::item:selected {\n"
            "                background: #d4e9e3;\n"
            "                color: #0c6e63;\n"
            "                outline: none;\n"
            "            }\n"
            "            QFrame#settingsOptionCard {\n"
            "                background: #ffffff;\n"
            "                border: 1px solid #cbdcd6;\n"
            "                border-radius: 9px;\n"
            "            }\n"
            "            QFrame#settingsModelRow {\n"
            "                background: #ffffff;\n"
            "                border: 1px solid #cbdcd6;\n"
            "                border-radius: 8px;\n"
            "            }\n"
            "            QFrame#settingsModelRow:hover {\n"
            "                background: #f5faf8;\n"
            "                border-color: #91c5b9;\n"
            "            }\n"
            "            QFrame#settingsRetryRow {\n"
            "                background: #f8fbfa;\n"
            "                border: 1px solid #e0ebe7;\n"
            "                border-radius: 7px;\n"
            "            }\n"
            "            QFrame#settingsRetryRow:hover {\n"
            "                background: #f1f8f5;\n"
            "                border-color: #a9d2c7;\n"
            "            }\n"
            "            QScrollArea#settingsScroll {\n"
            "                background: transparent;\n"
            "                border: none;\n"
            "            }\n"
            "            QLabel#settingsSectionTitle {\n"
            "                color: #193331;\n"
            "                font-size: 18px;\n"
            "                font-weight: 750;\n"
            "            }\n"
            "            QLabel#settingsSectionSubtitle {\n"
            "                color: #758986;\n"
            "                font-size: 12px;\n"
            "            }\n"
            "            QLabel#settingsSectionNote {\n"
            "                color: #758986;\n"
            "                font-size: 11px;\n"
            "            }\n"
            "            QLabel#settingsCardTitle {\n"
            "                color: #244340;\n"
            "                font-size: 14px;\n"
            "                font-weight: 700;\n"
            "            }\n"
            "            QLabel#settingsGroupTitle {\n"
            "                color: #244340;\n"
            "                font-size: 13px;\n"
            "                font-weight: 700;\n"
            "            }\n"
            "            QLabel#settingsRunLabel {\n"
            "                color: #193331;\n"
            "                font-size: 13px;\n"
            "                font-weight: 600;\n"
            "            }\n"
            "            QLabel#settingsRetryId {\n"
            "                color: #718783;\n"
            "                font-size: 12px;\n"
            "                min-width: 100px;\n"
            "            }\n"
            "            QFormLayout QLabel {\n"
            "                color: #49615f;\n"
            "                font-size: 13px;\n"
            "            }\n"
            "            QFrame#settingsFooter {\n"
            "                border-top: 1px solid #cbdcd6;\n"
            "            }\n"
            "            QPushButton#settingsSwitch {\n"
            "                min-width: 72px;\n"
            "                max-width: 72px;\n"
            "                min-height: 32px;\n"
            "                max-height: 32px;\n"
            "                padding: 0;\n"
            "                color: #48615f;\n"
            "                background: #edf2f0;\n"
            "                border-color: #c5d5d0;\n"
            "                border-radius: 16px;\n"
            "                font-size: 11px;\n"
            "                font-weight: 800;\n"
            "            }\n"
            "            QPushButton#settingsSwitch:hover {\n"
            "                background: #e1eeea;\n"
            "                border-color: #88bdb1;\n"
            "            }\n"
            "            QPushButton#settingsSwitch:checked {\n"
            "                color: #ffffff;\n"
            "                background: #137f73;\n"
            "                border-color: #137f73;\n"
            "            }\n"
            "            QPushButton#settingsSwitch:checked:hover {\n"
            "                background: #0e6e64;\n"
            "                border-color: #0e6e64;\n"
            "            }\n"
            "            QRadioButton#settingsModelRadio {\n"
            "                color: #31504d;\n"
            "                font-size: 13px;\n"
            "                spacing: 6px;\n"
            "            }\n"
            "            QRadioButton#settingsModelRadio::indicator {\n"
            "                width: 15px;\n"
            "                height: 15px;\n"
            "                border: 1px solid #9bb9b1;\n"
            "                border-radius: 8px;\n"
            "                background: #ffffff;\n"
            "            }\n"
            "            QRadioButton#settingsModelRadio::indicator:hover {\n"
            "                border-color: #2b9b8b;\n"
            "            }\n"
            "            QRadioButton#settingsModelRadio::indicator:checked {\n"
            "                border: 1px solid #138277;\n"
            "                border-radius: 8px;\n"
            "                background: #138277;\n"
            "            }\n"
            "            QLabel#settingsOcrFeedback { color: #6e8580; font-size: 12px; }\n"
            "            QLabel#settingsModelSize { color: #718783; font-size: 12px; min-width: 62px; }\n"
            "            QPushButton#settingsModelAction {\n"
            "                min-height: 32px;\n"
            "                min-width: 76px;\n"
            "                padding: 0 13px;\n"
            "            }\n"
            "            QPushButton#settingsModelAction:disabled {\n"
            "                color: #9db3af;\n"
            "                background: #f0f5f3;\n"
            "                border-color: #dbe8e4;\n"
            "            }\n"
            + _s.MESSAGE_BOX_QSS
            + _s.SCROLLBAR_QSS
            + _s.COMMON_CONTROLS_QSS
            + "        "
        )

"""FREE 界面样式总览 Demo。

把项目 `free_app` 里定义的全部 UI 样式集中到一个可滚动的展示窗口里，
每一项都带编号、来源位置与 `objectName`，便于对照代码。

样式来源（全部复用项目自身定义，不在此处重写）：

- ``ui_tokens.py``   : 设计令牌（颜色/圆角/字号/字重/按钮角色）
- ``ui_common.py``   : 公共 QSS 片段、按钮工厂、尺寸常量、共享控件
- ``ui_main.py``     : 主页 QSS（品牌/设备徽标/任务列表/日志区、执行态）
- ``ui_settings.py`` : 设置页 QSS（导航/卡片/模型行/今日完成状态）
- ``ui_task_manager.py`` : 任务管理页 QSS（面板/分栏/Tab/Toast/运行视图）

运行（两种方式都可以）：

    python demo/styles.py
    python -m demo.styles
"""

from __future__ import annotations

import sys
from pathlib import Path

# 本文件位于 <仓库根>/demo/ 下，而 free_app 在仓库根。
# 直接把脚本当文件运行时，Python 只把「脚本所在目录」放进 sys.path[0]，
# 因此这里显式把仓库根补进去；已存在则跳过（`python -m demo.styles`
# 与从仓库根导入本模块的场景下，仓库根本来就在 sys.path 里）。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from free_app import ui_tokens as _t
from free_app.ui_common import (
    BACK_BUTTON_WIDTH,
    MAIN_ACTION_BUTTON_WIDTH,
    MAIN_ICON_BUTTON_WIDTH,
    MESSAGE_BOX_QSS,
    OUTLINE_COMBO_WIDTH,
    PAGE_BASE_QSS,
    ROW_BUTTON_HEIGHT,
    TOOL_BUTTON_WIDTH,
    WIDE_BUTTON_MIN_WIDTH,
    ElidedLabel,
    ExecutionCountComboBox,
    SettingsComboBox,
    back_button,
    card_title,
    danger_button,
    dialog_action_row,
    outline_button,
    page_title,
    primary_button,
    run_button,
    secondary_button,
    stop_button,
)
from free_app.ui_tokens import (
    BUTTON_DANGER,
    BUTTON_NEUTRAL,
    BUTTON_PRIMARY,
    BUTTON_RUN,
    BUTTON_SECONDARY,
    BUTTON_STOP,
)
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# 仓库根与截图目录（截图目录在仓库根下，不随本文件放在 demo/ 里）。
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"


# --------------------------------------------------------------------- 字体

def load_chinese_fonts() -> str:
    """加载系统中文字体，返回可用的字体族名。

    offscreen 平台下 Qt 默认字体库为空，中文会渲染成方框；这里显式从
    ``C:\\Windows\\Fonts`` 注册字体，让截图与真实运行一致。
    """

    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ):
        if Path(path).exists():
            QFontDatabase.addApplicationFont(path)
    families = QFontDatabase.families()
    for candidate in ("Microsoft YaHei", "SimHei", "SimSun"):
        if candidate in families:
            return candidate
    return ""


# --------------------------------------------------------------- 展示骨架

class _DemoPage(QWidget):
    """一个展示页：把若干编号条目纵向排布在可滚动区域里。"""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("appRoot")
        self._index = 0
        self._build(title, subtitle)

    def _build(self, title: str, subtitle: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(14)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(page_title(title))
        sub = QLabel(subtitle)
        sub.setObjectName("demoPageSubtitle")
        heading.addWidget(sub)
        root.addLayout(heading)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self.items = QVBoxLayout(content)
        self.items.setContentsMargins(0, 0, 12, 0)
        self.items.setSpacing(12)
        scroll.setWidget(content)
        scroll.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        root.addWidget(scroll, 1)

    def add_item(
        self,
        title: str,
        *,
        source: str,
        object_name: str = "—",
        note: str = "",
        body: QWidget | None = None,
    ) -> int:
        """追加一个编号条目，返回其序号（从 1 开始）。"""

        self._index += 1
        number = self._index

        card = QFrame()
        card.setObjectName("demoCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel(f"{number:02d}")
        badge.setObjectName("demoBadge")
        badge.setFixedWidth(38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(badge)

        name = QLabel(title)
        name.setObjectName("demoItemTitle")
        head.addWidget(name)
        head.addStretch(1)
        layout.addLayout(head)

        meta = QLabel(f"objectName: {object_name}   ·   {source}")
        meta.setObjectName("demoMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        if note:
            note_label = QLabel(note)
            note_label.setObjectName("demoNote")
            note_label.setWordWrap(True)
            layout.addWidget(note_label)

        if body is not None:
            layout.addWidget(body)
        self.items.addWidget(card)
        return number

    def add_note(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("demoSectionNote")
        label.setWordWrap(True)
        self.items.addWidget(label)

    def add_section(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("demoSectionTitle")
        self.items.addWidget(label)

    def finish(self) -> None:
        self.items.addStretch(1)


def _row(*widgets: QWidget, spacing: int = 10, align_left: bool = True) -> QWidget:
    """把若干控件放进一行，左侧对齐。"""

    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    if align_left:
        layout.addStretch(1)
    return holder


def _swatch(color: str, label: str) -> QWidget:
    """一个颜色样本块。"""

    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    chip = QLabel()
    chip.setFixedSize(22, 22)
    chip.setStyleSheet(
        f"background: {color}; border: 1px solid #cbdcd6; border-radius: 4px;"
    )
    text = QLabel(f"{label}  {color}")
    text.setObjectName("demoSwatchLabel")
    layout.addWidget(chip)
    layout.addWidget(text)
    layout.addStretch(1)
    return holder


# ------------------------------------------------------------ 各分类构建

def build_buttons_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "① 按钮样式",
        "全部 QPushButton 都经 ui_common 的工厂创建。角色只有六个，"
        "共用同一套几何（高度/圆角/内边距/字重），差异只在配色。",
    )
    page.add_section("六个语义角色（ui_tokens.BUTTON_*）")

    page.add_item(
        "secondaryButton — 次要操作",
        source="ui_tokens.BUTTON_SECONDARY · secondary_button()",
        object_name=BUTTON_SECONDARY,
        note="最常用的默认外观（浅绿）。表单行内的“浏览/刷新/测试”等也用同一角色，"
        "只是经 outline_button() 构造以表达“表单内按钮”的意图。"
        f"默认宽度 {TOOL_BUTTON_WIDTH}px，主页动作栏用 {MAIN_ACTION_BUTTON_WIDTH}px。",
        body=_row(
            secondary_button("任务管理", width=MAIN_ACTION_BUTTON_WIDTH),
            secondary_button("执行全部", width=MAIN_ACTION_BUTTON_WIDTH),
            secondary_button("执行", width=MAIN_ACTION_BUTTON_WIDTH),
            secondary_button("刷新", width=MAIN_ACTION_BUTTON_WIDTH),
        ),
    )
    page.add_item(
        "primaryButton — 主操作",
        source="ui_tokens.BUTTON_PRIMARY · primary_button()",
        object_name=BUTTON_PRIMARY,
        note="绿色渐变 + 白字，自动设为对话框默认按钮。"
        "QSS 里把 :default 也纳入同一角色，因此消息框的默认按钮自动一致。",
        body=_row(primary_button("保存"), primary_button("确定")),
    )
    page.add_item(
        "outlineButton — 表单内次要按钮",
        source="ui_common.outline_button()",
        object_name=f"{BUTTON_SECONDARY}（同角色）",
        note=f"与 secondaryButton 同角色、同几何；minimum_width 兜底较长中文"
        f"（WIDE_BUTTON_MIN_WIDTH={WIDE_BUTTON_MIN_WIDTH}px）。",
        body=_row(
            outline_button("浏览"),
            outline_button("刷新实例"),
            outline_button("测试识别"),
            outline_button("发送测试邮件", minimum_width=WIDE_BUTTON_MIN_WIDTH),
        ),
    )
    page.add_item(
        "neutralButton — 中性/退出",
        source="ui_tokens.BUTTON_NEUTRAL · back_button()",
        object_name=BUTTON_NEUTRAL,
        note=f"浅灰底，用于「返回 / 取消」这类退出语义。"
        f"固定宽 BACK_BUTTON_WIDTH={BACK_BUTTON_WIDTH}px。",
        body=_row(
            back_button(),
            outline_button("取消", object_name=BUTTON_NEUTRAL),
        ),
    )
    page.add_item(
        "dangerButton — 不可逆操作",
        source="ui_tokens.BUTTON_DANGER · danger_button()",
        object_name=BUTTON_DANGER,
        note="红底红字。模型行的「删除」也复用同一角色"
        "（此前是 settingsModelAction 的第二个外观，已合并）。",
        body=_row(
            danger_button("清理全部日志"),
            danger_button("清理全部截图"),
            danger_button("重置全部完成时间"),
        ),
    )
    page.add_item(
        "runButton — 运行/试运行",
        source="ui_tokens.BUTTON_RUN · run_button()",
        object_name=BUTTON_RUN,
        note="全项目唯一的蓝色按钮，用于与保存类操作区分。",
        body=_row(run_button("运行"), run_button("试运行")),
    )
    page.add_item(
        "stopButton — 停止",
        source="ui_tokens.BUTTON_STOP · stop_button()",
        object_name=BUTTON_STOP,
        note="红色渐变 + 白字；禁用态自动回到灰底。"
        "主页「停止」与调试视图「停止」曾各写一套渐变，现已合并。",
        body=_row(
            stop_button("停止"),
            _disabled(stop_button("停止（禁用）")),
        ),
    )
    page.add_item(
        "可切换的次要按钮（选中态）",
        source="UNIFIED_BUTTONS_QSS :checked",
        object_name=f"{BUTTON_SECONDARY}[checked]",
        note="checkable 的次要按钮选中时整块变实心绿白字；"
        "无需为「显示坐标」单独建 objectName。",
        body=_row(
            _checkable_button("显示坐标"),
            _checkable_button("显示坐标（已选中）", checked=True),
        ),
    )
    page.add_item(
        "禁用态",
        source="UNIFIED_BUTTONS_QSS :disabled",
        note="统一的灰底灰字 + 弱边框，对所有角色一致。",
        body=_row(
            _disabled(secondary_button("禁用的次要按钮")),
            _disabled(primary_button("禁用的主按钮")),
            _disabled(danger_button("禁用的危险按钮")),
        ),
    )
    page.finish()
    return page


def _disabled(button: QPushButton) -> QPushButton:
    button.setEnabled(False)
    return button


def _checkable_button(text: str, *, checked: bool = False) -> QPushButton:
    button = secondary_button(text)
    button.setCheckable(True)
    button.setChecked(checked)
    return button


def build_inputs_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "② 表单控件",
        "输入框、下拉框、复选框、单选按钮——全部在 ui_common 统一定义，"
        "各页面直接复用。",
    )
    page.add_section("文本与数字输入")

    edit = QLineEdit("普通输入框")
    edit.setPlaceholderText("占位提示文本")
    area = QTextEdit("多行文本编辑框\n第二行")
    area.setFixedHeight(74)
    plain = QPlainTextEdit("QPlainTextEdit 纯文本区")
    plain.setFixedHeight(74)

    page.add_item(
        "QLineEdit / QSpinBox / QDoubleSpinBox",
        source="ui_common.UNIFIED_FORM_CONTROLS_QSS",
        note="高 40px、圆角 8、边框 #cbdcd6；hover 变 #83b9ad，"
        "focus 换成 2px #2b9b8b。",
        body=_row(edit, QLineEdit("聚焦试试"), edit_placeholder := QLineEdit()),
    )
    edit_placeholder.setPlaceholderText("我是占位提示")
    page.add_item(
        "QTextEdit / QPlainTextEdit",
        source="ui_common.UNIFIED_FORM_CONTROLS_QSS",
        note="与单行输入同一套边框配色，内边距 8×12。",
        body=_row(area, plain),
    )

    page.add_section("下拉框")
    combo = SettingsComboBox()
    combo.addItems(["选项 A", "选项 B", "选项 C"])
    combo.setFixedWidth(OUTLINE_COMBO_WIDTH)
    wide_combo = SettingsComboBox()
    wide_combo.addItems(["SSL", "STARTTLS"])
    wide_combo.setFixedWidth(200)

    page.add_item(
        "SettingsComboBox — 统一下拉框",
        source="ui_common.UNIFIED_COMBO_QSS + 自绘箭头/自绘弹层",
        object_name="（无 objectName，按类选择器）",
        note="刻意隐藏 Qt 原生箭头（down-arrow 宽高 0）并自绘 V 形箭头，"
        "因此各平台外观一致；下拉弹层是自绘窗口，见 ⑨。滚轮悬停不会误切换选项。",
        body=_row(combo, wide_combo),
    )

    page.add_section("勾选与单选")
    checks = _row(
        QCheckBox("普通复选框"),
        _checked(QCheckBox("已勾选复选框")),
    )
    radio_holder = QWidget()
    radio_layout = QHBoxLayout(radio_holder)
    radio_layout.setContentsMargins(0, 0, 0, 0)
    radio_layout.setSpacing(14)
    radio_a = QRadioButton("单选 A")
    radio_a.setObjectName("settingsModelRadio")
    radio_b = QRadioButton("单选 B（选中）")
    radio_b.setObjectName("settingsModelRadio")
    radio_b.setChecked(True)
    radio_layout.addWidget(radio_a)
    radio_layout.addWidget(radio_b)
    radio_layout.addStretch(1)

    page.add_item(
        "QCheckBox — 复选框",
        source="ui_common.UNIFIED_CHECKBOX_QSS",
        note="指示器 18×18、圆角 4；勾选时填充绿色对角渐变（GREEN_DIAG）。"
        "动作参数表单里的布尔参数用的就是这个控件。",
        body=checks,
    )
    page.add_item(
        "QRadioButton — 单选按钮",
        source="ui_settings.SettingsDialog._style_sheet()",
        object_name="settingsModelRadio",
        note="15×15 圆形指示器；选中由边框色变化表示（模型选择卡用）。",
        body=radio_holder,
    )

    page.add_section("任务专用下拉框")
    page.add_item(
        "ExecutionCountComboBox — 执行次数选择",
        source="ui_common.ExecutionCountComboBox",
        note="0–10 次的预设项，value()/setValue() 直接读写整数；"
        "设置页「执行次数」分页每行一个。",
        body=_row(
            _exec_combo(1),
            _exec_combo(5),
        ),
    )
    page.finish()
    return page


def _checked(widget: QCheckBox) -> QCheckBox:
    widget.setChecked(True)
    return widget


def _exec_combo(value: int) -> ExecutionCountComboBox:
    combo = ExecutionCountComboBox()
    combo.setFixedWidth(112)
    combo.setValue(value)
    return combo


def build_labels_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "③ 文字与标签",
        "标题层级、说明文字、状态标签与省略标签。",
    )
    page.add_section("页面与卡片标题")

    brand = QLabel("FREE")
    brand.setObjectName("brandLabel")
    title = QLabel("MuMu 自动化控制台")
    title.setObjectName("titleLabel")
    muted = QLabel("实例 0  ·  1080×1920  ·  480 dpi")
    muted.setObjectName("mutedLabel")

    page.add_item(
        "page_title — 二级页面标题",
        source="ui_common.PAGE_TITLE_QSS",
        object_name="settingsPageTitle",
        note="22px / 750 字重，设置页与任务管理页页头。",
        body=_row(page_title("设置")),
    )
    page.add_item(
        "card_title — 卡片标题",
        source="ui_common.CARD_TITLE_QSS",
        object_name="settingsCardTitle（默认）/ settingsSectionTitle / settingsGroupTitle",
        note="14px / 700。同族还有各页自己定义的 settingsSectionTitle（18px）、"
        "settingsGroupTitle（13px）。",
        body=_row(
            card_title("卡片标题"),
            _named_label("区块标题 18px", "settingsSectionTitle"),
            _named_label("分组标题 13px", "settingsGroupTitle"),
        ),
    )
    page.add_item(
        "主页品牌 / 主标题 / 副标题",
        source="ui_main.MainPage._apply_style()",
        object_name="brandLabel / titleLabel / mutedLabel",
        note="brandLabel 22px 加粗绿（#0b8478）；titleLabel 18px 700；"
        "mutedLabel 11px 灰（#718181）。",
        body=_row(brand, title, muted),
    )

    page.add_section("状态与说明文字")
    page.add_item(
        "deviceBadge — 设备状态徽标（三态）",
        source="ui_main.MainPage._apply_style() · [state=...]",
        object_name="deviceBadge",
        note='属性选择器驱动配色：state="ready" 绿、state="checking" 黄、'
        'state="error" 红；尺寸固定 216×40。',
        body=_row(
            _state_label("MuMu · 127.0.0.1:16384 · 可用", "deviceBadge", "ready"),
            _state_label("设备：检查中…", "deviceBadge", "checking"),
            _state_label("MuMu · 未找到设备", "deviceBadge", "error"),
        ),
    )
    page.add_item(
        "settingsCompletionState — 今日完成状态（三态）",
        source="ui_settings.SettingsDialog._style_sheet() · [state=...]",
        object_name="settingsCompletionState",
        note='state="done" 绿 / state="stale" 橙 / state="pending" 灰。'
        "本次新增的「今日完成」分页用它表示三种完成情况。",
        body=_row(
            _state_label("今日已完成 · 21:32:05", "settingsCompletionState", "done"),
            _state_label("未完成（上次 10-09 21:32）", "settingsCompletionState", "stale"),
            _state_label("今日未完成", "settingsCompletionState", "pending"),
        ),
    )
    page.add_item(
        "OCR 反馈 / 运行进度标签",
        source="ui_common.OCR_FEEDBACK_QSS",
        object_name="settingsOcrFeedback",
        note="12px 灰字 + 浅底圆角块（#f7faf9）。被设置页 OCR 分页与"
        "任务管理页的运行视图共用。",
        body=_row(
            _named_label("已刷新 · 4 个模型已下载", "settingsOcrFeedback"),
            _named_label("进度 3/12", "settingsOcrFeedback"),
        ),
    )
    page.add_item(
        "ElidedLabel — 超长文本省略",
        source="ui_common.ElidedLabel",
        note="用省略号代替硬截断，最小宽度 80px。"
        "「今日完成」分页的长路径说明就用它，避免撑出横向滚动条。",
        body=_row(_named_elided("这是一段很长很长的任务名称，长到需要省略号才不会撑破卡片宽度")),
    )
    page.finish()
    return page


def _named_label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def _state_label(text: str, object_name: str, state: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setProperty("state", state)
    label.setFixedSize(216, 40) if object_name == "deviceBadge" else None
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _named_elided(text: str) -> ElidedLabel:
    label = ElidedLabel(text)
    label.setObjectName("settingsRunLabel")
    label.setFixedWidth(300)
    return label


def build_containers_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "④ 容器与卡片",
        "卡片、面板、分隔线、导航面板、滚动区与页头骨架。",
    )
    page.add_section("卡片与面板")

    option_card = QFrame()
    option_card.setObjectName("settingsOptionCard")
    oc_layout = QVBoxLayout(option_card)
    oc_layout.setContentsMargins(22, 20, 22, 20)
    oc_layout.setSpacing(8)
    oc_layout.addWidget(card_title("白底圆角卡片"))
    oc_layout.addWidget(_plain("settingsOptionCard：白底 + 1px #cbdcd6 + 圆角 9，设置页所有分组用它。"))

    model_row = QFrame()
    model_row.setObjectName("settingsModelRow")
    mr_layout = QHBoxLayout(model_row)
    mr_layout.setContentsMargins(26, 18, 26, 18)
    mr_layout.setSpacing(10)
    radio = QRadioButton()
    radio.setObjectName("settingsModelRadio")
    radio.setChecked(True)
    mr_layout.addWidget(radio)
    mr_layout.addWidget(_named_label("PP-OCRv6_small_det", "settingsRunLabel"), 1)
    mr_layout.addWidget(_named_label("12.4 MB", "settingsModelSize"))
    mr_layout.addWidget(outline_button("删除", object_name=BUTTON_DANGER))

    retry_row = QFrame()
    retry_row.setObjectName("settingsRetryRow")
    rr_layout = QHBoxLayout(retry_row)
    rr_layout.setContentsMargins(16, 12, 14, 12)
    rr_layout.setSpacing(10)
    rr_layout.addWidget(_named_label("哔哩哔哩 · 经验", "settingsRunLabel"), 1)
    rr_layout.addWidget(_named_label("bilibili_exp", "settingsRetryId"))
    exec_combo = ExecutionCountComboBox()
    exec_combo.setFixedWidth(112)
    rr_layout.addWidget(exec_combo)

    surface = QFrame()
    surface.setObjectName("surface")
    s_layout = QVBoxLayout(surface)
    s_layout.setContentsMargins(14, 14, 14, 14)
    s_layout.addWidget(_named_label("主页左侧 surface 面板（白底、圆角 0）", "taskSectionTitle"))

    page.add_item(
        "settingsOptionCard — 设置页分组卡片",
        source="ui_settings.SettingsDialog._style_sheet()",
        object_name="settingsOptionCard",
        note="设置页每个分组的容器；内边距由调用方给（常见 22,20,22,20）。",
        body=option_card,
    )
    page.add_item(
        "settingsModelRow — 模型行卡片（可点选）",
        source="ui_settings.SettingsDialog._style_sheet()",
        object_name="settingsModelRow",
        note="hover 变浅绿底并换边框（#f5faf8 / #91c5b9）。配 ModelRowCard "
        "实现整行可点选。",
        body=model_row,
    )
    page.add_item(
        "settingsRetryRow — 执行次数 / 今日完成行",
        source="ui_settings.SettingsDialog._style_sheet()",
        object_name="settingsRetryRow",
        note="浅底 #f8fbfa + 圆角 7，hover 同样有反馈。"
        "「执行次数」与「今日完成」两个分页共用。",
        body=retry_row,
    )
    page.add_item(
        "surface — 主页左侧任务面板",
        source="ui_main.MainPage._apply_style()",
        object_name="surface",
        note="主页独有：纯白底、直角（radius 0）、1px 边框，固定宽 282px。",
        body=surface,
    )

    page.add_section("导航与骨架")
    page.add_item(
        "settingsNav + settingsNavPanel — 设置页左侧导航",
        source="ui_settings.SettingsDialog._style_sheet()",
        object_name="settingsNavPanel / settingsNav / ::item:selected",
        note="面板宽 176px；选中项实心浅绿 + 绿字 700，hover 有中间态。"
        "这是设置页五个分页的切换入口。",
        body=_nav_demo(),
    )
    page.add_item(
        "settingsTaskList — 列表控件",
        source="ui_settings / ui_task_manager 共用",
        object_name="settingsTaskList",
        note="任务管理页的任务/动作/复合动作列表、步骤列表都用它；"
        "选中项浅绿底绿字。主页的 taskList 是另一套（带 70px 行高与拖拽）。",
        body=_list_demo("settingsTaskList", ["毛怪俱乐部  ·  6 个动作", "小黑盒  ·  12 个动作", "哔哩哔哩 · 经验  ·  13 个动作"]),
    )
    page.add_item(
        "taskList — 主页任务顺序列表",
        source="ui_main.MainPage._apply_style()",
        object_name="taskList / ::item / ::item:selected",
        note="行高 70px（setSizeHint），支持拖拽排序；"
        "选中项绿边框 + 浅绿底。行内文字颜色由任务状态决定（见 ⛃ 状态色）。",
        body=_list_demo("taskList", ["01  毛怪俱乐部\n已完成  ·  6 个动作", "02  小黑盒\n待执行  ·  12 个动作"]),
    )
    page.add_item(
        "分隔线 — 任务管理页分栏",
        source="ui_task_manager.TaskManagerWidget._style_sheet()",
        object_name="taskManagerHLine / taskManagerVLine",
        body=_row(_hline("taskManagerHLine"), _vline("taskManagerVLine")),
    )
    page.add_item(
        "settingsFooter — 对话框底栏",
        source="ui_settings / ui_common.dialog_action_row()",
        object_name="settingsFooter",
        note="只有一条上边框，内部是「取消 / 保存」按钮行（取消走 neutralButton，"
        "保存走 primaryButton 绿色渐变并作为默认按钮）。",
        body=_footer_demo(),
    )
    page.add_item(
        "scrollable_page — 可滚动页面骨架",
        source="ui_common.scrollable_page() + mount_scrollable_page()",
        object_name="settingsTabPage / settingsScroll",
        note="返回 (container, page, scroll, content, content_layout)，"
        "各设置分页统一用它；滚动条为 6px 细样式（见 ⛁）。",
        body=_plain("滚动条样式见「⑧ 滚动条」；这里展示的是容器骨架本身（透明背景）。"),
    )
    page.add_item(
        "taskManagerEditorPanel / LeftPanel / RightPanel — 任务管理三栏",
        source="ui_task_manager.TaskManagerWidget._style_sheet()",
        object_name="taskManagerEditorPanel / LeftPanel / RightPanel",
        note="左栏 280px 列表区、中间编辑器区、右侧（默认隐藏）。",
        body=_panels_demo(),
    )
    page.finish()
    return page


def _plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("demoNote")
    label.setWordWrap(True)
    return label


def _nav_demo() -> QWidget:
    panel = QFrame()
    panel.setObjectName("settingsNavPanel")
    panel.setFixedWidth(176)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(14, 14, 14, 14)
    nav = QListWidget()
    nav.setObjectName("settingsNav")
    nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    nav.addItems(["运行设置", "执行次数", "今日完成", "OCR 模型", "邮件通知"])
    nav.setFrameShape(QFrame.Shape.NoFrame)
    nav.setSpacing(6)
    nav.setUniformItemSizes(True)
    nav.setCurrentRow(2)
    nav.setFixedHeight(230)
    layout.addWidget(nav)
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(panel)
    row.addStretch(1)
    return holder


def _list_demo(object_name: str, items: list[str]) -> QWidget:
    widget = QListWidget()
    widget.setObjectName(object_name)
    widget.setSpacing(7)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if object_name == "taskList":
        for text in items:
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 70))
            widget.addItem(item)
        widget.setCurrentRow(0)
    else:
        widget.addItems(items)
        widget.setCurrentRow(1)
    widget.setFixedHeight(160)
    return widget


def _hline(object_name: str) -> QFrame:
    line = QFrame()
    line.setObjectName(object_name)
    line.setFixedHeight(2)
    line.setFixedWidth(160)
    return line


def _vline(object_name: str) -> QFrame:
    line = QFrame()
    line.setObjectName(object_name)
    line.setFixedWidth(2)
    line.setFixedHeight(60)
    return line


def _footer_demo() -> QWidget:
    footer = QFrame()
    footer.setObjectName("settingsFooter")
    dialog_action_row(footer, lambda: None, lambda: None)
    return footer


def _panels_demo() -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    left = QFrame()
    left.setObjectName("taskManagerLeftPanel")
    left.setFixedWidth(180)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(10, 10, 10, 10)
    ll.addWidget(_plain("LeftPanel（280px）"))
    editor = QFrame()
    editor.setObjectName("taskManagerEditorPanel")
    el = QVBoxLayout(editor)
    el.setContentsMargins(10, 10, 10, 10)
    el.addWidget(_plain("EditorPanel"))
    right = QFrame()
    right.setObjectName("taskManagerRightPanel")
    right.setFixedWidth(140)
    rl = QVBoxLayout(right)
    rl.setContentsMargins(10, 10, 10, 10)
    rl.addWidget(_plain("RightPanel"))
    row.addWidget(left)
    row.addWidget(editor, 1)
    row.addWidget(right)
    return holder


def build_pages_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "⑤ 页面级样式组合",
        "每个页面 = PAGE_BASE_QSS + 自己的例外规则。窗口级样式（底色、固定尺寸）"
        "不下沉到页面。",
    )
    page.add_section("共享前缀")
    page.add_item(
        "PAGE_BASE_QSS — 页面级样式唯一入口",
        source="ui_common.PAGE_BASE_QSS",
        note="= MESSAGE_BOX_QSS + SCROLLBAR_QSS + COMMON_CONTROLS_QSS + PAGE_TITLE_QSS。"
        "四个界面模块的页面都从这里取公共规则，避免同一控件出现第二套基础样式。",
        body=_plain("COMMON_CONTROLS_QSS = UNIFIED_FORM_CONTROLS_QSS + UNIFIED_COMBO_QSS "
                    "+ UNIFIED_CHECKBOX_QSS + UNIFIED_BUTTONS_QSS"),
    )
    page.add_item(
        "PANEL_BASE_QSS + PANEL_CONTENT_QSS — 嵌入式面板底座",
        source="ui_common.PANEL_BASE_QSS / PANEL_CONTENT_QSS",
        note="查看器、内嵌编辑器用：面板透明背景；"
        "内容区白底圆角 8（QPlainTextEdit / QTreeWidget 共用一套）。",
        body=_panel_content_demo(),
    )
    page.add_item(
        "窗口级样式（不下沉到页面）",
        source="ui_main.MainWindow._build_ui()",
        object_name="QMainWindow / appPages",
        note="窗口底色 #f3f7f5、固定 1240×820 且禁用最大化；"
        "QStackedWidget# appPages 承载主页/设置页/任务管理页三页切换。",
        body=_plain("QMainWindow { background: #f3f7f5; color: #172b2c; }\n"
                    "setFixedSize(1240, 820)  ·  WindowMaximizeButtonHint = False"),
    )

    page.add_section("各页面专有分组")
    page.add_item(
        "主页（MainPage）",
        source="ui_main.MainPage._apply_style()",
        note="brandLabel / titleLabel / mutedLabel / deviceBadge / taskSectionTitle / "
        "taskSortHint / taskList / logView / contentSplitter / QStatusBar / "
        "mainActionBar，以及 4 个 runState 状态（idle|busy|stop|stopping）。",
        body=_runstate_demo(),
    )
    page.add_item(
        "设置页（SettingsPage + SettingsDialog）",
        source="ui_settings.SettingsDialog._style_sheet()",
        note="settingsNavPanel / settingsNav / settingsStack / settingsTabPage / "
        "settingsOptionCard / settingsModelRow / settingsRetryRow / settingsScroll / "
        "settingsSection* / settingsGroupTitle / settingsRunLabel / settingsRetryId / "
        "settingsCompletionState / settingsFooter / settingsModelRadio / "
        "settingsModelSize / settingsTaskList。",
        body=_plain("共 5 个分页：运行设置 · 执行次数 · 今日完成 · OCR 模型 · 邮件通知"),
    )
    page.add_item(
        "任务管理页（TaskManagerPage / TaskManagerWidget）",
        source="ui_task_manager.TaskManagerPage/_TaskToast/各查看器",
        note="taskManagerHLine / VLine / LeftPanel / EditorPanel / RightPanel / Tabs "
        "(QTabBar) / DirtyLabel / taskToast + taskToastTitle + taskToastMessage / "
        "runStatusLabel / runLogEdit / actionParamsContainer。"
        "按钮一律用六大角色，不再有页面专属按钮外观。",
        body=_tab_demo(),
    )
    page.finish()
    return page


def _panel_content_demo() -> QWidget:
    # 不设自身样式表：PANEL_BASE_QSS / PANEL_CONTENT_QSS 已由窗口样式表提供。
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(card_title("PANEL_CONTENT_QSS"))
    edit = QPlainTextEdit(
        "QPlainTextEdit / QTreeWidget 共用的白底圆角内容区\n选中文字为浅绿 #cce8e0"
    )
    edit.setFixedHeight(80)
    layout.addWidget(edit)
    return holder


def _runstate_demo() -> QWidget:
    # 关键：容器**不设**自己的样式表，完全继承 demo 窗口的样式表。
    # Qt 中控件自身 setStyleSheet 的规则优先于祖先窗口；一旦这里再套一层
    # 样式表，其中的通用 QPushButton#secondaryButton 规则就会盖掉窗口样式表里的
    # [runState="stop"] 属性规则，按钮会错误地显示成默认浅绿。
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for text, state in (
        ("执行全部", "idle"),
        ("停止", "stop"),
        ("正在停止…", "stopping"),
        ("执行", "busy"),
    ):
        button = secondary_button(text, width=MAIN_ACTION_BUTTON_WIDTH)
        button.setProperty("runState", state)
        if state in ("busy", "stopping"):
            button.setEnabled(False)
        layout.addWidget(button)
    layout.addStretch(1)
    return holder


def _tab_demo() -> QWidget:
    # 不设自身样式表：taskManagerTabs 的 QTabBar 规则由窗口样式表提供。
    tabs = QTabWidget()
    tabs.setObjectName("taskManagerTabs")
    for name in ("任务", "复合动作"):
        inner = QWidget()
        QVBoxLayout(inner).addWidget(_plain(f"{name}列表内容"))
        tabs.addTab(inner, name)
    tabs.setFixedHeight(120)
    tabs.setFixedWidth(280)
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(tabs)
    row.addStretch(1)
    return holder


def build_states_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "⑥ 状态颜色与动态属性",
        "任务状态、运行按钮状态等由「动态属性 + 属性选择器」驱动，"
        "改属性后需 refresh_style() 重新套用。",
    )
    page.add_section("主页任务状态色（QColor，非 QSS）")

    from free_app.ui_main import MainPage

    page.add_item(
        "任务状态 → 文字颜色",
        source="ui_main.MainPage._STATE_LABELS / _STATE_COLORS",
        note="主页任务列表每行的文字颜色按状态取自这张表；"
        "状态由窗口在运行时写入 task_states。",
        body=_state_colors_demo(MainPage),
    )
    page.add_item(
        "任务状态标签文案",
        source="ui_main.MainPage._STATE_LABELS",
        note="这是本次新增 completed_today（今日已完成）后的完整状态集。",
        body=_state_labels_demo(MainPage),
    )

    page.add_section("动态属性选择器")
    page.add_item(
        'runState 属性：按钮执行态',
        source="ui_main.MainPage._apply_style() · [runState=...]",
        object_name="secondaryButton[runState]",
        note='取值 idle | busy | stop | stopping。执行中把“执行”变“停止”'
        "（红色），另一颗置灰不可点；只改文字与属性，不改几何，整排不跳动。",
        body=_plain("idle → 默认浅绿    busy → 灰底禁用    "
                    "stop → 红色（#d6534d）    stopping → 黄底（#fff0d9）"),
    )
    page.add_item(
        'state 属性：状态徽标 / 完成状态',
        source="ui_main [state] · ui_settings [state]",
        object_name="deviceBadge[state] / settingsCompletionState[state]",
        note="同一控件按属性值切换配色，避免为每种状态建一套 objectName。",
        body=_plain('deviceBadge: ready | checking | error\n'
                    'settingsCompletionState: done | stale | pending'),
    )
    page.add_item(
        "设计令牌层（ui_tokens.py）",
        source="free_app/ui_tokens.py",
        note="全项目唯一的颜色/圆角/字号/字重/间距来源，不导入 Qt、不含选择器。"
        "四个界面模块都从它取值，因此“界面一共用了多少种颜色”是一份可读完的清单。",
        body=_token_summary(),
    )
    page.finish()
    return page


def _token_summary() -> QWidget:
    """展示 token 层的规模与分组，让“样式只有这些”一目了然。"""

    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    groups = (
        ("品牌绿", 12),
        ("语义色（运行蓝/危险红/警告琥珀/成功绿）", 25),
        ("中性色（文字/底色/边框）", 30),
        ("圆角 · 字号 · 字重 · 间距 · 控件高度", 18),
        ("渐变", 6),
        ("按钮角色（6 个）与尺寸", 15),
    )
    for label, count in groups:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        name = QLabel(label)
        name.setObjectName("demoNote")
        name.setFixedWidth(300)
        value = QLabel(f"{count} 个常量")
        value.setObjectName("demoConstValue")
        row_layout.addWidget(name)
        row_layout.addWidget(value)
        row_layout.addStretch(1)
        layout.addWidget(row)
    return holder


def _state_colors_demo(main_page_cls) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for state, label in main_page_cls._STATE_LABELS.items():
        color = main_page_cls._STATE_COLORS[state]
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        sample = QLabel(f"● {label}")
        sample.setStyleSheet(
            f"color: {color.name()}; font-size: 13px; font-weight: 600;"
        )
        sample.setFixedWidth(120)
        meta = QLabel(f'{state}   {color.name()}')
        meta.setObjectName("demoSwatchLabel")
        row_layout.addWidget(sample)
        row_layout.addWidget(meta)
        row_layout.addStretch(1)
        layout.addWidget(row)
    return holder


def _state_labels_demo(main_page_cls) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for state, label in main_page_cls._STATE_LABELS.items():
        chip = QLabel(f"{label}")
        chip.setObjectName("demoChip")
        chip.setToolTip(state)
        layout.addWidget(chip)
    layout.addStretch(1)
    return holder


def build_palette_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "⑦ 配色板",
        "从各模块 QSS 中提取的实际用色，按用途归类。",
    )
    page.add_section("品牌与主色")
    page.add_item(
        "绿色主色阶（按钮 / 选中 / 强调）",
        source="ui_common.GREEN_VGRAD / GREEN_DIAG / DARK_PRESSED_VGRAD 等",
        note="主按钮渐变、复选框勾选填充、导航与列表选中背景都取自这一族。",
        body=_swatch_grid([
            ("#1a9385", "渐变起点（GREEN_VGRAD/VGRAD_HOVER/DIAG）"),
            ("#137f73", "渐变终点 · 选中实心 · 边框强调"),
            ("#0e6e64", "hover 加深"),
            ("#095b54", "按下最深（DARK_PRESSED_VGRAD 终点）"),
            ("#0c6e63", "选中/悬停文字色"),
            ("#146e65", "次要按钮文字"),
        ]),
    )
    page.add_item(
        "中性色（文字 / 底 / 边框）",
        source="各模块 QSS 通用取值",
        note="文字三级：正文 #193331 / 次级 #31504d / 说明 #718783；"
        "边框统一 #cbdcd6 系，页面底 #f2f6f4～#f3f7f5。",
        body=_swatch_grid([
            ("#172b2c", "窗口正文文字"),
            ("#193331", "表单/面板正文文字"),
            ("#31504d", "按钮文字"),
            ("#49615f", "表单标签"),
            ("#718783", "说明文字（muted）"),
            ("#cbdcd6", "标准边框"),
            ("#f2f6f4", "对话框底色"),
            ("#f3f7f5", "主窗口底色"),
            ("#ffffff", "卡片 / 输入框底"),
        ]),
    )
    page.add_item(
        "语义色（成功 / 警告 / 错误 / 运行）",
        source="ui_tokens.SUCCESS_* / WARN_* / DANGER_* / RUN_BLUE",
        note="绿=成功/可用，黄=进行中/警告，红=失败/危险，蓝=特殊运行操作（全项目唯一）。",
        body=_swatch_grid([
            (_t.SUCCESS_FILL, "可用徽标底（SUCCESS_FILL）"),
            (_t.SUCCESS_TEXT, "可用徽标字（SUCCESS_TEXT）"),
            (_t.WARN_FILL, "检查中徽标底（WARN_FILL）"),
            (_t.WARN_TEXT, "检查中徽标字（WARN_TEXT）"),
            (_t.DANGER_FILL, "错误徽标底（DANGER_FILL）"),
            (_t.DANGER_TEXT_ALT, "错误徽标字 / Toast 标题"),
            (_t.DANGER_SOLID, "停止按钮（DANGER_SOLID）"),
            (_t.RUN_BLUE, "运行按钮蓝（RUN_BLUE）"),
        ]),
    )
    page.finish()
    return page


def _swatch_grid(pairs: list[tuple[str, str]]) -> QWidget:
    holder = QWidget()
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(6)
    for index, (color, label) in enumerate(pairs):
        row, col = divmod(index, 2)
        grid.addWidget(_swatch(color, label), row, col)
    return holder


def build_scrollbar_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "⑧ 滚动条与弹层",
        "6px 细滚动条、自绘下拉弹层、Toast 与消息框。",
    )
    page.add_section("滚动条")
    page.add_item(
        "SCROLLBAR_QSS — 全局细滚动条",
        source="ui_common.SCROLLBAR_QSS",
        note="宽/高 6px，滑柄 #b9d3ca、hover #8bbeb2、圆角 3；"
        "隐藏加减箭头（add-line/sub-line 尺寸 0）。"
        "所有页面与滚动区共用，没有第二种滚动条。",
        body=_scrollbar_demo(),
    )
    page.add_section("弹层与通知")
    page.add_item(
        "自绘下拉弹层（SettingsComboBox）",
        source="ui_common.COMBO_POPUP_QSS + _PopupFrame.paintEvent",
        object_name="settingsComboPopup / settingsComboPopupList",
        note="不用 Qt 原生弹层：外层 QFrame 半透明 + 圆角 7 边框 #b9d3ca，"
        "内层列表项高 34、选中 #d6ebe5。保证跨平台外观一致。",
        body=_combo_demo(),
    )
    page.add_item(
        "taskToast — 非模态错误提示",
        source="ui_task_manager._TaskToast",
        object_name="taskToast / taskToastTitle / taskToastMessage",
        note="无边框置顶小窗，红系配色（#fff7f6 / #e6aaa5 / #a3403b），"
        "圆角 10 由 paintEvent 手绘；显示 5 秒后自动关闭，位置居中于父窗口。",
        body=_toast_demo(),
    )
    page.add_item(
        "QMessageBox — 中文消息框",
        source="ui_common.MESSAGE_BOX_QSS + QMessageBox 子类 / confirm()",
        object_name="messageBoxAction",
        note="标准按钮文案被替换为中文（确定/取消/是/否/重试/放弃…）；"
        "默认按钮走绿色渐变，其余为白底次要样式。",
        body=_row(*_messagebox_buttons()),
    )
    page.finish()
    return page


def _scrollbar_demo() -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    area = QScrollArea()
    area.setObjectName("settingsScroll")
    area.setFixedSize(320, 110)
    area.setWidgetResizable(True)
    inner = QWidget()
    inner_layout = QVBoxLayout(inner)
    for i in range(14):
        inner_layout.addWidget(_plain(f"第 {i + 1} 行 —— 拖动右侧细滚动条查看效果"))
    area.setWidget(inner)
    layout.addWidget(area)
    layout.addStretch(1)
    return holder


def _combo_demo() -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    combo = SettingsComboBox()
    combo.addItems(["自动", "百度 BCE", "ModelScope", "HuggingFace"])
    combo.setFixedWidth(180)
    combo.setCurrentIndex(1)
    layout.addWidget(combo)
    layout.addWidget(_plain("点击展开可见自绘弹层"))
    layout.addStretch(1)
    return holder


def _toast_demo() -> QWidget:
    from free_app.ui_task_manager import _TaskToast

    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    # _TaskToast 自带样式表（真实代码即如此），但它的规则只针对
    # taskToast* 这几个 objectName，不会与窗口样式表冲突，可直接内嵌。
    toast = _TaskToast(
        holder, "获取包名失败", "未找到当前前台应用，请先启动目标任务对应的 App。"
    )
    toast.setParent(holder)
    toast.setWindowFlags(Qt.WindowType.Widget)
    layout.addWidget(toast)
    layout.addStretch(1)
    return holder


def _messagebox_buttons() -> list[QPushButton]:
    holder_styles = MESSAGE_BOX_QSS
    buttons: list[QPushButton] = []
    for text, is_default in (("确定", False), ("保存", True), ("取消", False), ("放弃", False)):
        button = QPushButton(text)
        button.setObjectName("messageBoxAction")
        if is_default:
            button.setDefault(True)
        button.setStyleSheet(holder_styles)
        buttons.append(button)
    return buttons


def build_sizes_page(font: str) -> _DemoPage:
    page = _DemoPage(
        "⑨ 尺寸常量",
        "尺寸集中在 ui_common；「同一种外观是否需要写第二遍」是判定标准，"
        "主页与二级页有意的层级差异保留为不同常量。",
    )
    page.add_section("ui_common 尺寸常量")
    page.add_item(
        "按钮尺寸族",
        source="ui_common 常量区",
        note="高度一律交给样式表 min-height，只有宽度与个别最小宽度用常量锁定，"
        "因此执行状态切换只改文字不改几何。",
        body=_const_table([
            ("ROW_BUTTON_HEIGHT", f"{ROW_BUTTON_HEIGHT}px", "成排按钮统一高度（主页动作栏 / 二级页头 / 对话框底栏）"),
            ("MAIN_ACTION_BUTTON_WIDTH", f"{MAIN_ACTION_BUTTON_WIDTH}px", "主页动作栏四个按钮同宽"),
            ("MAIN_ICON_BUTTON_WIDTH", f"{MAIN_ICON_BUTTON_WIDTH}px", "主页齿轮图标按钮宽度"),
            ("TOOL_BUTTON_WIDTH", f"{TOOL_BUTTON_WIDTH}px", "二级页头工具按钮默认宽"),
            ("BACK_BUTTON_WIDTH", f"{BACK_BUTTON_WIDTH}px", "返回按钮宽度"),
            ("WIDE_BUTTON_MIN_WIDTH", f"{WIDE_BUTTON_MIN_WIDTH}px", "较长中文次级按钮的最小宽度"),
            ("OUTLINE_COMBO_WIDTH", f"{OUTLINE_COMBO_WIDTH}px", "表单小号下拉框宽度"),
        ]),
    )
    page.add_item(
        "页面与布局常量",
        source="ui_settings.CONTENT_MARGINS/CONTENT_SPACING · ui_main · ui_task_manager",
        body=_const_table([
            ("CONTENT_MARGINS", "(28, 22, 28, 22)", "设置页内容区边距，与任务管理页页头留白一致"),
            ("CONTENT_SPACING", "14", "设置页内容区间距"),
            ("主页 task_panel 宽", "282px", "与任务管理页 280px 面板 + 2px 分隔条对齐"),
            ("设置导航面板宽", "176px", "settingsNavPanel 固定宽"),
            ("窗口尺寸", "1240×820", "固定且禁用最大化"),
            ("deviceBadge", "216×40", "设备状态徽标固定尺寸"),
            ("主页 taskList 行高", "70px", "setSizeHint，两行文字"),
        ]),
    )
    page.add_item(
        "动作编辑器常量（ui_task_manager）",
        source="ui_task_manager 各表单",
        note="动作参数表单由 action_schema.ParamSpec 驱动生成，"
        "因此新增原子动作通常无需改样式。",
        body=_plain("BoolCombo / IntSpin / FloatSpin / LineEdit / TextsEditor / "
                    "分支步骤编辑器 等按 ParamSpec 的 kind 选择控件"),
    )
    page.finish()
    return page


def _const_table(rows: list[tuple[str, str, str]]) -> QWidget:
    holder = QWidget()
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(6)
    for index, (name, value, note) in enumerate(rows):
        key = QLabel(name)
        key.setObjectName("demoConstKey")
        val = QLabel(value)
        val.setObjectName("demoConstValue")
        desc = QLabel(note)
        desc.setObjectName("demoNote")
        desc.setWordWrap(True)
        grid.addWidget(key, index, 0)
        grid.addWidget(val, index, 1)
        grid.addWidget(desc, index, 2)
    grid.setColumnStretch(2, 1)
    return holder


def collect_page_styles() -> str:
    """收集四个界面模块各自的页面级样式表。

    很多 ``objectName`` 的规则（settingsOptionCard / settingsRetryRow /
    taskList / deviceBadge / taskManagerTabs …）定义在各页面自己的
    ``_style_sheet()`` / ``_apply_style()`` 里，只有 ``PAGE_BASE_QSS``
    是不够的——demo 必须把它们一并拼进来，否则这些控件会渲染成"没有样式"。

    这些片段会重复包含 ``COMMON_CONTROLS_QSS`` 等公共规则，但不需要去重：
    Qt 对同优先级的重复规则按"后者胜"处理，而每个页面的样式表都把属性
    选择器规则紧跟在基础规则之后，重复拼接不改变最终结果（已实测确认）。

    .. important::
       **展示控件及其父容器都不要调用 ``setStyleSheet``。** Qt 中控件自身
       样式表的规则优先于祖先窗口，一旦某个展示容器自带样式表，其中的通用
       规则（如 ``QPushButton#secondaryButton { background: #e3f2ed }``）
       就会盖掉窗口样式表里的属性规则
       （如 ``QPushButton#secondaryButton[runState="stop"]``），
       让 demo 显示出与真实程序不同的配色。实测：容器自带样式表时按钮是
       默认浅绿 ``#e3f2ed``，不设样式表时才是正确的红色 ``#d6534d``。
    """

    from free_app.ui_main import MainPage
    from free_app.ui_settings import SettingsDialog
    from free_app.ui_task_manager import (
        JsonViewerWidget,
        RunViewerWidget,
        TaskManagerWidget,
        UiTreeDumpWidget,
    )

    parts: list[str] = []
    # MainPage 在构造时就把样式写进自身，用一次性实例取出（不显示）。
    main_page = MainPage(make_adb=lambda: None)  # type: ignore[arg-type,return-value]
    parts.append(main_page.styleSheet())
    parts.append(SettingsDialog._style_sheet())
    parts.append(TaskManagerWidget._style_sheet())
    parts.append(RunViewerWidget._style_sheet())
    parts.append(JsonViewerWidget._style_sheet())
    parts.append(UiTreeDumpWidget._style_sheet())
    main_page.deleteLater()
    return "\n".join(parts)


# ------------------------------------------------------------------ Demo 壳

DEMO_QSS = """
QWidget#demoRoot { background: #f3f7f5; color: #172b2c; }
QFrame#demoSidebar {
    background: #e8f0ed;
    border-right: 1px solid #d1e0db;
}
QListWidget#demoNav {
    background: transparent;
    border: none;
    outline: none;
    padding: 0;
}
QListWidget#demoNav::item {
    min-height: 42px;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 0 12px;
    color: #48615f;
    font-weight: 600;
}
QListWidget#demoNav::item:hover { background: #dcebe6; color: #146e65; }
QListWidget#demoNav::item:selected {
    background: #d4e9e3;
    color: #0c6e63;
    font-weight: 700;
    outline: none;
}
QLabel#demoBrand { color: #0b8478; font-size: 20px; font-weight: 800; }
QLabel#demoBrandSub { color: #718181; font-size: 11px; }
QFrame#demoCard {
    background: #ffffff;
    border: 1px solid #cbdcd6;
    border-radius: 9px;
}
QLabel#demoBadge {
    background: #137f73;
    color: #ffffff;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 800;
    padding: 3px 0;
}
QLabel#demoItemTitle { color: #193331; font-size: 15px; font-weight: 700; }
QLabel#demoMeta {
    color: #7c918d;
    font-size: 11px;
    font-family: Consolas, "Cascadia Mono", monospace;
}
QLabel#demoNote { color: #5c7370; font-size: 12px; }
QLabel#demoSectionTitle {
    color: #0b8478;
    font-size: 14px;
    font-weight: 800;
    padding: 10px 0 2px 0;
}
QLabel#demoSectionNote { color: #718181; font-size: 11px; }
QLabel#demoPageSubtitle { color: #758986; font-size: 12px; }
QLabel#demoSwatchLabel {
    color: #4a615e;
    font-size: 12px;
    font-family: Consolas, "Cascadia Mono", monospace;
}
QLabel#demoChip {
    background: #eef5f3;
    border: 1px solid #d5e5e0;
    border-radius: 11px;
    color: #3f5b58;
    font-size: 11px;
    padding: 3px 10px;
}
QLabel#demoConstKey {
    color: #146e65;
    font-size: 12px;
    font-family: Consolas, "Cascadia Mono", monospace;
    font-weight: 700;
}
QLabel#demoConstValue {
    color: #a3403b;
    font-size: 12px;
    font-family: Consolas, "Cascadia Mono", monospace;
}
QFrame#demoDivider { background: #d1e0db; border: none; }
"""


class StyleDemoWindow(QWidget):
    """样式总览窗口：左侧分类导航 + 右侧编号条目。"""

    PAGES = (
        ("① 按钮", "build_buttons_page"),
        ("② 表单控件", "build_inputs_page"),
        ("③ 文字标签", "build_labels_page"),
        ("④ 容器卡片", "build_containers_page"),
        ("⑤ 页面组合", "build_pages_page"),
        ("⑥ 状态与属性", "build_states_page"),
        ("⑦ 配色板", "build_palette_page"),
        ("⑧ 滚动条与弹层", "build_scrollbar_page"),
        ("⑨ 尺寸常量", "build_sizes_page"),
    )

    def __init__(self, font_family: str) -> None:
        super().__init__()
        self.setObjectName("demoRoot")
        self.setWindowTitle("FREE · 界面样式总览")
        self.resize(1320, 900)
        self._font = font_family
        self._build()

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("demoSidebar")
        sidebar.setFixedWidth(210)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 18)
        sidebar_layout.setSpacing(4)
        brand = QLabel("FREE")
        brand.setObjectName("demoBrand")
        brand_sub = QLabel("界面样式总览 Demo")
        brand_sub.setObjectName("demoBrandSub")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(brand_sub)
        sidebar_layout.addSpacing(14)

        self.nav = QListWidget()
        self.nav.setObjectName("demoNav")
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSpacing(5)
        self.nav.setUniformItemSizes(True)
        for title, _ in self.PAGES:
            self.nav.addItem(title)
        sidebar_layout.addWidget(self.nav, 1)

        hint = QLabel("序号即条目编号，\n每张卡片标注来源与 objectName。")
        hint.setObjectName("demoBrandSub")
        hint.setWordWrap(True)
        sidebar_layout.addWidget(hint)
        root.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("demoStack")
        for _, builder_name in self.PAGES:
            builder = globals()[builder_name]
            self.stack.addWidget(builder(self._font))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        root.addWidget(self.stack, 1)

        self.setStyleSheet(PAGE_BASE_QSS + self._page_styles() + DEMO_QSS)

    @staticmethod
    def _page_styles() -> str:
        """四个界面模块的页面级样式表（供各 objectName 规则生效）。"""

        return collect_page_styles()

    def show_page(self, index: int) -> None:
        self.nav.setCurrentRow(index)
        self.stack.setCurrentIndex(index)

    def page_at(self, index: int) -> _DemoPage:
        return self.stack.widget(index)  # type: ignore[return-value]


def probe() -> int:
    """自检模式（``--probe``）：构建全部页面后立刻退出，不开窗口。

    用来确认 demo 能在当前环境下正常导入 free_app、加载样式并构建控件——
    不进入事件循环，便于脚本化验证（也方便排查"中文显示成方框"这类问题）。
    """

    app = QApplication.instance() or QApplication(sys.argv)
    family = load_chinese_fonts()
    if family:
        app.setFont(QFont(family, 9))
    window = StyleDemoWindow(family)
    total = sum(window.page_at(i)._index for i in range(window.stack.count()))
    print(f"demo OK: {window.stack.count()} 页 / {total} 个条目")
    print(f"  仓库根   : {PROJECT_ROOT}")
    print(f"  中文字体 : {family or '（未找到，中文可能显示为方框）'}")
    window.deleteLater()
    return 0


def main() -> int:
    if "--probe" in sys.argv:
        return probe()

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FREE 样式总览")
    family = load_chinese_fonts()
    if family:
        app.setFont(QFont(family, 9))
    window = StyleDemoWindow(family)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

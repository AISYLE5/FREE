"""界面设计令牌（Design Tokens）。

全项目**唯一**的颜色、圆角、字号、字重与间距取值来源。所有 QSS 都应
引用这里的常量，而不再写裸值——这样"界面用了多少种颜色"是一个可以一眼
读完的清单，而不是散落在四个模块里的上百个字面量。

分层规则（见 ARCHITECTURE.md「样式体系」）：

- ``ui_tokens.py``（本模块）：只放**取值**，不含选择器，不导入 Qt。
- ``ui_common.py``：把 token 组装成 QSS 片段与控件工厂。
- ``ui_*.py`` 各页面：只写自己的例外规则，取值同样取自 token。

命名约定：``角色_状态``，例如 ``SURFACE_HOVER``、``BORDER_STRONG``。
"""

from __future__ import annotations

# ============================================================ 颜色 · 基础

# 品牌绿：从"深按"到"浅底"一条色阶，界面的强调色全部取自这里。
BRAND_DEEP = "#095b54"  # 按下最深
BRAND_DARK = "#0e6e64"  # 悬停加深
BRAND = "#137f73"  # 主色：选中实心 / 边框强调 / 渐变终点
BRAND_LIGHT = "#1a9385"  # 渐变起点
BRAND_TEXT = "#0c6e63"  # 选中态文字
BRAND_TEXT_SOFT = "#146e65"  # 次要按钮文字
BRAND_LINE = "#65b3a7"  # 按下边框
BRAND_LINE_HOVER = "#83b9ad"  # 悬停边框
BRAND_LINE_SOFT = "#afd5ca"  # 常态边框（浅绿控件）
BRAND_WASH = "#d6ebe5"  # 选中底
BRAND_WASH_HOVER = "#edf7f4"  # 悬停底
BRAND_WASH_PRESSED = "#dcece7"  # 按下底
BRAND_FILL = "#e3f2ed"  # 次要点缀填充
BRAND_TRACK = "#b9d3ca"  # 滚动条滑柄
BRAND_TRACK_HOVER = "#8bbeb2"  # 滚动条滑柄悬停

# 运行蓝：仅用于"运行/试运行"这类与保存语义区分的操作。
RUN_BLUE = "#1976d2"
RUN_BLUE_HOVER = "#1565c0"
RUN_BLUE_PRESSED = "#0d47a1"

# 危险红：不可逆操作与失败态。
DANGER_TEXT = "#a34a42"
DANGER_TEXT_ALT = "#a3403b"  # 错误徽标字 / Toast 标题
DANGER_FILL = "#fbeae8"
DANGER_LINE = "#e5b9b3"
DANGER_FILL_HOVER = "#f6d8d4"
DANGER_LINE_HOVER = "#d88f87"
DANGER_FILL_PRESSED = "#efc4be"
DANGER_LINE_PRESSED = "#c9776e"
DANGER_SOLID = "#d6534d"  # 停止按钮 / 徽标红
DANGER_SOLID_HOVER = "#bd403b"
DANGER_SOLID_PRESSED = "#a93632"

# 警告琥珀：检查中 / 停止中 / 过期记录。
WARN_TEXT = "#95651b"
WARN_TEXT_DEEP = "#9a6a24"
WARN_TEXT_ALT = "#a06b1e"
WARN_FILL = "#fff3d9"
WARN_LINE = "#edd39a"
WARN_LINE_ALT = "#e4b56c"
WARN_FILL_DEEP = "#fff0d9"

# 成功绿：可用 / 今日已完成。
SUCCESS_TEXT = "#126b5f"
SUCCESS_TEXT_ALT = "#1f7a4c"
SUCCESS_TEXT_SOFT = "#2b7b4e"
SUCCESS_LINE = "#a5d8c8"
SUCCESS_FILL = "#e0f3ec"

# ------------------------------------------------------------ 颜色 · 中性
# 文字四级（越靠后越淡）
TEXT_STRONG = "#172b2c"  # 窗口级正文
TEXT = "#193331"  # 面板/表单正文
TEXT_HEADING = "#203637"  # 页面标题
TEXT_BUTTON = "#31504d"  # 按钮文字
TEXT_LABEL = "#49615f"  # 表单标签
TEXT_SECTION = "#244340"  # 区块标题
TEXT_MUTED = "#718783"  # 说明文字
TEXT_MUTED_ALT = "#758986"  # 说明文字（同族，已归并）
TEXT_FAINT = "#8a9695"  # 未执行 / 最淡
TEXT_DISABLED = "#9aa9a7"
TEXT_ON_BRAND = "#ffffff"

# 底色三级：页面 → 分区 → 卡片
SURFACE_PAGE = "#f3f7f5"  # 主窗口底
SURFACE_PAGE_ALT = "#f2f6f4"  # 对话框底（与上一档同族）
SURFACE_SECTION = "#e8f0ed"  # 导航面板等分区底
SURFACE_CARD = "#ffffff"  # 卡片 / 输入框 / 内容区底
SURFACE_SUBTLE = "#f7faf9"  # 卡片内浅底（已归并 4 个近似值）
SURFACE_HOVER = "#edf7f4"  # 悬停底（= BRAND_WASH_HOVER）
SURFACE_DISABLED = "#e9eeec"

# 边框三级
BORDER = "#cbdcd6"  # 标准边框
BORDER_SOFT = "#d1e0db"  # 分隔线 / 弱边框
BORDER_FAINT = "#dce5e2"  # 极弱边框（禁用态）
BORDER_STRONG = "#b9d3ca"  # 弹层/滚动条边框

# 其它
OVERLAY_DIM = "#8a9695"  # 中性灰（未执行态）
SHADOW = "#7f9a94"  # 浮层投影色（与页面底色同族，偏冷灰绿）

# ============================================================ 圆角 / 字号

# 圆角台阶：小（指示器/滑柄）→ 中（按钮/输入框/列表项）→ 大（卡片/弹层）
RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 10

# 字号台阶：说明 → 次要 → 正文 → 小标题 → 区块标题 → 页面标题
FONT_XS = 11
FONT_SM = 12
FONT_MD = 13
FONT_LG = 14
FONT_XL = 18
FONT_TITLE = 22

# 字重台阶：常规强调 → 按钮 → 小标题 → 大标题
WEIGHT_MEDIUM = 600
WEIGHT_SEMIBOLD = 700
WEIGHT_BOLD = 750

# ============================================================ 间距 / 尺寸

# 内边距台阶
PAD_XS = 8
PAD_SM = 12
PAD_MD = 16
PAD_LG = 20

# 间距台阶
GAP_SM = 6
GAP_MD = 10
GAP_LG = 14
GAP_XL = 18

# 控件高度（统一后只保留两档：常规控件与紧凑控件）
CONTROL_HEIGHT = 40
CONTROL_HEIGHT_COMPACT = 32

# 最低可点面积
TOUCH_MIN = 40

# ============================================================ 渐变

GREEN_VGRAD = (
    f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BRAND_LIGHT}, stop:1 {BRAND})"
)
GREEN_VGRAD_HOVER = (
    f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BRAND_LIGHT}, stop:1 {BRAND_DARK})"
)
GREEN_DIAG = (
    f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {BRAND_LIGHT}, stop:1 {BRAND})"
)
DARK_PRESSED_VGRAD = (
    f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BRAND}, stop:1 {BRAND_DEEP})"
)
RED_VGRAD = (
    f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {DANGER_SOLID}, stop:1 {DANGER_SOLID_HOVER})"
)
RED_VGRAD_HOVER = (
    f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {DANGER_SOLID_HOVER}, stop:1 {DANGER_SOLID_PRESSED})"
)


# ============================================================ 按钮角色

# 按钮的**语义角色**。QSS 用这些常量拼属性选择器，控件用它们设 objectName，
# 两侧不会再各写一遍字符串字面量。
#
# 角色只有五种，每个角色含常态 / 悬停 / 按下三态：
BUTTON_PRIMARY = "primaryButton"  # 主操作（保存/确定）：绿色渐变
BUTTON_SECONDARY = "secondaryButton"  # 次要操作：浅绿
BUTTON_NEUTRAL = "neutralButton"  # 中性/取消/返回：浅灰
BUTTON_DANGER = "dangerButton"  # 不可逆操作：浅红
BUTTON_RUN = "runButton"  # 运行/试运行：蓝色实心
BUTTON_STOP = "stopButton"  # 停止：红色渐变
BUTTON_DIALOG = "messageBoxAction"  # 消息框按钮（由 QMessageBox 样式表收口）

# 可切换按钮的选中态用 ``[checked="true"]`` 之外的伪类，见 QSS。
# 所有按钮统一尺寸常量（去掉主页与二级页的高度差异）。
BUTTON_WIDTH = 128  # 常规按钮宽度
BUTTON_WIDTH_NARROW = 108  # 较窄的页头工具按钮
BUTTON_WIDTH_BACK = 74  # 返回
BUTTON_WIDTH_ICON = 48  # 图标按钮
BUTTON_MIN_WIDTH_WIDE = 120  # 较长中文兜底
COMBO_WIDTH_NARROW = 130  # 表单小号下拉框

# FREE 界面样式清单

本文件列出 `free_app` 中的全部界面样式。配套的**可视化 Demo** 是同目录的
[styles.py](styles.py)，在仓库根下运行：

```powershell
python demo\styles.py        # 或 python -m demo.styles
python demo\styles.py --probe   # 自检：构建全部页面后退出，不开窗口
```

## 0. 分层与唯一来源

样式分三层，依赖方向单向向下：

| 层 | 模块 | 职责 |
| --- | --- | --- |
| **令牌层** | `ui_tokens.py` | 全部颜色、圆角、字号、字重、间距、按钮角色的**取值**。不导入 Qt、不含选择器 |
| **公共层** | `ui_common.py` | 把令牌组装成 QSS 片段与控件工厂；跨页面共用的 `objectName` 规则 |
| **页面层** | `ui_main.py` / `ui_settings.py` / `ui_task_manager.py` | 只写自己的例外规则，取值同样取自令牌 |

**页面层不允许出现裸色值**。自查：

```powershell
# 在 QSS 字符串行内查找裸色值，当前应为 0
Select-String -Path free_app\ui_*.py -Pattern '^\s*".*#[0-9a-fA-F]{6}'
```

页面级样式的公共前缀是 `PAGE_BASE_QSS`：

```text
PAGE_BASE_QSS = MESSAGE_BOX_QSS + SCROLLBAR_QSS + COMMON_CONTROLS_QSS + PAGE_TITLE_QSS
COMMON_CONTROLS_QSS = UNIFIED_FORM_CONTROLS_QSS + UNIFIED_COMBO_QSS
                    + UNIFIED_CHECKBOX_QSS + UNIFIED_BUTTONS_QSS
```

> **Qt 优先级提醒**：控件自身 `setStyleSheet` 的规则**优先于**祖先窗口的样式表。
> 判断"某个样式为什么没生效"时，先看该控件或其父容器有没有自己的样式表——
> 它会整体盖掉窗口上的同名规则（属性选择器如 `[runState="stop"]` 尤其容易中招）。

---

## 1. 按钮：六个语义角色

全项目所有 `QPushButton` 都经 `ui_common` 的工厂创建（整个包内只有一处
`QPushButton(` 字面量，就在工厂里）。角色名即 `objectName`，定义在
`ui_tokens.BUTTON_*`，QSS 与控件两侧引用同一常量。

| # | 角色 | 工厂 | 外观 |
| --- | --- | --- | --- |
| 1 | `secondaryButton` | `secondary_button()` / `outline_button()` | 浅绿（最常用） |
| 2 | `primaryButton` | `primary_button()` | 绿色渐变 + 白字，自动设为默认按钮 |
| 3 | `neutralButton` | `back_button()` / `outline_button()` | 浅灰，用于返回/取消 |
| 4 | `dangerButton` | `danger_button()` | 浅红，不可逆操作 |
| 5 | `runButton` | `run_button()` | 蓝色实心，运行/试运行 |
| 6 | `stopButton` | `stop_button()` | 红色渐变，停止 |

**六个角色共用同一套几何**：高度 `CONTROL_HEIGHT`=40px、同一圆角（`RADIUS_MD`=8）、
同一内边距与字重。因此成排按钮天然对齐；执行状态切换只改文字不改几何，整排不跳动。

**可切换按钮**（如主页「显示坐标」）用 `setCheckable(True)`，选中态由
`secondaryButton:checked` 表达，不必单独建 `objectName`。

**已删除的旧名**（共 12 个，全部归入上述角色）：
`settingsTestButton` · `settingsBrowseButton` · `settingsModelAction` ·
`taskManagerPointerButton` · `quietButton` · `settingsCancelButton` ·
`settingsSaveButton` · `taskManagerOperationSave` · `runActionButton` ·
`runStopButton` · `settingsTryButton`

尺寸常量：`BUTTON_WIDTH`=128 · `BUTTON_WIDTH_NARROW`=108 ·
`BUTTON_WIDTH_BACK`=74 · `BUTTON_WIDTH_ICON`=48 · `BUTTON_MIN_WIDTH_WIDE`=120。

---

## 2. 表单控件

| # | 名称 | 来源 | 说明 |
| --- | --- | --- | --- |
| 1 | `QLineEdit` / `QSpinBox` / `QDoubleSpinBox` | `UNIFIED_FORM_CONTROLS_QSS` | 高 40px、圆角 8、边框 `BORDER`；focus 换 2px `BRAND` |
| 2 | `QTextEdit` / `QPlainTextEdit` | 同上 | 同一套边框，内边距 8×12 |
| 3 | `SettingsComboBox` | `UNIFIED_COMBO_QSS` + 自绘 | **隐藏原生箭头并自绘 V 形**，跨平台一致；滚轮不误切换 |
| 4 | `QCheckBox` | `UNIFIED_CHECKBOX_QSS` | 指示器 18×18，勾选填充 `GREEN_DIAG` 对角渐变 |
| 5 | `QRadioButton` | `settingsModelRadio` | 15×15 圆形指示器 |
| 6 | `ExecutionCountComboBox` | `ui_common` | 0–10 次预设 |

---

## 3. 文字与标签

| # | objectName | 来源 | 字号 / 字重 |
| --- | --- | --- | --- |
| 1 | `settingsPageTitle` | `PAGE_TITLE_QSS` | `FONT_TITLE` 22px / `WEIGHT_BOLD` 750 |
| 2 | `settingsCardTitle` | `CARD_TITLE_QSS` | `FONT_LG` 14px / 700 |
| 3 | `settingsSectionTitle` | 各页 | `FONT_XL` 18px / 750 |
| 4 | `settingsGroupTitle` | 设置页 | `FONT_MD` 13px / 700 |
| 5 | `brandLabel` / `titleLabel` / `mutedLabel` | 主页 | 22px / 18px / 11px |
| 6 | `settingsSectionSubtitle` / `settingsSectionNote` | 设置页 | 12px / 11px |
| 7 | `deviceBadge` `[state]` | 主页 | ready 绿 / checking 黄 / error 红；固定 216×40 |
| 8 | `settingsCompletionState` `[state]` | 设置页 | done 绿 / stale 橙 / pending 灰 |
| 9 | `settingsOcrFeedback` | `OCR_FEEDBACK_QSS` | 12px 灰字 + 浅底圆角块 |
| 10 | `taskManagerDirtyLabel` | 任务管理页 | 11px 琥珀色 |
| 11 | `runStatusLabel` | 运行视图 | 颜色由 `_paint_status()` 动态设置 |
| 12 | `ElidedLabel` | `ui_common` | 省略号替代硬截断，最小宽 80px |

**字号台阶**：`FONT_XS` 11 → `FONT_SM` 12 → `FONT_MD` 13 → `FONT_LG` 14 →
`FONT_XL` 18 → `FONT_TITLE` 22。
**字重台阶**：`WEIGHT_MEDIUM` 600 → `WEIGHT_SEMIBOLD` 700 → `WEIGHT_BOLD` 750。

---

## 4. 容器与卡片

| # | objectName | 来源 | 说明 |
| --- | --- | --- | --- |
| 1 | `settingsOptionCard` | 设置页 | 白底 + 1px `BORDER` + 圆角 `RADIUS_LG` |
| 2 | `settingsModelRow` `:hover` | 设置页 | 模型行，hover 浅绿 |
| 3 | `settingsRetryRow` `:hover` | 设置页 | 执行次数/今日完成行 |
| 4 | `surface` | 主页 | 纯白直角 + 边框，固定宽 282px |
| 5 | `settingsNavPanel` / `settingsNav` | 设置页 | 面板宽 176px；选中实心浅绿 |
| 6 | `settingsTaskList` | **`LIST_QSS`（公共）** | 设置页 + 任务管理页共用，见下方注意 |
| 7 | `taskList` | 主页 | 行高 70px，支持拖拽排序 |
| 8 | `taskManagerHLine` / `VLine` | 任务管理页 | 分栏分隔（渐变线） |
| 9 | `settingsFooter` | `dialog_action_row()` | 仅一条上边框 |
| 10 | `settingsTabPage` / `settingsScroll` | `scrollable_page()` | 各设置分页统一骨架 |
| 11 | `taskManagerLeftPanel` / `EditorPanel` / `RightPanel` | 任务管理页 | 左 280px / 中编辑器 / 右面板 |
| 12 | `actionParamsContainer` | 动作编辑器 | 由 `ParamSpec` 驱动生成 |
| 13 | `PANEL_BASE_QSS` / `PANEL_CONTENT_QSS` | `ui_common` | 嵌入式面板底座 |

> **注意**：`settingsTaskList` 被设置页与任务管理页共 6 个控件使用，它的规则
> 只能定义在公共层 `ui_common.LIST_QSS`。曾放在设置页的样式表里，导致任务管理页
> 的列表项实际没有背景色（渲染成白色）。判断依据是"这个 `objectName` 是否只被
> 一个页面使用"——共用就必须下沉到公共层。

---

## 5. 页面级样式组合

| 页面 | 类 | 样式表来源 |
| --- | --- | --- |
| 主页 | `MainPage` | `_apply_style()`（窗口级底色在 `MainWindow`） |
| 设置页 | `SettingsPage` + `SettingsDialog` | `PAGE_BASE_QSS` / `_style_sheet()` |
| 任务管理页 | `TaskManagerPage` / `TaskManagerWidget` | `PAGE_BASE_QSS` / `_style_sheet()` |
| 查看器 | `JsonViewerWidget` / `RunViewerWidget` / `UiTreeDumpWidget` | 各自 `_style_sheet()` |

窗口级（不下沉到页面）：`QMainWindow` 底色 `SURFACE_PAGE`、固定 1240×820 且
禁用最大化、`QStackedWidget#appPages` 三页切换、`QStatusBar`。

---

## 6. 状态颜色与动态属性

**任务状态 → 文字颜色**（`MainPage._STATE_COLORS`，取值来自令牌）：

| 状态 | 标签 | 令牌 |
| --- | --- | --- |
| `pending` | 待执行 | `TEXT_SECTION` |
| `running` | 运行中 | `BRAND` |
| `success` | 已完成 | `SUCCESS_TEXT_SOFT` |
| `failed` | 失败 | `DANGER_TEXT_ALT` |
| `stopped` | 已停止 | `WARN_TEXT_DEEP` |
| `skipped` | 未执行 | `TEXT_FAINT` |
| `completed_today` | 今日已完成 | `SUCCESS_TEXT_SOFT` |

**动态属性选择器**：

| 属性 | 取值 | 作用对象 |
| --- | --- | --- |
| `runState` | `idle` / `busy` / `stop` / `stopping` | 主页执行按钮（stop 红、stopping 黄） |
| `state` | `ready` / `checking` / `error` | `deviceBadge` |
| `state` | `done` / `stale` / `pending` | `settingsCompletionState` |

---

## 7. 配色板（全部令牌）

**品牌绿**：`BRAND_DEEP` `#095b54` · `BRAND_DARK` `#0e6e64` · `BRAND` `#137f73` ·
`BRAND_LIGHT` `#1a9385` · `BRAND_TEXT` `#0c6e63` · `BRAND_TEXT_SOFT` `#146e65` ·
`BRAND_LINE` `#65b3a7` · `BRAND_LINE_HOVER` `#83b9ad` · `BRAND_LINE_SOFT` `#afd5ca` ·
`BRAND_WASH` `#d6ebe5` · `BRAND_WASH_HOVER` `#edf7f4` · `BRAND_WASH_PRESSED` `#dcece7` ·
`BRAND_FILL` `#e3f2ed` · `BRAND_TRACK` `#b9d3ca` · `BRAND_TRACK_HOVER` `#8bbeb2`

**语义色**：运行蓝 `RUN_BLUE` `#1976d2`(+hover/pressed) ·
危险红 `DANGER_SOLID` `#d6534d` / `DANGER_TEXT` `#a34a42` / `DANGER_FILL` `#fbeae8` ·
警告琥珀 `WARN_TEXT` `#95651b` / `WARN_FILL` `#fff3d9` ·
成功绿 `SUCCESS_TEXT` `#126b5f` / `SUCCESS_FILL` `#e0f3ec`

**中性色**：文字 `TEXT_STRONG` `#172b2c` → `TEXT` `#193331` → `TEXT_BUTTON` `#31504d`
→ `TEXT_LABEL` `#49615f` → `TEXT_MUTED` `#718783` → `TEXT_FAINT` `#8a9695`；
底色 `SURFACE_PAGE` `#f3f7f5` → `SURFACE_SECTION` `#e8f0ed` → `SURFACE_SUBTLE` `#f7faf9`
→ `SURFACE_CARD` `#ffffff`；
边框 `BORDER` `#cbdcd6` / `BORDER_SOFT` `#d1e0db` / `BORDER_FAINT` `#dce5e2` /
`BORDER_STRONG` `#b9d3ca`

---

## 8. 滚动条与弹层

| # | objectName | 来源 | 说明 |
| --- | --- | --- | --- |
| 1 | — | `SCROLLBAR_QSS` | 6px 细滚动条，滑柄 `BRAND_TRACK`，隐藏箭头 |
| 2 | `settingsComboPopup` / `settingsComboPopupList` | `COMBO_POPUP_QSS` | 自绘下拉弹层，圆角边框只绘一次 |
| 3 | `taskToast` / `Title` / `Message` | `_TaskToast` | 无边框置顶提示，红系，圆角手绘，5 秒关闭 |
| 4 | `messageBoxAction` | `MESSAGE_BOX_QSS` + `QMessageBox` 子类 | 标准按钮中文化，默认按钮走绿色渐变 |

---

## 9. 设计约定

- **取值单一来源**：颜色/圆角/字号/字重/间距只允许定义在 `ui_tokens.py`。
- **按钮六角色**：新按钮先选角色；确需新外观时先问"能否用现有六个表达"。
- **共用 `objectName` 的样式放公共层**：只被一个页面使用的才留在页面里。
- **一个页面一个文件**：窗口级职责（底色、固定尺寸、页面切换、线程编排）
  不下沉到页面；页面不反向导入 `ui_main`。
- **中文用户体验**：所有用户可见文案、日志标记、错误消息为中文。

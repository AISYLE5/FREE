"""共享的测试配置。

在任何 PySide6 导入之前把 Qt 平台设为 ``offscreen``，
使 GUI 测试既能在无显示器的 CI 机器上运行，也能在本地桌面运行。
"""

from __future__ import annotations

import os

if os.environ.get("QT_QPA_PLATFORM") is None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

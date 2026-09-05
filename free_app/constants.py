from __future__ import annotations

from pathlib import Path

SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 1920
SCREEN_DENSITY = 480

# MuMu Player 12 的默认安装位置，仅在设置未配置且目录内找不到程序时兜底。
DEFAULT_MUMU_DIRECTORY = Path(r"D:\APP\MuMu Player 12")

# 批量执行期间单个任务最多可运行的次数。
MAX_TASK_EXECUTION_COUNT = 10
# 配置的输出文件保留数量的上限。
MAX_OUTPUT_FILE_LIMIT = 1000

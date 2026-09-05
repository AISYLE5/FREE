from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def default_base_directory() -> Path:
    """返回项目根目录——用户配置目录唯一支持的基准目录。"""

    return Path(__file__).resolve().parent.parent


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # 仅 GUI 入口；不解释命令行参数。
    base_directory = default_base_directory()

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .main_window import create_window

    application = QApplication(sys.argv)
    application.setApplicationName("FREE")
    application.setFont(QFont("Microsoft YaHei", 9))
    window = create_window(base_directory)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

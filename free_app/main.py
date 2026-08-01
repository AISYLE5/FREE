from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence


def default_base_directory() -> Path:
    """Return the project root containing the only supported user config directory."""

    return Path(__file__).resolve().parent.parent


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # GUI-only entry; arguments are not interpreted.
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

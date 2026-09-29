"""根目录启动入口：只调用 ``free_app.ui_main.main``。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from free_app.ui_main import main

if __name__ == "__main__":
    raise SystemExit(main())

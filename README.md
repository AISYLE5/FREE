# FREE

FREE 是一个 Windows 桌面工具，通过 MuMu Player 12 的 ADB 控制已登录的 Android App，执行签到、奖励领取和分享任务。
可在「任务管理」中自定义任务流程。

## 使用前准备

- Windows
- Python 3.11 或更高版本
- MuMu Player 12

模拟器参数 `1080x1920`、`480 dpi`。

## 安装与启动

```powershell
git clone https://github.com/AISYLE5/FREE

cd FREE

python -m pip install -e ".[dev]"

python main.py
```

## 项目结构

```text
FREE/
├─ free_app\                      # 核心 Python 包
│  ├─ __init__.py                 # 包初始化
│  ├─ action_schema.py            # 原子动作规格
│  ├─ adb.py                      # ADB 命令封装
│  ├─ app_lifecycle.py            # App 进程生命周期管理
│  ├─ config.py                   # 配置读写
│  ├─ constants.py                # 模拟器尺寸常量
│  ├─ device_ui.py                # 设备侧 UI 层级解析（非界面代码）
│  ├─ engine.py                   # 任务引擎
│  ├─ helpers.py                  # 通用辅助函数
│  ├─ logging_utils.py            # 日志工具
│  ├─ models.py                   # 数据模型与运行状态
│  ├─ mumu.py                     # MuMu CLI 封装
│  ├─ notifications.py            # SMTP 通知
│  ├─ ocr_models.py               # OCR 模型下载与管理
│  ├─ onnx_ocr.py                 # onnxocr 封装
│  ├─ task_runner.py              # 任务执行器
│  ├─ trash.py                    # 文件删除 / 回收站
│  ├─ ui_common.py                # 共享样式 / 中文消息框 / 控件工厂 / 共享控件
│  ├─ ui_main.py                  # 主页 + 主窗口容器 + GUI 入口
│  ├─ ui_settings.py              # 设置页 + 设置对话框（含 OCR 模型下载）
│  ├─ ui_task_manager.py          # 任务管理页 + 任务编辑器 + 查看器 + 动作表单
│  └─ worker.py                   # Qt 工作线程任务执行
├─ tests\                         # 自动化测试
├─ config\                        # 用户配置
├─ logs\                          # 运行日志
├─ screenshots\                   # 动作/失败截图
├─ models\                        # OCR 模型下载目录
├─ main.py                        # 根目录启动入口
├─ .gitignore                     # Git 忽略规则
├─ pyproject.toml                 # 项目配置与依赖
├─ ARCHITECTURE.md                # 架构与模块说明
└─ README.md                      # 本说明
```

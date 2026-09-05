from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any
from xml.etree.ElementTree import ParseError

from .action_schema import (
    COMPOUND_TYPE,
    PRIMITIVE_TYPE_SET,
    describe_action,
    effective_parameters,
)
from .adb import AdbClient, AdbError
from .constants import SCREEN_DENSITY, SCREEN_HEIGHT, SCREEN_WIDTH
from .helpers import (
    LogCallback,
    OcrBoxCallback,
    OcrCallback,
    ProgressCallback,
    clamp_coord,
)
from .models import Action, RunResult, RunStatus, TaskDefinition
from .ui_automation import UiSnapshot, text_matches


class StopRequested(RuntimeError):
    pass


class ActionError(RuntimeError):
    pass


class AutomationEngine:
    """按任务定义驱动 ADB/OCR 执行动作。

    信任边界：所有动作参数在进入引擎前都已经被 ``action_schema.validate_action_params``
    校验（任务加载、试运行、编辑器保存均走这条路径），执行期由 :meth:`_execute`
    统一套用 ``effective_parameters`` 填充默认值；处理器直接读取参数，不再做
    类型宽容或第二套默认值。
    """

    def __init__(
        self,
        adb: AdbClient,
        screenshot_directory: Path,
        log_callback: LogCallback | None = None,
        sleep_function: Callable[[float], None] = time.sleep,
        ocr_client: OcrCallback | None = None,
        ocr_boxes_client: OcrBoxCallback | None = None,
        screenshots_enabled: bool = True,
        progress_callback: ProgressCallback | None = None,
        log_foreground_package: bool = False,
    ):
        self.adb = adb
        self.screenshot_directory = Path(screenshot_directory)
        self.log_callback = log_callback or (lambda _message: None)
        self.sleep_function = sleep_function
        self.ocr_client = ocr_client
        self.ocr_boxes_client = ocr_boxes_client
        self.screenshots_enabled = bool(screenshots_enabled)
        self.progress_callback = progress_callback
        # 记录前台包名每步要额外跑 2-4 次 dumpsys 子进程，默认关闭。
        self.log_foreground_package = bool(log_foreground_package)
        self._run_stamp = ""
        self._stop_event = Event()
        self._current_task: TaskDefinition | None = None
        self._key_screenshots: list[Path] = []
        self._context: dict[str, Any] = {}

    def request_stop(self) -> None:
        self._stop_event.set()
        self._log("已请求停止，当前 ADB 动作完成后将停止。")

    def run(self, task: TaskDefinition) -> RunResult:
        self._current_task = task
        self._key_screenshots = []
        self._context.clear()
        self._run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        total = len(task.actions)
        completed = 0
        current_action = "准备连接设备"
        run_started = time.monotonic()
        try:
            self._log_separator("任务开始")
            self._log_header(f"task={task.id}")
            self._check_stop()
            device = self.adb.select_device(self.adb.serial)
            self._log(f"已连接 MuMu: {device.serial}")
            self._log_screen_info()
            self._log(
                f"任务开始: id={task.id}, name={task.name}, package={task.package}, "
                f"actions={total}"
            )
            for index, action in enumerate(task.actions, start=1):
                self._check_stop()
                current_action = describe_action(action.type, action.parameters)
                started = time.monotonic()
                if self.progress_callback:
                    self.progress_callback(index, total, current_action)
                self._log_header(
                    f"task={task.id}", f"step={index}/{total}", f"action={action.type}"
                )
                self._log(f"[{index}/{total}] 开始: {current_action}")
                display_params = effective_parameters(action.type, action.parameters)
                if action.type in {"stop", "launch"} and task.package:
                    display_params.setdefault("package", task.package)
                self._log(f"动作参数: {self._format_parameters(display_params)}")
                if self.log_foreground_package:
                    self._log_current_package("动作前台")
                self._run_with_retries(action)
                completed = index
                elapsed = time.monotonic() - started
                self._log(
                    f"[{index}/{total}] 完成: {current_action}，耗时 {elapsed:.2f}s"
                )
                if self.log_foreground_package:
                    self._log_current_package("动作完成前台")
            self._log(f"任务完成: {task.name}")
            total_elapsed = time.monotonic() - run_started
            self._log(
                f"任务统计: id={task.id}, status=success, completed={completed}/{total}, "
                f"elapsed={total_elapsed:.2f}s"
            )
            self._log_separator("任务完成")
            return RunResult(
                task.id,
                RunStatus.SUCCESS,
                completed,
                total,
                key_screenshots=tuple(self._key_screenshots),
            )
        except StopRequested:
            total_elapsed = time.monotonic() - run_started
            self._log(
                f"任务已停止: completed={completed}/{total}, current={current_action}, "
                f"elapsed={total_elapsed:.2f}s"
            )
            self._log_separator("任务已停止")
            return RunResult(
                task.id,
                RunStatus.STOPPED,
                completed,
                total,
                failed_step=current_action,
                key_screenshots=tuple(self._key_screenshots),
            )
        except Exception as exc:
            screenshot = self._save_failure_screenshot(task.id, completed + 1)
            message = str(exc)
            total_elapsed = time.monotonic() - run_started
            self._log(
                f"任务失败: task={task.id}, completed={completed}/{total}, "
                f"step={current_action}, elapsed={total_elapsed:.2f}s, "
                f"exception={type(exc).__name__}, error={message}"
            )
            trace = (
                traceback.format_exc().strip().replace("\r", "").replace("\n", "\\n")
            )
            self._log(f"异常追踪: {trace}")
            if screenshot:
                self._log(f"失败截图: {screenshot}")
            self._log_separator("任务失败")
            return RunResult(
                task.id,
                RunStatus.FAILED,
                completed,
                total,
                failed_step=current_action,
                screenshot=screenshot,
                error=message,
                key_screenshots=tuple(self._key_screenshots),
            )
        finally:
            self._current_task = None

    def _run_with_retries(self, action: Action) -> None:
        retries = max(0, int(action.parameters.get("retries", 0)))
        last_error: Exception | None = None
        total_attempts = retries + 1
        for attempt in range(total_attempts):
            self._check_stop()
            attempt_number = attempt + 1
            started = time.monotonic()
            self._log(f"动作尝试 {attempt_number}/{total_attempts}: {action.type}")
            try:
                self._execute(action)
                self._log(
                    f"动作尝试成功: attempt={attempt_number}/{total_attempts}, "
                    f"elapsed={time.monotonic() - started:.2f}s"
                )
                return
            except StopRequested:
                raise
            except Exception as exc:
                last_error = exc
                elapsed = time.monotonic() - started
                self._log(
                    f"动作尝试失败: attempt={attempt_number}/{total_attempts}, "
                    f"elapsed={elapsed:.2f}s, exception={type(exc).__name__}, error={exc}"
                )
                if attempt < retries:
                    self._log(f"动作失败，将重试 ({attempt_number}/{retries}): {exc}")
                    if isinstance(exc, AdbError):
                        try:
                            if self.adb.reconnect():
                                self._log("ADB 设备已重新连接")
                            else:
                                self._log("ADB 设备重连失败，继续按原计划重试")
                        except Exception as reconnect_exc:
                            self._log(f"ADB 设备重连异常: {reconnect_exc}")
                    self._sleep(1.0)
                else:
                    self._log(f"动作最终失败: {exc}")
        if last_error is None:
            raise ActionError("动作失败")
        raise ActionError(str(last_error)) from last_error

    def _execute(self, action: Action) -> None:
        handler = _ACTION_HANDLERS.get(action.type)
        if handler is None:
            raise ActionError(f"不支持的动作类型: {action.type}")
        # 参数默认值的唯一来源是 action_schema 的 ParamSpec；引擎在此统一
        # 填充，处理器只读取已解析的参数，不再各自维护第二套默认值。
        handler(self, effective_parameters(action.type, action.parameters))

    def _execute_stop(self, params: dict[str, Any]) -> None:
        package = self._lifecycle_package(params)
        self._log(f"ADB force-stop: package={package}")
        self.adb.force_stop(package)

    def _execute_launch(self, params: dict[str, Any]) -> None:
        package = self._lifecycle_package(params)
        wait_seconds = float(params["wait_seconds"])
        self._log(f"ADB launch: package={package}, 等待 {wait_seconds:.1f}s")
        attempts = max(1, int(params["launch_attempts"]))
        current = ""
        for attempt in range(1, attempts + 1):
            self._log(f"启动尝试 {attempt}/{attempts}: {package}")
            self.adb.launch(package)
            self._sleep(wait_seconds)
            try:
                current = self.adb.current_package() or ""
            except Exception as exc:
                # 前台探测失败视为启动完成（部分 ADB 环境不支持探测）。
                self._log(f"启动后前台读取失败: {exc}")
                return
            if current == package:
                return
            if attempt < attempts:
                self._log(f"启动后前台为 {current or '未知'}，将重试")
        self._log(
            f"警告: 启动 {attempts} 次后前台仍为 {current or '未知'}，继续后续动作"
        )

    def _execute_wait(self, params: dict[str, Any]) -> None:
        seconds = float(params["seconds"])
        self._log(f"等待页面稳定: {seconds:.1f}s")
        self._sleep(seconds)

    def _execute_back(self, params: dict[str, Any]) -> None:
        self._check_stop()
        self.adb.press_back()
        self._log("ADB press-back")

    def _execute_swipe(self, params: dict[str, Any]) -> None:
        x1, y1 = int(params["x1"]), int(params["y1"])
        x2, y2 = int(params["x2"]), int(params["y2"])
        duration_ms = int(params["duration_ms"])
        if duration_ms < 0:
            raise ActionError("swipe duration_ms must be non-negative")
        self._log(f"ADB swipe: ({x1},{y1}) -> ({x2},{y2}), duration={duration_ms}ms")
        self._check_stop()
        self.adb.swipe(x1, y1, x2, y2, duration_ms)

    def _execute_swipe_until(self, params: dict[str, Any]) -> None:
        result_var = str(params["result_var"])
        max_iterations = max(1, int(params["max_iterations"]))
        if self._swipe_until_detected(params, result_var):
            self._log("滑动直到: 初始已满足，无需滑动")
            return
        for index in range(1, max_iterations + 1):
            self._check_stop()
            self._log(f"滑动直到 第 {index}/{max_iterations} 次")
            self._execute_swipe(params)
            if self._swipe_until_detected(params, result_var):
                self._log("滑动直到: 已检测到目标，结束")
                return
        raise ActionError(
            f"滑动直到超过最大次数 {max_iterations}: {result_var}_found 仍未为 true"
        )

    def _swipe_until_detected(self, params: dict[str, Any], result_var: str) -> bool:
        # swipe_until 与 detect 的定位/轮询参数同名，已解析的参数可直接传给 _detect。
        self._detect(params)
        return bool(self._context.get(f"{result_var}_found"))

    def _execute_capture_screenshot(self, params: dict[str, Any]) -> None:
        label = "screenshot"
        if self.screenshots_enabled:
            task_id = (
                self._current_task.id if self._current_task is not None else "unknown"
            )
            self._capture_key_screenshot(task_id, label)
        else:
            self._log(f"截图动作跳过: {label}（截图已禁用）")

    def _detect(self, params: dict[str, Any]) -> None:
        """检测 OCR/UI 状态，把命中的文本、数量与坐标写入运行上下文。"""

        locate = str(params["locate"])
        target = str(params.get("target", "text"))
        result_var = self._required_string(params, "result_var")
        coord_key = f"{result_var}_coord"
        count_key = f"{result_var}_count"
        timeout = max(0.0, float(params["timeout_seconds"]))
        interval = max(0.1, float(params["interval_seconds"]))
        continue_on_timeout = bool(params["continue_on_timeout"])
        match_mode = str(params.get("match_mode", "exact"))
        targets: list[str] = list(params.get("texts", []))
        if locate == "ui" and target == "resource_id":
            resource_id = self._required_string(params, "resource_id")
        else:
            resource_id = ""

        def probe() -> bool:
            matched, coord, count = self._locate_target(
                locate, target, resource_id, targets, match_mode
            )
            if matched is None:
                return False
            self._write_detect_result(result_var, matched, True, coord, count)
            self._log(
                f"detect 命中: locate={locate}, target={target}, "
                f"result={matched}, result_var={result_var}, "
                f"coord={coord_key}, count={count_key}"
            )
            return True

        if self._poll_until(timeout, interval, probe, "detect"):
            return

        if continue_on_timeout:
            self._write_detect_result(result_var, "", False, None, 0)
            self._log(
                f"detect 超时，写入 none: result_var={result_var}, count={count_key}"
            )
            return
        source = "OCR" if locate == "ocr" else "UI"
        raise ActionError(
            f"{source} 检测超时: targets={targets}, result_var={result_var}"
        )

    def _locate_target(
        self,
        locate: str,
        target: str,
        resource_id: str,
        targets: list[str],
        match_mode: str,
    ) -> tuple[str | None, tuple[int, int] | None, int]:
        """单次定位尝试：返回 (首个命中的目标, 坐标, 匹配数)。"""

        if locate == "ocr":
            recognized_boxes = self._ocr_boxes()
            recognized = [text for text, _points in recognized_boxes]
            self._log(f"detect OCR 候选: {recognized}")
            matched, count = self._match_texts_in_order(recognized, targets, match_mode)
            if matched is None:
                return None, None, count
            return (
                matched,
                self._ocr_box_center(recognized_boxes, matched, match_mode),
                count,
            )
        snapshot = self._snapshot()
        if target == "resource_id":
            node = snapshot.find_resource_id(resource_id)
            count = snapshot.count_resource_matches(resource_id)
            if node:
                return (
                    resource_id,
                    node.bounds.center if node.bounds else None,
                    count,
                )
            return None, None, count
        for value in targets:
            node = snapshot.find_text(value, match_mode=match_mode)
            if node:
                return (
                    value,
                    node.bounds.center if node.bounds else None,
                    snapshot.count_text_matches(targets, match_mode=match_mode),
                )
        return None, None, snapshot.count_text_matches(targets, match_mode=match_mode)

    def _if(self, params: dict[str, Any]) -> None:
        """根据上下文变量是否等于 ``equals``，执行 ``then`` 或 ``else`` 分支动作。"""

        var = self._required_string(params, "var")
        current = self._context.get(var)
        equals = params["equals"]
        condition_met = self._comparable(current) == self._comparable(equals)
        condition_text = f"{current!r} == {equals!r}"
        branch_key = "then" if condition_met else "else"
        branch = params.get(branch_key, [])
        self._log(f"if 分支: {var}, {condition_text}, 执行 {branch_key}")
        self._run_nested_steps(branch, f"if {branch_key}")

    def _tap(self, params: dict[str, Any]) -> None:
        x = int(params["x"])
        y = int(params["y"])
        x, y = self._clamp(x, y)
        self._log(f"ADB tap: ({x}, {y})")
        self.adb.tap(x, y)

    def _click(self, params: dict[str, Any]) -> None:
        locate = str(params["locate"])
        if locate == "coordinate":
            # coordinate 点击无轮询语义，规格里也不含 timeout/interval 参数。
            self._tap(params)
            return
        target = str(params.get("target", "text"))
        timeout = max(0.0, float(params["timeout_seconds"]))
        interval = max(0.1, float(params["interval_seconds"]))
        match_mode = str(params.get("match_mode", "exact"))
        skip_values: list[str] = list(params.get("skip_if_texts") or [])
        values: list[str] = list(params.get("texts", []))
        if locate != "ocr" and target == "resource_id":
            resource_id = self._required_string(params, "resource_id")
        else:
            resource_id = ""

        def probe() -> bool:
            if locate == "ocr":
                boxes = self._ocr_boxes()
                recognized = [text for text, _points in boxes]
                if self._skip_requested(recognized, skip_values, match_mode):
                    return True
                for value in values:
                    point = self._ocr_box_center(boxes, value, match_mode)
                    if point is not None:
                        x, y = point
                        self._log(f"OCR 点击文本: {value} ({x}, {y})")
                        self.adb.tap(x, y)
                        return True
                return False
            snapshot = self._snapshot()
            if skip_values and snapshot.find_any(skip_values, match_mode=match_mode):
                self._log(f"点击前检测到跳过状态: {skip_values}")
                return True
            candidates: list[tuple[str, Any]] = []
            if target == "resource_id":
                candidates.append((resource_id, snapshot.find_resource_id(resource_id)))
            else:
                candidates.extend(
                    (value, snapshot.find_text(value, match_mode=match_mode))
                    for value in values
                )
            for label, node in candidates:
                if node is None:
                    continue
                if not node.enabled:
                    raise ActionError(f"目标控件已禁用: {label}")
                if not node.bounds:
                    raise ActionError(f"目标控件没有可点击区域: {label}")
                x, y = node.bounds.center
                self._log(f"点击: {label} ({x}, {y})")
                self.adb.tap(x, y)
                return True
            return False

        if not self._poll_until(timeout, interval, probe, "click"):
            source = "OCR" if locate == "ocr" else "UI"
            raise ActionError(f"{source} 未找到可点击目标: {params}")

    def _skip_requested(
        self, recognized: list[str], skip_values: list[str], match_mode: str
    ) -> bool:
        """OCR 文本中是否出现任一跳过标记；命中时记录日志。"""

        if not skip_values:
            return False
        if any(
            text_matches(text, skip_text, match_mode)
            for skip_text in skip_values
            for text in recognized
        ):
            self._log(f"点击前检测到跳过状态: {skip_values}")
            return True
        return False

    def _poll_until(
        self,
        timeout: float,
        interval: float,
        probe: Callable[[], bool],
        label: str,
    ) -> bool:
        """按 ``interval`` 轮询 ``probe`` 直到命中或超时。

        probe 抛出的异常按内容去重记录后继续重试；返回 False 表示超时。
        """

        deadline = time.monotonic() + max(0.0, timeout)
        last_error = ""
        while time.monotonic() < deadline:
            self._check_stop()
            try:
                if probe():
                    return True
            except StopRequested:
                raise
            except Exception as exc:
                if str(exc) != last_error:
                    self._log(f"{label} 检测失败，继续重试: {exc}")
                    last_error = str(exc)
            self._sleep(interval)
        return False

    def _run_nested_steps(self, steps: Any, label: str) -> None:
        if not isinstance(steps, list):
            raise ActionError(f"{label} 必须是动作列表")
        for index, step in enumerate(steps, start=1):
            self._check_stop()
            if not isinstance(step, dict):
                raise ActionError(f"{label}[{index}] 必须是动作对象")
            try:
                nested = Action.from_dict(step)
            except ValueError as exc:
                raise ActionError(f"{label}[{index}] 动作无效: {exc}")
            if nested.type == COMPOUND_TYPE:
                raise ActionError(f"{label} 内不支持复合动作: {nested.type}")
            self._log(
                f"{label} 子步骤 {index}/{len(steps)}: "
                f"{describe_action(nested.type, nested.parameters)}"
            )
            self._run_with_retries(nested)

    def _loop_until(self, params: dict[str, Any]) -> None:
        var = self._required_string(params, "var")
        expected = self._comparable(params["equals"])
        max_iterations = max(1, int(params["max_iterations"]))
        steps = params.get("steps", [])
        for index in range(1, max_iterations + 1):
            self._check_stop()
            current = self._context.get(var)
            if self._comparable(current) == expected:
                self._log(
                    f"loop_until 条件满足，结束: {var}={current!r} == {expected!r}"
                )
                return
            self._log(f"loop_until 第 {index}/{max_iterations} 次")
            self._run_nested_steps(steps, f"loop_until[{index}]")
            current = self._context.get(var)
            if self._comparable(current) == expected:
                self._log(
                    f"loop_until 条件满足，结束: {var}={current!r} == {expected!r}"
                )
                return
        raise ActionError(
            f"loop_until 超过最大次数 {max_iterations}: {var} 仍未等于 {expected!r}"
        )

    @staticmethod
    def _comparable(value: Any) -> str:
        """归一化上下文或 ``equals`` 的取值，使 bool/int/float/str 能合理比较。

        ``True``/``False`` 转成 "true"/"false"，整数值保持普通形式，其余值
        转为去首尾空白的字符串，因此 ``equals: "true"`` 与 ``equals: true``
        行为一致，``equals: "3"`` 也能匹配上下文里的整数 3。
        """

        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value)
        return str(value).strip()

    @staticmethod
    def _clamp(x: int, y: int) -> tuple[int, int]:
        return clamp_coord(x, y, SCREEN_WIDTH, SCREEN_HEIGHT)

    @staticmethod
    def _match_texts_in_order(
        recognized: list[str], targets: list[str], match_mode: str
    ) -> tuple[str | None, int]:
        """返回 (首个命中任一识别文本的目标, 匹配数)。

        ``count`` 是命中任一目标的识别文本条数；每条识别文本至多计一次。
        """

        matched_index = len(targets)
        count = 0
        for text in recognized:
            hit = False
            for index, value in enumerate(targets):
                if text_matches(text, value, match_mode):
                    matched_index = min(matched_index, index)
                    hit = True
            if hit:
                count += 1
        matched = targets[matched_index] if matched_index < len(targets) else None
        return matched, count

    def _write_detect_result(
        self,
        result_var: str,
        matched: str | None,
        found: bool,
        coord: tuple[int, int] | None,
        count: int,
    ) -> None:
        """把一次 detect 的结果写入运行上下文。"""

        self._context[result_var] = matched
        self._context[f"{result_var}_found"] = found
        self._context[f"{result_var}_coord"] = coord
        self._context[f"{result_var}_count"] = count

    def _ocr_boxes(self) -> list[tuple[str, list[tuple[int, int]]]]:
        """OCR 识别文本及检测框；纯文本客户端返回空检测框。

        外部 OCR 库的输出不属于本项目校验范围，这里保留唯一的归一化边界。
        """

        if self.ocr_boxes_client is None:
            if self.ocr_client is None:
                raise ActionError("未配置 OCR 客户端")
            return [(text, []) for text in self.ocr_client(self.adb.screenshot())]
        raw = self.ocr_boxes_client(self.adb.screenshot())
        boxes: list[tuple[str, list[tuple[int, int]]]] = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            text, points = item
            text = str(text).strip()
            if not text:
                continue
            normalized: list[tuple[int, int]] = []
            if isinstance(points, (list, tuple)):
                for point in points:
                    if not isinstance(point, (list, tuple)) or len(point) < 2:
                        continue
                    try:
                        x = round(float(point[0]))
                        y = round(float(point[1]))
                    except (TypeError, ValueError):
                        continue
                    x, y = self._clamp(x, y)
                    normalized.append((x, y))
            boxes.append((text, normalized))
        return boxes

    def _ocr_box_center(
        self,
        boxes: list[tuple[str, list[tuple[int, int]]]],
        target: str,
        match_mode: str,
    ) -> tuple[int, int] | None:
        """返回文本与 target 匹配的检测框中心点。"""

        for text, points in boxes:
            if not text_matches(text, target, match_mode) or len(points) < 3:
                continue
            center_x = sum(point[0] for point in points) / len(points)
            center_y = sum(point[1] for point in points) / len(points)
            x, y = self._clamp(round(center_x), round(center_y))
            return (x, y)
        return None

    def _snapshot(self) -> UiSnapshot:
        started = time.monotonic()
        try:
            snapshot = UiSnapshot.from_xml(self.adb.dump_ui())
        except (AdbError, ParseError, ValueError) as exc:
            elapsed = time.monotonic() - started
            self._log(
                f"UI hierarchy 识别失败: exception={type(exc).__name__}: {exc}，"
                f"耗时 {elapsed:.2f}s"
            )
            raise ActionError(f"读取当前页面失败: {exc}") from exc
        elapsed = time.monotonic() - started
        recognized_nodes = tuple(
            node
            for node in snapshot.nodes
            if node.class_name
            or node.bounds
            or node.resource_id
            or node.text
            or node.content_description
        )
        visible = sum(1 for node in recognized_nodes if node.visible)
        clickable = sum(
            1 for node in recognized_nodes if node.visible and node.clickable
        )
        text_nodes = sum(
            1
            for node in recognized_nodes
            if node.visible and (node.text or node.content_description)
        )
        resource_nodes = sum(
            1 for node in recognized_nodes if node.visible and node.resource_id
        )
        self._log(
            "UI hierarchy 识别成功: "
            f"nodes={len(recognized_nodes)}, visible={visible}, clickable={clickable}, "
            f"text={text_nodes}, resource_id={resource_nodes}，耗时 {elapsed:.2f}s"
        )
        return snapshot

    @staticmethod
    def _required_string(params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ActionError(f"动作缺少参数: {key}")
        return value.strip()

    def _lifecycle_package(self, params: dict[str, Any]) -> str:
        package = params.get("package")
        if isinstance(package, str) and package.strip():
            return package.strip()
        if self._current_task is not None:
            return self._current_task.package
        return self._required_string(params, "package")

    def _check_stop(self) -> None:
        if self._stop_event.is_set():
            raise StopRequested()

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            self._check_stop()
            self.sleep_function(min(0.2, max(0, deadline - time.monotonic())))

    def _log_screen_info(self) -> None:
        info = self.adb.screen_info()
        self._log(f"设备规格: {info.width}x{info.height}, density={info.density}dpi")
        if (info.width, info.height, info.density) != (
            SCREEN_WIDTH,
            SCREEN_HEIGHT,
            SCREEN_DENSITY,
        ):
            raise ActionError(
                "模拟器规格不符合固定要求: "
                f"当前={info.width}x{info.height}/{info.density}dpi, "
                f"要求={SCREEN_WIDTH}x{SCREEN_HEIGHT}/{SCREEN_DENSITY}dpi"
            )

    def _log_current_package(self, label: str) -> None:
        try:
            package = self.adb.current_package() or "未知"
        except Exception as exc:
            self._log(f"{label}: 读取失败: {exc}")
            return
        self._log(f"{label}: {package}")

    @staticmethod
    def _format_parameters(parameters: dict[str, Any]) -> str:
        return json.dumps(
            parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def _ensure_screenshot_dir(self) -> None:
        self.screenshot_directory.mkdir(parents=True, exist_ok=True)

    def _write_screenshot(self, filename: str) -> Path:
        """截取当前屏幕并保存到截图目录。"""

        self._ensure_screenshot_dir()
        data = self.adb.screenshot()
        path = self.screenshot_directory / filename
        path.write_bytes(data)
        return path

    def _capture_key_screenshot(self, task_id: str, label: str) -> None:
        """以 ``label`` 命名截取关键页面截图，并记录到运行结果中。"""

        try:
            safe_label = (
                "".join(
                    character if character.isalnum() or character in "-_（）()" else "_"
                    for character in label
                ).strip("_")
                or "key"
            )
            path = self._write_screenshot(
                f"{task_id}_{safe_label}_{self._run_stamp}.png"
            )
            self._key_screenshots.append(path)
            self._log(f"关键页面截图: {path}")
        except Exception as exc:
            self._log(f"关键页面截图失败，不影响动作: {exc}")

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_callback(f"[{timestamp}] {message}")

    def _log_header(self, *parts: str) -> None:
        """输出一行的 task/step/action 日志头。"""

        self._log(" ".join(parts))

    def _log_separator(self, label: str) -> None:
        """输出带当前运行阶段标签的醒目分隔线。"""

        self._log(f"================ {label} ================")

    def _save_failure_screenshot(self, task_id: str, step: int) -> Path | None:
        if not self.screenshots_enabled:
            return None
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            return self._write_screenshot(f"{task_id}_step_{step}_{stamp}.png")
        except Exception as exc:
            self._log(f"无法保存失败截图: {exc}")
            return None


# 原语动作类型到执行器的直接映射（方法引用，无反射）。PRIMITIVE_TYPES 声明
# 支持的类型集合；加载时强制校验两个集合完全一致，漏登记直接启动失败。
_ACTION_HANDLERS: dict[str, Callable[[AutomationEngine, dict[str, Any]], None]] = {
    "stop": AutomationEngine._execute_stop,
    "launch": AutomationEngine._execute_launch,
    "wait": AutomationEngine._execute_wait,
    "back": AutomationEngine._execute_back,
    "swipe": AutomationEngine._execute_swipe,
    "swipe_until": AutomationEngine._execute_swipe_until,
    "click": AutomationEngine._click,
    "detect": AutomationEngine._detect,
    "if": AutomationEngine._if,
    "loop_until": AutomationEngine._loop_until,
    "capture_screenshot": AutomationEngine._execute_capture_screenshot,
}

_UNHANDLED_TYPES = sorted(PRIMITIVE_TYPE_SET - set(_ACTION_HANDLERS))
if _UNHANDLED_TYPES:
    raise RuntimeError(f"动作类型缺少处理器: {_UNHANDLED_TYPES}")

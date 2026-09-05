from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class Action:
    type: str
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        if not isinstance(data, dict):
            raise ValueError("每个动作都必须是对象")
        action_type = data.get("type")
        if not isinstance(action_type, str) or not action_type.strip():
            raise ValueError("每个动作都必须包含非空的 type")
        parameters = {key: value for key, value in data.items() if key != "type"}
        return cls(type=action_type.strip(), parameters=parameters)


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    name: str
    package: str
    actions: tuple[Action, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDefinition:
        task_id = data.get("id")
        name = data.get("name")
        package = data.get("package")
        actions = data.get("actions", [])
        if not (
            isinstance(task_id, str)
            and task_id.strip()
            and isinstance(name, str)
            and name.strip()
            and isinstance(package, str)
            and package.strip()
        ):
            raise ValueError("任务必须包含 id、name 和 package")
        if not isinstance(actions, list):
            raise ValueError("任务 actions 必须是列表")
        unsupported_fields = sorted(
            str(key)
            for key in data
            if key not in {"id", "name", "package", "actions", "description"}
        )
        if unsupported_fields:
            raise ValueError(f"任务不支持字段: {', '.join(unsupported_fields)}")
        task_package = package.strip()
        parsed_actions = tuple(Action.from_dict(action) for action in actions)
        for action in parsed_actions:
            if action.type in {"stop", "launch"} and "package" in action.parameters:
                action_package = action.parameters["package"]
                if (
                    not isinstance(action_package, str)
                    or action_package.strip() != task_package
                ):
                    raise ValueError(
                        f"动作 {action.type} 的 package 必须与任务顶层 package 一致: "
                        f"{action_package!r} != {task_package!r}"
                    )
        return cls(
            id=task_id.strip(),
            name=name.strip(),
            package=task_package,
            actions=parsed_actions,
        )


@dataclass(frozen=True)
class RunResult:
    task_id: str
    status: RunStatus
    completed_steps: int
    total_steps: int
    failed_step: str | None = None
    screenshot: Path | None = None
    error: str | None = None
    key_screenshots: tuple[Path, ...] = ()

    @classmethod
    def failed(
        cls,
        task_id: str,
        total_steps: int,
        *,
        failed_step: str = "准备连接设备",
        error: str | None = None,
    ) -> RunResult:
        """构造一个未完成任何步骤的失败结果。"""
        return cls(
            task_id,
            RunStatus.FAILED,
            0,
            total_steps,
            failed_step=failed_step,
            error=error,
        )

    @classmethod
    def stopped(
        cls,
        task_id: str,
        total_steps: int,
        *,
        failed_step: str = "准备连接设备",
    ) -> RunResult:
        """构造一个未完成任何步骤的停止结果。"""
        return cls(
            task_id,
            RunStatus.STOPPED,
            0,
            total_steps,
            failed_step=failed_step,
        )


@dataclass(frozen=True)
class BatchRunResult:
    """多个任务顺序执行的汇总结果。"""

    status: RunStatus
    results: tuple[RunResult, ...]
    total_tasks: int
    completed_tasks: int
    failed_task: str | None = None
    error: str | None = None

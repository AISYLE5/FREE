from __future__ import annotations

import html
import smtplib
from collections.abc import Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_NOTIFY_ON,
    DEFAULT_SMTP_HOST,
    DEFAULT_SUBJECT_PREFIX,
    TaskFileError,
)
from .helpers import LogCallback, noop_log, unique_existing_paths
from .models import BatchRunResult, RunResult, RunStatus, TaskDefinition

RunSummary = RunResult | BatchRunResult


@dataclass(frozen=True)
class _EmailBlock:
    text: str
    images: tuple[Path, ...] = ()


def send_run_notification(
    settings: dict[str, Any],
    summary: RunSummary,
    tasks: Iterable[TaskDefinition] = (),
    log_callback: LogCallback | None = None,
    config_errors: Iterable[TaskFileError] = (),
) -> bool:
    """发送可选的 SMTP 邮件通知，不改变任务结果。"""

    log = noop_log(log_callback)
    # 信任边界：email_notification 由 config._sanitize_email 统一清洗，
    # 类型与取值范围在这里不再重复校验。
    configuration = settings.get("email_notification") or {}
    if not configuration.get("enabled", False):
        return False

    status = summary.status.value
    notify_on = configuration.get("notify_on", DEFAULT_NOTIFY_ON)
    if not notify_on or status not in notify_on:
        # 空列表 = 所有状态都不通知（用户手写 [] 的意图是"全部不发"）。
        return False

    host = configuration.get("smtp_host", DEFAULT_SMTP_HOST)
    username = configuration.get("smtp_username", "")
    password = configuration.get("smtp_password", "")
    sender = username
    recipients = configuration.get("recipients", [])
    # 消费点断言：清洗层保证 list；契约外脏类型显式抛错，
    # 绝不把 str 静默拆成单字符收件人。
    if not isinstance(recipients, list):
        raise TypeError(f"email recipients 必须是列表，实际为 {recipients!r}")
    if not host or not username or not password or not recipients:
        log("邮件通知跳过：SMTP 配置不完整，请检查 config/settings.json")
        return False

    port = int(configuration.get("smtp_port", 465))
    timeout = max(1.0, float(configuration.get("smtp_timeout_seconds", 20)))

    task_names = {task.id: task.name for task in tasks}
    subject, blocks = _format_message(
        summary,
        task_names,
        configuration,
        config_errors=config_errors,
    )
    attachments = _collect_attachment_paths(blocks)
    message = _build_email(subject, sender, recipients, blocks, attachments, log)

    try:
        security = configuration.get("smtp_security", "ssl")
        if security == "ssl":
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as client:
                client.login(username, password)
                client.send_message(message)
        elif security == "starttls":
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.ehlo()
                client.starttls()
                client.ehlo()
                client.login(username, password)
                client.send_message(message)
        else:
            log(f"邮件通知跳过：不支持的 SMTP 安全模式: {security}")
            return False
    except (OSError, smtplib.SMTPException) as exc:
        log(f"邮件通知发送失败: {exc}")
        return False

    log(f"邮件通知已发送: {', '.join(recipients)}")
    return True


def _collect_attachment_paths(blocks: Iterable[_EmailBlock]) -> list[Path]:
    """按邮件中的呈现顺序收集实际存在的截图。"""

    return unique_existing_paths(path for block in blocks for path in block.images)


def _set_email_headers(
    message: EmailMessage | MIMEMultipart,
    subject: str,
    sender: str,
    recipients: list[str],
) -> None:
    """设置两种邮件形态共用的 Subject/From/To 头。"""

    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)


def _build_email(
    subject: str,
    sender: str,
    recipients: list[str],
    blocks: list[_EmailBlock],
    attachments: list[Path],
    log: LogCallback,
) -> EmailMessage | MIMEMultipart:
    body = _blocks_to_plain_text(blocks)
    if not attachments:
        message = EmailMessage()
        _set_email_headers(message, subject, sender, recipients)
        message.set_content(body)
        return message

    related = MIMEMultipart("related")
    _set_email_headers(related, subject, sender, recipients)
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(body, "plain", "utf-8"))
    alternative.attach(MIMEText(_body_to_html(blocks, attachments), "html", "utf-8"))
    related.attach(alternative)

    for index, path in enumerate(attachments, start=1):
        try:
            data = path.read_bytes()
        except OSError as exc:
            log(f"邮件图片读取失败，已跳过: {path}: {exc}")
            continue
        suffix = path.suffix.lower()
        subtype = (
            "png"
            if suffix == ".png"
            else "jpeg"
            if suffix in {".jpg", ".jpeg"}
            else "png"
        )
        image = MIMEImage(data, _subtype=subtype)
        image.add_header("Content-ID", f"<free-image-{index}>")
        image.add_header("Content-Disposition", "inline", filename=path.name)
        related.attach(image)
    return related


def _blocks_to_plain_text(blocks: Iterable[_EmailBlock]) -> str:
    return "\n".join(block.text for block in blocks if block.text)


def _body_to_html(blocks: list[_EmailBlock], attachments: list[Path]) -> str:
    cid_by_path = {
        path: f"cid:free-image-{index}"
        for index, path in enumerate(attachments, start=1)
    }
    html_parts: list[str] = []
    for block in blocks:
        if block.text:
            html_parts.append(html.escape(block.text))
        for path in block.images:
            cid = cid_by_path.get(Path(path))
            if cid:
                html_parts.append(
                    f'<img src="{cid}" style="max-width:100%;border-radius:8px;">'
                )
    return f"<html><body>{'<br>'.join(html_parts)}</body></html>"


def _format_message(
    summary: RunSummary,
    task_names: dict[str, str],
    configuration: dict[str, Any],
    config_errors: Iterable[TaskFileError] = (),
) -> tuple[str, list[_EmailBlock]]:
    prefix = configuration.get("subject_prefix", DEFAULT_SUBJECT_PREFIX)
    if isinstance(summary, RunResult):
        task_name = task_names.get(summary.task_id, summary.task_id)
        status_line = {
            RunStatus.SUCCESS: f"✅成功：{task_name}",
            RunStatus.FAILED: f"❌失败：{task_name}",
            RunStatus.STOPPED: f"⏹已停止：{task_name}",
        }.get(summary.status, f"{summary.status.value}：{task_name}")
        blocks = [_EmailBlock(status_line)]
        if summary.status == RunStatus.FAILED:
            blocks.append(_failure_detail_block(summary))
        blocks.extend(_task_screenshot_blocks(summary))
        error_block = _config_error_block(config_errors)
        if error_block is not None:
            blocks.append(error_block)
        return prefix, blocks

    results = list(summary.results)
    failed = [result for result in results if result.status == RunStatus.FAILED]
    stopped = [result for result in results if result.status == RunStatus.STOPPED]
    succeeded = [result for result in results if result.status == RunStatus.SUCCESS]
    result_ids = {result.task_id for result in results}
    uncompleted_ids = [task_id for task_id in task_names if task_id not in result_ids]
    error_block = _config_error_block(config_errors)
    has_config_errors = error_block is not None
    blocks = []
    if not results:
        # 批量准备阶段就失败/被停止（设备未就绪等）：把失败原因写进正文，
        # 否则用户只会收到一封只有主题的空邮件。
        if summary.error:
            blocks.append(_EmailBlock(f"❌ 执行失败：{summary.error}"))
        elif summary.status == RunStatus.STOPPED:
            blocks.append(_EmailBlock("⏹ 已停止：批量任务未开始"))
        else:
            blocks.append(_EmailBlock("⚠️ 批量任务未执行（无结果）"))
        if uncompleted_ids:
            blocks.append(_uncompleted_block(uncompleted_ids, task_names))
    elif not failed and not stopped and not has_config_errors:
        blocks.append(_EmailBlock("✅✅全部成功"))
    else:
        if failed:
            blocks.append(
                _EmailBlock(
                    "❌ 失败指令："
                    + "，".join(
                        task_names.get(result.task_id, result.task_id)
                        for result in failed
                    )
                )
            )
        if stopped:
            blocks.append(
                _EmailBlock(
                    "⏹ 已停止指令："
                    + "，".join(
                        task_names.get(result.task_id, result.task_id)
                        for result in stopped
                    )
                )
            )
        if uncompleted_ids:
            blocks.append(_uncompleted_block(uncompleted_ids, task_names))
        if error_block is not None:
            blocks.append(error_block)
    if succeeded:
        success_prefix = (
            "成功指令：" if results and not failed and not stopped else "✅ 成功指令："
        )
        blocks.append(
            _EmailBlock(
                success_prefix
                + "，".join(
                    task_names.get(result.task_id, result.task_id)
                    for result in succeeded
                )
            )
        )

    ordered_results = failed + stopped + succeeded
    for result in ordered_results:
        task_name = task_names.get(result.task_id, result.task_id)
        blocks.append(_EmailBlock(task_name))
        if result.status == RunStatus.FAILED:
            blocks.append(_failure_detail_block(result))
        blocks.extend(_task_screenshot_blocks(result))
    return prefix, blocks


def _uncompleted_block(
    uncompleted_ids: list[str],
    task_names: dict[str, str],
) -> _EmailBlock:
    """渲染批量运行停止或提前结束时未执行到的任务。"""

    return _EmailBlock(
        "⏸ 未完成指令："
        + "，".join(task_names.get(task_id, task_id) for task_id in uncompleted_ids)
    )


def _failure_detail_block(result: RunResult) -> _EmailBlock:
    """渲染邮件通知中简短的失败原因。"""

    reason = result.error or result.failed_step or "未知原因"
    return _EmailBlock(f"失败原因：{reason}")


def _config_error_block(errors: Iterable[TaskFileError]) -> _EmailBlock | None:
    """在邮件正文中渲染被跳过的任务文件列表。"""

    items = list(errors)
    if not items:
        return None
    lines = ["⚠️ 配置错误："]
    lines.extend(f"- {error.path.name}：{error.reason}" for error in items)
    return _EmailBlock("\n".join(lines))


def _task_screenshot_blocks(result: RunResult) -> list[_EmailBlock]:
    paths = unique_existing_paths((*result.key_screenshots, result.screenshot))
    if not paths:
        return []
    return [_EmailBlock("", tuple(paths))]

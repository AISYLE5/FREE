from __future__ import annotations

import smtplib
import html
from dataclasses import dataclass
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Iterable

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
    """Send an optional SMTP notification without changing the task result."""

    log = noop_log(log_callback)
    configuration = settings.get("email_notification", {})
    if not isinstance(configuration, dict) or not bool(configuration.get("enabled", False)):
        return False

    status = summary.status.value
    notify_on = configuration.get("notify_on", DEFAULT_NOTIFY_ON)
    if (
        isinstance(notify_on, list)
        and notify_on
        and status not in {str(item) for item in notify_on}
    ):
        return False

    host = str(configuration.get("smtp_host", DEFAULT_SMTP_HOST)).strip()
    username = str(configuration.get("smtp_username", "")).strip()
    password = str(configuration.get("smtp_password", ""))
    sender = username
    recipients = _split_addresses(configuration.get("recipients", []))
    if not host or not username or not password or not sender or not recipients:
        log("邮件通知跳过：SMTP 配置不完整，请检查 config/settings.json")
        return False

    try:
        port = int(configuration.get("smtp_port", 465))
        timeout = max(1.0, float(configuration.get("timeout_seconds", 20)))
    except (TypeError, ValueError) as exc:
        log(f"邮件通知跳过：SMTP 端口或超时时间无效: {exc}")
        return False

    task_names = {task.id: task.name for task in tasks}
    include_screenshots = str(settings.get("screenshot_save_level", "all")) != "none"
    subject, blocks = _format_message(
        summary,
        task_names,
        configuration,
        include_screenshots=include_screenshots,
        config_errors=config_errors,
    )
    attachments = _collect_attachment_paths(blocks)
    message = _build_email(subject, sender, recipients, blocks, attachments, log)

    try:
        security = str(configuration.get("security", "ssl")).strip().lower()
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
    """Collect existing screenshots in their rendered email order."""

    return unique_existing_paths(path for block in blocks for path in block.images)


def _set_email_headers(
    message: EmailMessage | MIMEMultipart,
    subject: str,
    sender: str,
    recipients: list[str],
) -> None:
    """Set the Subject/From/To headers shared by both email shapes."""

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
        subtype = "png" if suffix == ".png" else "jpeg" if suffix in {".jpg", ".jpeg"} else "png"
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


def _split_addresses(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = str(value).replace(";", ",").split(",")
    return [str(item).strip() for item in raw_values if str(item).strip()]


def _format_message(
    summary: RunSummary,
    task_names: dict[str, str],
    configuration: dict[str, Any],
    include_screenshots: bool = True,
    config_errors: Iterable[TaskFileError] = (),
) -> tuple[str, list[_EmailBlock]]:
    prefix = (
        str(configuration.get("subject_prefix", DEFAULT_SUBJECT_PREFIX)).strip()
        or DEFAULT_SUBJECT_PREFIX
    )
    if isinstance(summary, RunResult):
        task_name = task_names.get(summary.task_id, summary.task_id)
        status_line = {
            RunStatus.SUCCESS: f"✅成功：{task_name}",
            RunStatus.FAILED: f"❌失败：{task_name}",
            RunStatus.STOPPED: f"⏹已停止：{task_name}",
        }.get(summary.status, f"{summary.status.value}：{task_name}")
        blocks = [_EmailBlock(status_line)]
        blocks.extend(_task_screenshot_blocks(summary, include_screenshots))
        error_block = _config_error_block(config_errors)
        if error_block is not None:
            blocks.append(error_block)
        return prefix, blocks

    results = list(summary.results)
    failed = [result for result in results if result.status == RunStatus.FAILED]
    stopped = [result for result in results if result.status == RunStatus.STOPPED]
    succeeded = [result for result in results if result.status == RunStatus.SUCCESS]
    error_block = _config_error_block(config_errors)
    has_config_errors = error_block is not None
    blocks = []
    if results and not failed and not stopped and not has_config_errors:
        blocks.append(_EmailBlock("✅✅全部成功"))
    else:
        if failed:
            blocks.append(
                _EmailBlock(
                    "❌ 失败指令：" + "，".join(
                        task_names.get(result.task_id, result.task_id) for result in failed
                    )
                )
            )
        if stopped:
            blocks.append(
                _EmailBlock(
                    "⏹ 已停止指令：" + "，".join(
                        task_names.get(result.task_id, result.task_id) for result in stopped
                    )
                )
            )
        if error_block is not None:
            blocks.append(error_block)
    if succeeded:
        success_prefix = "成功指令：" if results and not failed and not stopped else "✅ 成功指令："
        blocks.append(
            _EmailBlock(
                success_prefix + "，".join(
                    task_names.get(result.task_id, result.task_id) for result in succeeded
                )
            )
        )

    ordered_results = failed + stopped + succeeded
    for result in ordered_results:
        task_name = task_names.get(result.task_id, result.task_id)
        blocks.append(_EmailBlock(task_name))
        blocks.extend(_task_screenshot_blocks(result, include_screenshots))
    return prefix, blocks


def _config_error_block(errors: Iterable[TaskFileError]) -> _EmailBlock | None:
    """Render the list of skipped task files for the email body."""

    items = list(errors)
    if not items:
        return None
    lines = ["⚠️ 配置错误："]
    lines.extend(f"- {error.path.name}：{error.reason}" for error in items)
    return _EmailBlock("\n".join(lines))


def _task_screenshot_blocks(
    result: RunResult,
    include_screenshots: bool,
) -> list[_EmailBlock]:
    if not include_screenshots:
        return []
    paths = unique_existing_paths((*result.key_screenshots, result.screenshot))
    if not paths:
        return []
    return [_EmailBlock("", tuple(paths))]

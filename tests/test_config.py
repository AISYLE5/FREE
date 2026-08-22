from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from free_app.action_schema import validate_action_params
from free_app.config import (
    _coerce_scalars,
    _expand_action,
    _native_number,
    _substitute,
    ensure_settings_file,
    expand_action_for_run,
    load_action_library,
    load_settings,
    load_task_directory,
    order_tasks,
    save_settings,
)
from free_app.models import Action, TaskDefinition


class ConfigTests(unittest.TestCase):
    @staticmethod
    def _write_task(directory: str | Path, task_id: str, data: dict) -> Path:
        tasks_directory = Path(directory) / "tasks"
        tasks_directory.mkdir(parents=True, exist_ok=True)
        path = tasks_directory / f"{task_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _contains_key(value: object, target: str) -> bool:
        if isinstance(value, dict):
            if target in value:
                return True
            return any(
                ConfigTests._contains_key(item, target)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(ConfigTests._contains_key(item, target) for item in value)
        return False

    def test_project_gitignore_excludes_local_settings(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        gitignore = (project_root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/config/settings.json", gitignore)

    def test_ensure_settings_file_copies_template_only_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            template = base / "settings.example.json"
            settings_path = base / "settings.json"
            template.write_text(json.dumps({"mumu_vmindex": 4}), encoding="utf-8")

            self.assertTrue(ensure_settings_file(settings_path))
            self.assertEqual(load_settings(settings_path)["mumu_vmindex"], 4)

            settings_path.write_text(json.dumps({"mumu_vmindex": 7}), encoding="utf-8")
            self.assertFalse(ensure_settings_file(settings_path))
            self.assertEqual(load_settings(settings_path)["mumu_vmindex"], 7)

    def test_shipped_config_has_no_smtp_credentials(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config_paths = [
            *sorted((project_root / "config" / "tasks").glob("*.json")),
            *sorted((project_root / "config" / "actions").glob("*.json")),
        ]

        for path in config_paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(
                self._contains_key(data, "smtp_password"),
                f"{path.name} must not contain SMTP credentials",
            )

    def test_task_order_keeps_configured_order_and_appends_new_tasks(self) -> None:
        tasks, _errors = load_task_directory(
            Path(__file__).resolve().parents[1] / "config" / "tasks",
            {"qq_group_name": "群聊-转发广告"},
        )

        self.assertEqual(_errors, [])
        ordered = order_tasks(tasks, ["hanserclub", "missing", "bilibili_pts"])

        self.assertEqual(
            [task.id for task in ordered],
            [
                "hanserclub",
                "bilibili_pts",
                "bilibili_exp",
                "bilibili_share",
                "xiaoheihe",
            ],
        )

    def test_bilibili_workflow_is_split_into_three_independent_tasks(self) -> None:
        tasks, _errors = load_task_directory(
            Path(__file__).resolve().parents[1] / "config" / "tasks",
            {"qq_group_name": "群聊-转发广告"},
        )
        task_by_id = {task.id: task for task in tasks}

        self.assertEqual(
            set(task_by_id),
            {
                "bilibili_exp",
                "bilibili_pts",
                "bilibili_share",
                "xiaoheihe",
                "hanserclub",
            },
        )
        exp_types = [action.type for action in task_by_id["bilibili_exp"].actions]
        for expected in ("detect", "click", "if", "wait", "back", "capture_screenshot"):
            self.assertIn(expected, exp_types)
        raw_pts = (
            Path(__file__).resolve().parents[1] / "config" / "tasks" / "bilibili_pts.json"
        ).read_text(encoding="utf-8")
        self.assertIn("loop_until", raw_pts)
        share = task_by_id["bilibili_share"]
        video_click = next(
            action
            for action in share.actions
            if action.type == "click" and action.parameters.get("text") == "视频,%"
        )
        self.assertEqual(video_click.parameters["match_mode"], "fuzzy")
        share_click = next(
            action
            for action in share.actions
            if action.type == "click"
            and action.parameters.get("resource_id") == "tv.danmaku.bili:id/frame_share"
        )
        self.assertEqual(
            share_click.parameters["resource_id"],
            "tv.danmaku.bili:id/frame_share",
        )
        xiaoheihe = task_by_id["xiaoheihe"]
        container_click = next(
            action
            for action in xiaoheihe.actions
            if action.type == "click"
            and action.parameters.get("resource_id")
            == "com.max.xiaoheihe:id/epoxy_model_group_child_container"
        )
        self.assertEqual(
            container_click.parameters["resource_id"],
            "com.max.xiaoheihe:id/epoxy_model_group_child_container",
        )
        self.assertTrue(
            any(
                action.type == "detect" and action.parameters.get("locate") == "ocr"
                for action in task_by_id["bilibili_pts"].actions
            )
        )

    def test_hanserclub_workflow_uses_compact_click_workflow(self) -> None:
        tasks, _errors = load_task_directory(
            Path(__file__).resolve().parents[1] / "config" / "tasks",
            {"qq_group_name": "群聊-转发广告"},
        )
        task = next(task for task in tasks if task.id == "hanserclub")

        launch_action = next(action for action in task.actions if action.type == "launch")
        self.assertEqual(launch_action.parameters["wait_seconds"], 10)
        more_click = next(
            action
            for action in task.actions
            if action.type == "click" and action.parameters.get("text") == "更多"
        )
        self.assertEqual(more_click.parameters["text"], "更多")
        sign_click = next(
            action
            for action in task.actions
            if action.type == "click" and action.parameters.get("text") == "签到"
        )
        self.assertEqual(sign_click.parameters["skip_if_texts"], ["已签到"])
        capture_action = next(
            action
            for action in task.actions
            if action.type == "capture_screenshot"
        )
        self.assertNotIn("label", capture_action.parameters)
        self.assertFalse(any(action.type == "detect" for action in task.actions))
        self.assertEqual(
            [action.type for action in task.actions],
            ["stop", "launch", "click", "click", "wait", "capture_screenshot"],
        )

    def test_non_dict_action_entry_is_reported_as_broken_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            tasks_directory = Path(directory) / "tasks"
            (tasks_directory / "broken_action.json").write_text(
                json.dumps(
                    {
                        "id": "broken_action",
                        "name": "Broken action",
                        "package": "demo.package",
                        "actions": [{"type": "wait", "seconds": 1}, 123],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tasks, errors = load_task_directory(tasks_directory)

        self.assertEqual([task.id for task in tasks], ["demo"])
        self.assertEqual([error.path.name for error in errors], ["broken_action.json"])
        self.assertIn("对象", errors[0].reason)

    def test_task_variables_are_substituted_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "click", "locate": "ui", "target": "text", "text": "${target}"}],
                },
            )
            tasks, errors = load_task_directory(
                Path(directory) / "tasks",
                {"target": "固定群"},
            )
        self.assertEqual(errors, [])
        self.assertEqual(tasks[0].actions[0].parameters["text"], "固定群")

    def test_single_settings_file_keeps_smtp_credentials_and_removes_unknown_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            settings_path = base / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "email_notification": {
                            "enabled": True,
                            "smtp_host": "smtp.qq.com",
                            "smtp_port": 465,
                            "smtp_username": "sender@qq.com",
                            "smtp_password": "authorization-code",
                            "recipients": ["receiver@example.com"],
                        },
                        "stale_field": "emulator-5556",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            settings = load_settings(settings_path)

        self.assertTrue(settings["email_notification"]["enabled"])
        self.assertEqual(settings["email_notification"]["smtp_username"], "sender@qq.com")
        self.assertEqual(settings["email_notification"]["smtp_password"], "authorization-code")
        self.assertEqual(settings["email_notification"]["smtp_host"], "smtp.qq.com")
        self.assertNotIn("stale_field", settings)

    def test_invalid_json_and_top_level_shapes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            invalid_settings = base / "invalid-settings.json"
            invalid_settings.write_text("{", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_settings(invalid_settings)

            object_settings = base / "object-settings.json"
            object_settings.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "配置文件必须是对象"):
                load_settings(object_settings)

            invalid_tasks = base / "tasks"
            invalid_tasks.mkdir()
            (invalid_tasks / "broken.json").write_text(
                json.dumps({"tasks": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "任务目录中没有可用任务"):
                load_task_directory(invalid_tasks)

    def test_invalid_setting_types_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "mumu_vmindex": "bad",
                        "cleanup_delay_seconds": "bad",
                        "log_max_files": "bad",
                        "screenshot_max_files": "bad",
                        "task_execution_counts": {
                            "demo": "bad",
                            "": 4,
                            3: 2,
                            "valid": 99,
                        },
                        "cleanup_mode": "invalid",
                        "email_notification": [],
                    }
                ),
                encoding="utf-8",
            )
            settings = load_settings(path)

        self.assertEqual(settings["mumu_vmindex"], 0)
        self.assertEqual(settings["cleanup_delay_seconds"], 3)
        self.assertEqual(settings["log_max_files"], -1)
        self.assertEqual(settings["screenshot_max_files"], -1)
        self.assertEqual(settings["task_execution_counts"]["valid"], 10)
        self.assertNotIn("demo", settings["task_execution_counts"])
        self.assertEqual(settings["task_execution_counts"]["3"], 2)
        self.assertNotIn("hanserclub", settings["task_execution_counts"])
        self.assertEqual(settings["cleanup_mode"], "recycle")
        self.assertIsInstance(settings["email_notification"], dict)

    def test_output_settings_levels_are_dropped_and_retention_kept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            settings_path = config_directory / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "screenshot_save_level": "all",
                        "log_output_level": "all",
                        "log_max_files": 5,
                    }
                ),
                encoding="utf-8",
            )
            settings = load_settings(settings_path)
        self.assertEqual(settings["log_max_files"], 5)
        self.assertNotIn("screenshot_save_level", settings)
        self.assertNotIn("log_output_level", settings)

    def test_unknown_log_fields_are_removed(self) -> None:
        for stale_value in ("summary", "none"):
            with self.subTest(stale_value=stale_value):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "settings.json"
                    path.write_text(
                        json.dumps({"stale_log_field": stale_value}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    settings = load_settings(path)

                self.assertEqual(settings["log_max_files"], -1)
                self.assertNotIn("log_output_level", settings)
                self.assertNotIn("stale_log_field", settings)

    def test_log_max_files_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"stale_log_field": "none", "log_max_files": 5}),
                encoding="utf-8",
            )
            settings = load_settings(path)

        self.assertEqual(settings["log_max_files"], 5)
        self.assertNotIn("log_output_level", settings)

    def test_unknown_log_enabled_is_removed(self) -> None:
        for value in (False, True, "false"):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "settings.json"
                    path.write_text(
                        json.dumps({"stale_enabled": value}),
                        encoding="utf-8",
                    )
                    settings = load_settings(path)

                self.assertEqual(settings["log_max_files"], -1)
                self.assertNotIn("stale_enabled", settings)

    def test_unknown_smtp_fields_are_removed(self) -> None:
        for unsupported_security in ("invalid", "none"):
            with self.subTest(security=unsupported_security):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "settings.json"
                    path.write_text(
                        json.dumps(
                            {
                                "email_notification": {
                                    "stale_field": "first@example.com; second@example.com",
                                    "security": unsupported_security,
                                }
                            }
                        ),
                        encoding="utf-8",
                    )
                    settings = load_settings(path)

                email = settings["email_notification"]
                self.assertEqual(email["recipients"], [])
                self.assertEqual(email["security"], "ssl")
                self.assertNotIn("stale_field", email)

    def test_unknown_screenshot_flag_is_removed(self) -> None:
        for unsupported_value in (True, False):
            with self.subTest(value=unsupported_value):
                with tempfile.TemporaryDirectory() as directory:
                    config_directory = Path(directory) / "config"
                    config_directory.mkdir()
                    settings_path = config_directory / "settings.json"
                    settings_path.write_text(
                        json.dumps(
                            {"stale_screenshot_flag": unsupported_value},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    settings = load_settings(settings_path)
                self.assertNotIn("screenshot_save_level", settings)
                self.assertNotIn("stale_screenshot_flag", settings)

    def test_screenshot_level_is_dropped_from_config(self) -> None:
        for stale_level in ("all", "key", "everything"):
            with self.subTest(stale_level=stale_level):
                with tempfile.TemporaryDirectory() as directory:
                    config_directory = Path(directory) / "config"
                    config_directory.mkdir()
                    settings_path = config_directory / "settings.json"
                    settings_path.write_text(
                        json.dumps({"screenshot_save_level": stale_level}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    settings = load_settings(settings_path)
                self.assertNotIn("screenshot_save_level", settings)
                self.assertEqual(settings["screenshot_max_files"], -1)

    def test_mumu_directory_is_derived_from_adb_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            settings_path = config_directory / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {"adb_path": r"D:\APP\MuMu Player 12\nx_main\adb.exe"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            settings = load_settings(settings_path)
        self.assertEqual(settings["mumu_directory"], r"D:\APP\MuMu Player 12")

    def test_file_retention_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            settings_path = config_directory / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "log_max_files": 5,
                        "screenshot_max_files": 5,
                        "cleanup_mode": "recycle",
                    }
                ),
                encoding="utf-8",
            )
            settings = load_settings(settings_path)
        self.assertEqual(settings["log_max_files"], 5)
        self.assertEqual(settings["screenshot_max_files"], 5)
        self.assertEqual(settings["cleanup_mode"], "recycle")

    def test_screenshot_max_files_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            settings_path = config_directory / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "screenshot_max_files": -5,
                        "screenshot_save_level": "none",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            settings = load_settings(settings_path)
        self.assertEqual(settings["screenshot_max_files"], -1)
        self.assertNotIn("screenshot_save_level", settings)

        with tempfile.TemporaryDirectory() as directory:
            config_directory = Path(directory) / "config"
            config_directory.mkdir()
            settings_path = config_directory / "settings.json"
            settings_path.write_text(
                json.dumps({"screenshot_max_files": 0}, ensure_ascii=False),
                encoding="utf-8",
            )
            settings = load_settings(settings_path)
        self.assertEqual(settings["screenshot_max_files"], 0)
        self.assertNotIn("screenshot_save_level", settings)

    def test_missing_settings_is_rejected_without_bundled_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_settings(Path(directory) / "missing" / "settings.json")

    def test_ocr_download_source_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps({"ocr_download_source": "modelscope"}),
                encoding="utf-8",
            )
            self.assertEqual(load_settings(path)["ocr_download_source"], "modelscope")

            path.write_text(
                json.dumps({"ocr_download_source": "invalid-mirror"}),
                encoding="utf-8",
            )
            self.assertEqual(load_settings(path)["ocr_download_source"], "auto")

            path.write_text(json.dumps({}), encoding="utf-8")
            self.assertEqual(load_settings(path)["ocr_download_source"], "auto")

    def test_none_and_empty_fields_use_documented_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "log_directory": None,
                        "screenshot_directory": "",
                        "cleanup_after_task": None,
                        "cleanup_delay_seconds": "",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            settings = load_settings(path)

        self.assertEqual(settings["log_directory"], "logs")
        self.assertEqual(settings["screenshot_directory"], "screenshots")
        self.assertTrue(settings["cleanup_after_task"])
        self.assertEqual(settings["cleanup_delay_seconds"], 3)

    def test_missing_tasks_file_is_rejected_without_bundled_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_task_directory(Path(directory) / "missing" / "tasks")

    def test_project_tasks_are_loaded_from_project_config(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        variables = {"qq_group_name": "test-group"}
        configured, config_errors = load_task_directory(project_root / "config" / "tasks", variables)
        self.assertEqual(config_errors, [])
        self.assertEqual(
            {task.id for task in configured},
            {"hanserclub", "xiaoheihe", "bilibili_exp", "bilibili_pts", "bilibili_share"},
        )
        self.assertEqual(configured[0].actions[0].type, "stop")

    def test_bilibili_exp_uses_compound_ocr_claim_or_watch(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        raw = json.loads(
            (project_root / "config" / "tasks" / "bilibili_exp.json").read_text(encoding="utf-8")
        )
        compound = next(
            action for action in raw["actions"] if action.get("name") == "ocr_claim_or_watch"
        )
        self.assertEqual(compound["type"], "compound")
        self.assertTrue((project_root / "config" / "actions" / "ocr_claim_or_watch.json").exists())

        configured, config_errors = load_task_directory(
            project_root / "config" / "tasks",
            {"qq_group_name": "test-group"},
        )
        self.assertEqual(config_errors, [])
        task = next(task for task in configured if task.id == "bilibili_exp")
        ocr_action = next(
            action
            for action in task.actions
            if action.type == "detect"
            and action.parameters.get("result_var") == "ocr_state"
        )
        self.assertEqual(ocr_action.parameters["match_mode"], "exact")
        self.assertEqual(ocr_action.parameters["result_var"], "ocr_state")
        watch_if = next(
            action
            for action in task.actions
            if action.type == "if" and action.parameters.get("equals") == "去观看"
        )
        watch_click = next(
            step
            for step in watch_if.parameters["then"]
            if step.get("text") == "%看至第%集%"
        )
        self.assertEqual(watch_click["match_mode"], "fuzzy")
        self.assertIn(
            "if",
            [action.type for action in task.actions],
        )

    def test_shipped_action_library_steps_are_valid_primitives(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        for path in sorted((project_root / "config" / "actions").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for index, step in enumerate(data.get("steps", []), start=1):
                params = {
                    key: value
                    for key, value in step.items()
                    if key != "type"
                }
                errors = validate_action_params(step.get("type"), params)
                self.assertEqual(
                    errors,
                    [],
                    f"{path.name} step {index}: {errors}",
                )

    def test_ocr_claim_or_watch_has_skip_and_post_watch_claim_branches(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        library = load_action_library(project_root / "config" / "actions")
        actions, error = expand_action_for_run(
            {"type": "compound", "name": "ocr_claim_or_watch"},
            library,
        )

        self.assertIsNone(error)
        claim_if = next(
            action
            for action in actions
            if action.type == "if" and action.parameters.get("equals") == "领取"
        )
        self.assertNotIn("then", claim_if.parameters)
        watch_if = next(
            action
            for action in actions
            if action.type == "if" and action.parameters.get("equals") == "去观看"
        )
        self.assertTrue(
            any(
                step.get("type") == "detect"
                and step.get("result_var") == "claim_state"
                for step in watch_if.parameters.get("then", [])
            )
        )
        self.assertFalse(
            any(
                action.type == "if"
                and action.parameters.get("equals") == "已领取, 已领取√,领取"
                for action in actions
            )
        )

    def test_action_level_package_must_match_task_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "stop", "package": "other.package"}],
                },
            )
            with self.assertRaisesRegex(ValueError, "package 必须与任务顶层"):
                load_task_directory(Path(directory) / "tasks")

    def test_launch_action_wait_seconds_rejects_negative_or_non_numeric(self) -> None:
        for bad_value in (-1, "abc"):
            with self.subTest(bad_value=bad_value):
                with tempfile.TemporaryDirectory() as directory:
                    self._write_task(
                        directory,
                        "demo",
                        {
                            "id": "demo",
                            "name": "Demo",
                            "package": "demo.package",
                            "actions": [{"type": "launch", "wait_seconds": bad_value}],
                        },
                    )
                    with self.assertRaises(ValueError):
                        load_task_directory(Path(directory) / "tasks")

    def test_unknown_task_top_level_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "unknown_task",
                {
                    "id": "unknown_task",
                    "name": "Unknown task",
                    "package": "demo.package",
                    "stale_task_field": 20,
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            with self.assertRaisesRegex(ValueError, "任务不支持字段"):
                load_task_directory(Path(directory) / "tasks")

    def test_mumu_vmindex_is_loaded_as_integer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"mumu_vmindex": 3}), encoding="utf-8")
            settings = load_settings(path)
            missing_path = Path(directory) / "missing" / "settings.json"

        self.assertEqual(settings["mumu_vmindex"], 3)
        self.assertIsInstance(settings["mumu_vmindex"], int)
        with self.assertRaises(FileNotFoundError):
            load_settings(missing_path)

    def test_unknown_global_retry_field_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"stale_retry": 99}), encoding="utf-8")
            settings = load_settings(path)

        self.assertEqual(settings["task_execution_counts"], {})
        self.assertNotIn("stale_retry", settings)

    def test_unknown_counts_field_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "stale_counts": {
                            "demo": 3,
                            "disabled": 0,
                            "too_large": 99,
                        }
                    }
                ),
                encoding="utf-8",
            )
            settings = load_settings(path)

        self.assertEqual(settings["task_execution_counts"], {})
        self.assertNotIn("stale_counts", settings)

    def test_missing_settings_does_not_create_new_user_retry_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                load_settings(Path(directory) / "missing.json")

    def test_unknown_settings_are_dropped_without_bundled_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"custom_value": "from-config"}), encoding="utf-8")
            settings = load_settings(path)

        self.assertNotIn("custom_value", settings)
        self.assertNotIn("cleanup_packages", settings)
        self.assertNotIn("task_order", settings)

    def test_unknown_global_retry_setting_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"stale_retry": 3}), encoding="utf-8")
            settings = load_settings(path)

        self.assertNotIn("stale_retry", settings)
        self.assertEqual(settings["task_execution_counts"], {})

    def test_broken_task_file_is_skipped_and_good_tasks_survive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            tasks_directory = Path(directory) / "tasks"
            (tasks_directory / "broken.json").write_text("{", encoding="utf-8")
            (tasks_directory / "renamed.json").write_text(
                json.dumps(
                    {
                        "id": "other",
                        "name": "Other",
                        "package": "demo.package",
                        "actions": [{"type": "wait", "seconds": 1}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tasks, errors = load_task_directory(tasks_directory)

        self.assertEqual([task.id for task in tasks], ["demo"])
        self.assertEqual(
            [error.path.name for error in errors],
            ["broken.json", "renamed.json"],
        )
        self.assertIn("文件名必须与任务 id 一致", errors[1].reason)

    def test_duplicate_ids_skip_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            self._write_task(
                directory,
                "demo_backup",
                {
                    "id": "demo",
                    "name": "Demo Copy",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            self._write_task(
                directory,
                "valid",
                {
                    "id": "valid",
                    "name": "Valid",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            tasks, errors = load_task_directory(Path(directory) / "tasks")

        self.assertEqual([task.id for task in tasks], ["valid"])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("任务 id 重复" in error.reason for error in errors))

    def test_empty_actions_marks_file_broken(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [],
                },
            )
            self._write_task(
                directory,
                "valid",
                {
                    "id": "valid",
                    "name": "Valid",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            tasks, errors = load_task_directory(Path(directory) / "tasks")

        self.assertEqual([task.id for task in tasks], ["valid"])
        self.assertEqual(len(errors), 1)
        self.assertIn("actions 不能为空", errors[0].reason)

    def test_empty_task_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks_directory = Path(directory) / "tasks"
            tasks_directory.mkdir()
            with self.assertRaisesRegex(ValueError, "没有任务文件"):
                load_task_directory(tasks_directory)

    def test_non_json_files_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tasks_directory = Path(directory) / "tasks"
            tasks_directory.mkdir()
            (tasks_directory / "notes.txt").write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "没有任务文件"):
                load_task_directory(tasks_directory)

    def test_save_settings_creates_parent_and_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            save_settings(path, {"log_max_files": 7})

            loaded = load_settings(path)

        self.assertEqual(loaded["log_max_files"], 7)

    def test_top_level_task_file_that_is_not_an_object_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_task(
                directory,
                "demo",
                {
                    "id": "demo",
                    "name": "Demo",
                    "package": "demo.package",
                    "actions": [{"type": "wait", "seconds": 1}],
                },
            )
            tasks_directory = Path(directory) / "tasks"
            (tasks_directory / "array.json").write_text("[]", encoding="utf-8")
            tasks, errors = load_task_directory(tasks_directory)

        self.assertEqual([task.id for task in tasks], ["demo"])
        self.assertEqual([error.path.name for error in errors], ["array.json"])

    def test_load_action_library_reports_broken_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            actions_directory = Path(directory) / "actions"
            actions_directory.mkdir()
            (actions_directory / "bad_json.json").write_text("{", encoding="utf-8")
            (actions_directory / "not_object.json").write_text("[]", encoding="utf-8")
            (actions_directory / "no_name.json").write_text(
                json.dumps({"steps": [{"type": "wait", "seconds": 1}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (actions_directory / "empty_steps.json").write_text(
                json.dumps({"name": "empty", "steps": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            (actions_directory / "bad_params.json").write_text(
                json.dumps(
                    {"name": "bad_params", "params": [1], "steps": [{"type": "wait", "seconds": 1}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (actions_directory / "valid.json").write_text(
                json.dumps(
                    {"name": "valid", "params": [], "steps": [{"type": "wait", "seconds": 1}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            library = load_action_library(actions_directory)

        self.assertIn("valid", library)
        for broken_name in ("bad_json", "not_object", "no_name", "empty_steps", "bad_params"):
            self.assertIn("error", library[broken_name])

    def test_coerce_scalars_restores_primitive_types(self) -> None:
        coerced = _coerce_scalars(
            {
                "flag": "true",
                "disabled": "false",
                "count": "12",
                "ratio": "1.5",
                "text": "abc",
                "items": ["3", "false"],
            }
        )

        self.assertIs(coerced["flag"], True)
        self.assertIs(coerced["disabled"], False)
        self.assertEqual(coerced["count"], 12)
        self.assertEqual(coerced["ratio"], 1.5)
        self.assertEqual(coerced["text"], "abc")
        self.assertEqual(coerced["items"], [3, False])

    def test_native_number_rejects_non_finite_and_fractional_integers(self) -> None:
        self.assertEqual(_native_number(float("inf"), 1), 1)
        self.assertEqual(_native_number(1.5, 1, integer=True), 1)

    def test_expand_action_reports_missing_and_broken_compounds(self) -> None:
        action = Action("compound", {"name": ""})
        _actions, error = _expand_action(action, {}, {}, ())
        self.assertTrue(error)

        action = Action("compound", {"name": "missing"})
        _actions, error = _expand_action(action, {}, {}, ())
        self.assertTrue(error)

        action = Action("compound", {"name": "broken"})
        _actions, error = _expand_action(action, {"broken": {"error": "boom"}}, {}, ())
        self.assertTrue(error)

    def test_expand_action_detects_cycle_and_invalid_steps(self) -> None:
        library = {
            "cycle": {
                "name": "cycle",
                "params": [],
                "steps": [{"type": "compound", "name": "cycle"}],
            },
            "bad_step": {
                "name": "bad_step",
                "params": [],
                "steps": [123],
            },
            "bad_action": {
                "name": "bad_action",
                "params": [],
                "steps": [{"type": "wait", "seconds": "bad"}],
            },
            "bad_params": {
                "name": "bad_params",
                "params": [],
                "steps": [{"type": "wait", "seconds": 1}],
            },
        }
        cycle = Action("compound", {"name": "cycle"})
        _actions, error = _expand_action(cycle, library, {}, ())
        self.assertTrue(error)

        bad_step = Action("compound", {"name": "bad_step"})
        _actions, error = _expand_action(bad_step, library, {}, ())
        self.assertTrue(error)

        bad_action = Action("compound", {"name": "bad_action"})
        _actions, error = _expand_action(bad_action, library, {}, ())
        self.assertTrue(error)

        bad_params = Action("compound", {"name": "bad_params", "params": "not-dict"})
        _actions, error = _expand_action(bad_params, library, {}, ())
        self.assertTrue(error)

    def test_expand_action_applies_outer_retries_to_steps(self) -> None:
        library = {
            "demo": {
                "name": "demo",
                "params": [],
                "steps": [{"type": "wait", "seconds": 1}],
            }
        }
        action = Action("compound", {"name": "demo", "retries": 3})

        actions, error = _expand_action(action, library, {}, ())

        self.assertIsNone(error)
        self.assertEqual(actions[0].parameters["retries"], 3)

    def test_expand_action_for_run_returns_error_for_invalid_action(self) -> None:
        actions, error = expand_action_for_run(
            {"type": "wait", "seconds": "bad"},
            {},
        )

        self.assertEqual(actions, [])
        self.assertTrue(error)

    def test_order_tasks_handles_duplicate_and_non_string_order(self) -> None:
        tasks = [
            TaskDefinition("a", "A", "a.package", ()),
            TaskDefinition("b", "B", "b.package", ()),
        ]

        ordered = order_tasks(tasks, ["a", "a", "missing", 5, "b"])

        self.assertEqual([task.id for task in ordered], ["a", "b"])

    def test_substitute_expands_list_and_embedded_placeholders(self) -> None:
        result = _substitute(
            ["${items}", "prefix-${name}", "plain"],
            {"items": ["a", "b"], "name": "demo"},
        )

        self.assertEqual(result, ["a", "b", "prefix-demo", "plain"])

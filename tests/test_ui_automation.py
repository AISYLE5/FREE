from __future__ import annotations

import unittest

from free_app.ui_automation import UiSnapshot, parse_bounds


class UiAutomationTests(unittest.TestCase):
    def test_parse_bounds_and_find_disabled_node(self) -> None:
        self.assertEqual(parse_bounds("[10,20][110,80]").center, (60, 50))
        snapshot = UiSnapshot.from_xml(
            """
            <hierarchy>
              <node text="领取" class="android.widget.Button" enabled="false"
                    clickable="false" visible-to-user="true" bounds="[10,20][110,80]" />
              <node content-desc="分享" resource-id="com.example:id/share" clickable="true"
                    enabled="true" visible-to-user="true" bounds="[100,100][200,200]" />
            </hierarchy>
            """
        )
        claim = snapshot.find_text("领取")
        share = snapshot.find_resource_id("com.example:id/share")
        self.assertIsNotNone(claim)
        self.assertFalse(claim.enabled)
        self.assertEqual(share.content_description, "分享")

    def test_qualified_resource_id_requires_full_exact_match(self) -> None:
        snapshot = UiSnapshot.from_xml(
            """
            <hierarchy>
              <node resource-id="other:tv.danmaku.bili:id/frame_share"
                    visible-to-user="true" bounds="[0,0][100,100]" />
              <node resource-id="tv.danmaku.bili:id/frame_share"
                    visible-to-user="true" bounds="[100,0][200,100]" />
            </hierarchy>
            """
        )

        node = snapshot.find_resource_id("tv.danmaku.bili:id/frame_share")

        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.bounds.center, (150, 50))

        misleading = UiSnapshot.from_xml(
            """
            <hierarchy>
              <node resource-id="other:tv.danmaku.bili:id/frame_share"
                    visible-to-user="true" bounds="[0,0][100,100]" />
            </hierarchy>
            """
        )
        self.assertIsNone(misleading.find_resource_id("tv.danmaku.bili:id/frame_share"))

    def test_global_text_matching_is_exact_and_supports_multiline_labels(self) -> None:
        snapshot = UiSnapshot.from_xml(
            """
            <hierarchy>
              <node content-desc="签到活动" clickable="true" enabled="true"
                    visible-to-user="true" bounds="[48,1500][242,1572]" />
              <node content-desc="今日未签到&#10;已连续签到 148 天&#10;签到" clickable="true"
                    enabled="true" visible-to-user="true" bounds="[48,1620][1032,1872]" />
            </hierarchy>
            """
        )

        signin = snapshot.find_any(["签到"])

        self.assertIsNotNone(signin)
        assert signin is not None
        self.assertEqual(signin.bounds.center, (540, 1746))
        self.assertEqual(snapshot.find_text("签到活动").bounds.center, (145, 1536))
        self.assertIsNone(snapshot.find_any(["签到活"]))
        self.assertIsNone(snapshot.find_text("未签到"))
        self.assertEqual(snapshot.count_text_matches(["签到"]), 1)

    def test_fuzzy_text_matching_supports_substring_and_wildcards(self) -> None:
        snapshot = UiSnapshot.from_xml(
            """
            <hierarchy>
              <node content-desc="签到活动" clickable="true" enabled="true"
                    visible-to-user="true" bounds="[48,1500][242,1572]" />
              <node content-desc="今日未签到&#10;已连续签到 148 天&#10;签到" clickable="true"
                    enabled="true" visible-to-user="true" bounds="[48,1620][1032,1872]" />
              <node text="每日签到福利" visible-to-user="true" bounds="[0,0][100,80]" />
            </hierarchy>
            """
        )

        self.assertIsNotNone(snapshot.find_any(["签到活"], match_mode="fuzzy"))
        self.assertIsNotNone(snapshot.find_any(["签到活_"], match_mode="fuzzy"))
        self.assertIsNotNone(snapshot.find_any(["%签到%"], match_mode="fuzzy"))
        self.assertIsNone(snapshot.find_any(["签到活"], match_mode="exact"))
        self.assertEqual(snapshot.count_text_matches(["%签到%"], match_mode="fuzzy"), 3)

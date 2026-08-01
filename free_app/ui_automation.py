from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True)
class UiNode:
    text: str
    content_description: str
    resource_id: str
    class_name: str
    bounds: Bounds | None
    clickable: bool
    enabled: bool
    visible: bool

    @property
    def label(self) -> str:
        return self.text or self.content_description


_BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def text_matches(text: str, target: str, match_mode: str) -> bool:
    """Match a UI label using exact or fuzzy text recognition."""

    if match_mode == "fuzzy":
        return text_matches_fuzzy(text, target)
    if match_mode == "exact":
        return text_matches_exact(text, target)
    raise ValueError(f"不支持的文本匹配方式: {match_mode}")


def text_matches_exact(text: str, target: str) -> bool:
    """Match a complete label or one complete line of a multi-line label."""

    target = target.strip()
    if not target:
        return False
    value = text.strip()
    if not value:
        return False
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return target == value or target in lines


def text_matches_fuzzy(text: str, target: str) -> bool:
    """Match a label with substring or % / _ wildcard fuzzy recognition.

    % matches any run of characters and _ matches exactly one character.
    A fuzzy target without wildcards is treated as a case-insensitive
    substring match.
    """

    target = target.strip()
    if not target:
        return False
    value = text.strip()
    if not value:
        return False
    if "%" not in target and "_" not in target:
        return target.lower() in value.lower()
    expression = "".join(
        ".*" if part == "%" else "." if part == "_" else re.escape(part)
        for part in re.split(r"([%_])", target)
    )
    regex = re.compile(expression, flags=re.DOTALL | re.IGNORECASE)
    return regex.fullmatch(value) is not None or any(
        regex.fullmatch(line) for line in value.splitlines() if line.strip()
    )


def parse_bounds(value: str) -> Bounds | None:
    match = _BOUNDS_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    return Bounds(*(int(part) for part in match.groups()))


def _bool_attribute(element: ET.Element, name: str, default: bool = False) -> bool:
    value = element.attrib.get(name)
    return value.lower() == "true" if value is not None else default


def _matches_resource_id(node: UiNode, value: str) -> bool:
    """Return whether a visible node matches the requested resource id."""

    return node.visible and node.resource_id == value


class UiSnapshot:
    def __init__(self, nodes: Iterable[UiNode]):
        self.nodes = tuple(nodes)

    @classmethod
    def from_xml(cls, xml: str | bytes) -> "UiSnapshot":
        root = ET.fromstring(xml)
        nodes: list[UiNode] = []
        for element in root.iter():
            nodes.append(
                UiNode(
                    text=element.attrib.get("text", ""),
                    content_description=element.attrib.get("content-desc", ""),
                    resource_id=element.attrib.get("resource-id", ""),
                    class_name=element.attrib.get("class", ""),
                    bounds=parse_bounds(element.attrib.get("bounds", "")),
                    clickable=_bool_attribute(element, "clickable"),
                    enabled=_bool_attribute(element, "enabled", True),
                    visible=_bool_attribute(element, "visible-to-user", True),
                )
            )
        return cls(nodes)

    def find_text(self, value: str, match_mode: str = "exact") -> UiNode | None:
        value = value.strip()
        if not value:
            return None
        for node in self.nodes:
            if node.visible and (
                text_matches(node.text, value, match_mode)
                or text_matches(node.content_description, value, match_mode)
            ):
                return node
        return None

    def find_resource_id(self, value: str) -> UiNode | None:
        value = value.strip()
        for node in self.nodes:
            if _matches_resource_id(node, value):
                return node
        return None

    def count_resource_matches(self, value: str) -> int:
        value = value.strip()
        if not value:
            return 0
        return sum(
            1
            for node in self.nodes
            if _matches_resource_id(node, value)
        )

    def find_any(self, values: Iterable[str], match_mode: str = "exact") -> UiNode | None:
        """Return the first visible node matching one requested label."""

        for value in values:
            node = self.find_text(value, match_mode)
            if node:
                return node
        return None

    def count_text_matches(self, values: Iterable[str], match_mode: str = "exact") -> int:
        """Count distinct visible nodes matching the requested labels."""

        requested = [str(value).strip() for value in values if str(value).strip()]
        return sum(
            1
            for node in self.nodes
            if node.visible
            and any(
                text_matches(node.text, value, match_mode)
                or text_matches(node.content_description, value, match_mode)
                for value in requested
            )
        )

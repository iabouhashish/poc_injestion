"""Tests for extract_thinking() and the updated extract_json() CoT stripping."""
from __future__ import annotations

import json
import pytest

from src.utils import extract_thinking, extract_json


class TestExtractThinking:
    def test_returns_content_when_block_present(self):
        raw = "<thinking>This is the reasoning.</thinking>"
        assert extract_thinking(raw) == "This is the reasoning."

    def test_returns_none_when_absent(self):
        assert extract_thinking("no thinking block here") is None

    def test_strips_surrounding_whitespace(self):
        raw = "<thinking>  padded  </thinking>"
        assert extract_thinking(raw) == "padded"

    def test_handles_multiline_block(self):
        raw = "<thinking>\ndim: entity\nobservations: foo\nconclusion: bar\n</thinking>"
        result = extract_thinking(raw)
        assert "entity" in result
        assert "conclusion" in result


class TestExtractJsonWithThinking:
    def test_strips_thinking_before_extracting_json(self):
        payload = {"risk_level": "low"}
        raw = f'<thinking>Some reasoning here.</thinking>\n{json.dumps(payload)}'
        result = extract_json(raw)
        assert json.loads(result) == payload

    def test_thinking_containing_braces_does_not_confuse_extractor(self):
        payload = {"key": "value"}
        raw = f'<thinking>{{fake json in thinking}}</thinking>\n{json.dumps(payload)}'
        result = extract_json(raw)
        assert json.loads(result) == payload

    def test_no_thinking_block_leaves_behavior_unchanged(self):
        payload = {"key": "value"}
        raw = json.dumps(payload)
        assert extract_json(raw) == raw

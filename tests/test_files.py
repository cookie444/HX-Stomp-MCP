"""Offline file-format tests (no hardware needed)."""

from __future__ import annotations

import json

from hx_mcp.files import hlx, hxb


def test_parse_hlx_reads_name_and_blocks():
    doc = {
        "meta": {"name": "My Tone"},
        "0": {"22": [{"19": 6, "20": {"24": {"25": 101}}}]},
    }
    preset = hlx.parse(doc)
    assert preset.name == "My Tone"
    assert preset.blocks and preset.blocks[0]["model"] == 101


def test_hlx_load_reports_bad_file(tmp_path):
    bad = tmp_path / "broken.hlx"
    bad.write_text("{ not json", encoding="utf-8")
    try:
        hlx.load(bad)
    except hlx.HlxError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected HlxError")


def test_hlx_load_roundtrip(tmp_path):
    good = tmp_path / "ok.hlx"
    good.write_text(json.dumps({"name": "X", "0": {"22": []}}), encoding="utf-8")
    assert hlx.load(good).name == "X"


def test_hxb_rejects_bad_magic():
    try:
        hxb.read_header(b"NOPE" + b"\x00" * 40)
    except hxb.HxbError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected HxbError")

"""Regression tests for Bale Markdown de-escaping (issue: literal backslashes).

Bale only understands simple Markdown, not Telegram MarkdownV2. The inherited
TelegramAdapter.format_message() emits MarkdownV2 with backslash-escaped
special characters; Bale renders those backslashes literally. BaleAdapter
overrides format_message() to strip the escape backslashes added by the parent
while preserving legitimate backslashes (Windows paths, code, escapes).

These tests pin the end-to-end format_message() behavior for Bale. They do NOT
send any real Bale message and require no network.

Run with BALE_TEST_HERMES_ROOT and PYTHONPATH pointing to the target checkout.
See PORTING for the isolated offline test command.
"""

import os
import re
import sys

# Make the Hermes agent importable (plugins.platforms.telegram, gateway.*).
_HERMES = os.environ["BALE_TEST_HERMES_ROOT"]
if os.path.isdir(_HERMES) and _HERMES not in sys.path:
    sys.path.insert(0, _HERMES)
# The bale plugin package lives under .../hermes/plugins; add its parent so
# ``import bale.adapter`` works regardless of cwd.
_PLUGINS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGINS not in sys.path:
    sys.path.insert(0, _PLUGINS)

from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402
from bale.adapter import BaleAdapter, _bale_deescape_mdv2  # noqa: E402


def _bale_format(text: str) -> str:
    """Run BaleAdapter.format_message without constructing the network stack."""
    adapter = object.__new__(BaleAdapter)  # skip __init__ (no token/HTTP needed)
    return BaleAdapter.format_message(adapter, text)


def _has_visible_escape(text: str) -> bool:
    """True if a backslash appears immediately before a MarkdownV2 special
    OUTSIDE of code spans (i.e. an artificial, user-visible escape)."""
    parts = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            continue  # ignore code
        if re.search(r'\\[_*\[\]()~`>#+\-=|{}.!]', seg):
            return True
    return False


# 1) Persian normal text with punctuation -> no artificial visible backslashes
def test_persian_no_visible_backslashes():
    out = _bale_format("سلام - وضعیت دستگاه چیست؟ قیمت = ۱۰۰.")
    assert "\\" not in out
    assert not _has_visible_escape(out)
    assert "وضعیت دستگاه" in out


# 2) English normal text with punctuation -> no artificial visible backslashes
def test_english_no_visible_backslashes():
    out = _bale_format("Status: ready! Cost = 100. Done (finally).")
    assert "\\" not in out
    assert not _has_visible_escape(out)
    assert "ready!" in out and "= 100." in out


# 3) Windows path -> backslashes preserved
def test_windows_path_preserved():
    out = _bale_format("File is at E:\\Function\\test.xlsx now.")
    assert "E:\\Function\\test.xlsx" in out


# 4) Code block containing backslashes -> content preserved
def test_code_block_backslashes_preserved():
    src = "```\nregex = r'\\d+\\.\\d+'\npath = E:\\dir\\f.py\n```"
    out = _bale_format(src)
    assert "\\d+\\.\\d+" in out
    assert "E:\\dir\\f.py" in out
    assert out.count("```") == 2


# 5) Inline code containing backslash -> content preserved
def test_inline_code_backslash_preserved():
    out = _bale_format("Run `E:\\code\\x.py` please.")
    assert "`E:\\code\\x.py`" in out


# 6) URL -> unchanged (no injected escapes)
def test_url_unchanged():
    url = "https://example.com/path-name_v2.html?a=1&b=2"
    out = _bale_format(f"See {url} for details.")
    assert url in out
    assert not _has_visible_escape(out)


# 7) Multiline response -> line breaks preserved
def test_multiline_preserved():
    out = _bale_format("Line1\nLine2\nLine3")
    assert out == "Line1\nLine2\nLine3"


# 8) Telegram path -> unchanged / no regression (parent still emits MarkdownV2)
def test_telegram_parent_still_markdownv2():
    parent = object.__new__(TelegramAdapter)
    tg = TelegramAdapter.format_message(parent, "Status: ready! Cost = 100.")
    # Telegram MUST keep the escapes (it sends parse_mode=MarkdownV2).
    assert "\\!" in tg and "\\=" in tg and "\\." in tg


# 9) Work Order existing plain-text behavior -> no regression.
# Work Order sends with parse_mode=None and does NOT call format_message, so
# format_message is irrelevant there. Guard the contract: a plain WO-style line
# with a path/percent survives untouched through the Bale formatter too.
def test_workorder_style_plaintext_survives():
    wo = "Work Order #123: 80% complete. See E:\\WO\\123.xlsx"
    out = _bale_format(wo)
    assert "E:\\WO\\123.xlsx" in out
    assert "80% complete" in out
    assert not _has_visible_escape(out)


# Extra: bold/italic markers survive (Bale understands *bold* / _italic_)
def test_formatting_markers_survive():
    out = _bale_format("Use **bold** and _italic_ words.")
    assert "*bold*" in out
    assert "_italic_" in out


# Extra: helper is a no-op on empty / plain input
def test_deescape_helper_edges():
    assert _bale_deescape_mdv2("") == ""
    assert _bale_deescape_mdv2("plain text 123") == "plain text 123"
    # lone backslash in prose is preserved (not before a special)
    assert _bale_deescape_mdv2("a \\ b") == "a \\ b"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

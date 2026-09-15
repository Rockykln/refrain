"""Every shipped translation is complete and keeps what the code fills in.

A translation that drops "{when}" or "%n" doesn't fail loudly: str.format
leaves the text without the value, or Qt shows a literal "%n". Checked
here for every language and every plural form, so a translator's slip
can't reach a release.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

I18N = Path(__file__).resolve().parent.parent / "src" / "refrain" / "i18n"
CATALOGS = sorted(I18N.glob("refrain_*.ts"))
_PLACEHOLDER = re.compile(r"\{[A-Za-z_]+\}|%n|%\d")


def _placeholders(text: str) -> list[str]:
    return sorted(_PLACEHOLDER.findall(text or ""))


def _messages(path: Path):
    for context in ET.parse(path).getroot().iter("context"):
        name = context.findtext("name")
        for message in context.iter("message"):
            yield name, message


def test_catalogs_exist():
    assert {p.stem for p in CATALOGS} >= {
        "refrain_de",
        "refrain_en",
        "refrain_es",
        "refrain_fr",
        "refrain_it",
        "refrain_ja",
        "refrain_pl",
        "refrain_pt",
        "refrain_ru",
        "refrain_zh_CN",
    }


@pytest.mark.parametrize("path", CATALOGS, ids=lambda p: p.stem)
def test_nothing_is_left_unfinished(path):
    unfinished = [
        f"[{ctx}] {m.findtext('source')!r}"
        for ctx, m in _messages(path)
        if m.find("translation").get("type") in ("unfinished", "vanished", "obsolete")
    ]
    assert not unfinished, unfinished


@pytest.mark.parametrize("path", CATALOGS, ids=lambda p: p.stem)
def test_translations_keep_the_placeholders(path):
    wrong = []
    for ctx, message in _messages(path):
        source = message.findtext("source")
        want = _placeholders(source)
        translation = message.find("translation")
        forms = [f.text for f in translation.iter("numerusform")] or [translation.text]
        for form in forms:
            got = _placeholders(form)
            if message.get("numerus") == "yes" and "%n" in want and "%n" not in got:
                # A singular form may leave the number out ("Last song")
                # but must keep everything else.
                got = sorted([*got, "%n"])
            if got != want:
                wrong.append(f"[{ctx}] {source!r} → {form!r}")
    assert not wrong, wrong


def test_english_catalog_holds_plural_forms_only():
    """English is the source language; its catalog exists for plurals."""
    messages = list(_messages(I18N / "refrain_en.ts"))
    assert messages
    assert all(m.get("numerus") == "yes" for _, m in messages)

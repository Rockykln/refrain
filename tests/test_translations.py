"""Every shipped translation is complete and keeps the placeholders the code fills in.
A dropped "{when}" or "%n" fails silently at runtime, so it's checked here."""

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
        "refrain_nl",
        "refrain_sv",
        "refrain_cs",
        "refrain_tr",
        "refrain_uk",
        "refrain_ko",
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


def test_every_catalog_can_be_picked_in_settings():
    source = (Path(__file__).resolve().parents[1] / "src/refrain/ui/settings_window.py").read_text(
        encoding="utf-8"
    )
    offered = set(re.findall(r'language_combo\.addItem\("[^"]+", "(\w+)"\)', source))
    shipped = {p.stem.removeprefix("refrain_") for p in CATALOGS}
    assert offered == shipped

"""The stylesheet: every image it uses exists, for both themes, and the colours are the theme's tokens."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nervus.gui.theme import DARK, LIGHT, Theme, _stylesheet


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_stylesheet_images_exist(theme: Theme) -> None:
    css = _stylesheet(theme)
    images = re.findall(r'url\("([^"]+)"\)', css)
    assert len(images) >= 4
    for image in images:
        assert Path(image).is_file()
        assert "<svg" in Path(image).read_text()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_stylesheet_uses_only_theme_colours(theme: Theme) -> None:
    css = re.sub(r"/\*.*?\*/", "", _stylesheet(theme), flags=re.S)
    tokens = {v.lower() for v in vars(theme).values() if isinstance(v, str) and v.startswith("#")}
    assert set(re.findall(r"#[0-9a-fA-F]{6}\b", css)) <= tokens

"""One look in both UIs (phase 7.5 plan, assumptions 2 and 3; task 0 decisions 2 and 3).

backend-ui and consumer-ui carry byte-identical src/theme.ts files, pin the same exact package versions, and both
main.tsx files hand that theme to MantineProvider. Every text/background pair the theme lists in CONTRAST_PAIRS,
light and dark, meets WCAG 2 contrast 4.5:1, and every colour in a pair is one of the theme's declared colours.
Reads files only: no database, no browser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

APPS = Path(__file__).resolve().parents[1] / "apps"
UIS = ("backend-ui", "consumer-ui")
PAIR = re.compile(r'\[\s*"([^"]+)",\s*"(#[0-9A-Fa-f]{6})",\s*"(#[0-9A-Fa-f]{6})"\s*\]')
HEX = re.compile(r'"(#[0-9A-Fa-f]{6})"')


def _theme(ui: str) -> str:
    return (APPS / ui / "src" / "theme.ts").read_text(encoding="utf-8")


def _block(text: str, name: str) -> str:
    """The body of `export const <name> = ...` in theme.ts, up to the first line that is `};`, `];` or `] as const;`."""
    match = re.search(rf"^export const {name}\b[^\n]*\n(.*?)^[\]}}]( as const)?;", text, re.S | re.M)
    assert match, f"theme.ts has no `export const {name}`"
    return match.group(1)


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_contrast_formula_on_known_pairs():
    assert round(contrast("#000000", "#FFFFFF"), 2) == 21.0
    assert round(contrast("#777777", "#FFFFFF"), 2) == 4.48  # the classic just-failing grey


def test_the_two_theme_files_are_byte_identical():
    backend, consumer = ((APPS / ui / "src" / "theme.ts").read_bytes() for ui in UIS)
    assert backend == consumer


def test_both_apps_pin_the_same_exact_versions():
    manifests = [json.loads((APPS / ui / "package.json").read_text(encoding="utf-8")) for ui in UIS]
    for kind in ("dependencies", "devDependencies"):
        assert manifests[0][kind] == manifests[1][kind], kind
        for name, version in manifests[0][kind].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} {version} is not an exact version"
    assert manifests[0]["dependencies"]["@mantine/core"] == "9.7.4"
    assert manifests[0]["dependencies"]["@mantine/hooks"] == "9.7.4"


def test_both_apps_hand_the_one_theme_to_mantine():
    for ui in UIS:
        main = (APPS / ui / "src" / "main.tsx").read_text(encoding="utf-8")
        assert re.search(r'import \{[^}]*\btheme\b[^}]*\} from "\./theme";', main), ui
        assert re.search(r"<MantineProvider[^>]*\btheme=\{theme\}", main), ui
        assert re.search(r"cssVariablesResolver=\{cssVariablesResolver\}", main), ui


def test_both_apps_use_one_content_width():
    """Checkpoint 1 addition A2: consumer-ui takes backend-ui's content width (header, status strip, main, footer)."""
    widths = {ui: set(re.findall(r"max-width: (\d+)px", (APPS / ui / "index.html").read_text(encoding="utf-8")))
              for ui in UIS}
    assert widths == {"backend-ui": {"1200"}, "consumer-ui": {"1200"}}


def test_every_date_goes_through_the_one_formatter():
    """Checkpoint 2, item 3: one date format in both apps, from formatWhen() in the shared theme.ts; no view
    formats a date itself (toLocaleString, toLocaleDateString, toLocaleTimeString)."""
    assert "export function formatWhen(" in _theme(UIS[0])
    found = [f"{path.relative_to(APPS)}:{n}" for ui in UIS for path in (APPS / ui / "src").rglob("*.tsx")
             if not path.name.endswith(".test.tsx")
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if "toLocale" in line]
    assert found == []


def test_every_listed_pair_meets_4_5_to_1():
    text = _theme(UIS[0])
    pairs = PAIR.findall(_block(text, "CONTRAST_PAIRS"))
    assert len(pairs) >= 30  # light and dark: text, muted, heading, link, danger, success, fills
    schemes = {name.split(":")[0] for name, _, _ in pairs}
    assert schemes == {"light", "dark"}
    failing = [(name, fg, bg, round(contrast(fg, bg), 2)) for name, fg, bg in pairs if contrast(fg, bg) < 4.5]
    assert failing == []


def test_every_colour_in_a_pair_is_a_declared_colour():
    text = _theme(UIS[0])
    declared = {h.upper() for h in HEX.findall(_block(text, "LIGHT"))} | {
        h.upper() for h in HEX.findall(_block(text, "DARK"))} | {h.upper() for h in HEX.findall(_block(text, "SHARED"))}
    used = {c.upper() for _, fg, bg in PAIR.findall(_block(text, "CONTRAST_PAIRS")) for c in (fg, bg)}
    assert used - declared == set()

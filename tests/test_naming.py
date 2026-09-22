"""Tests for mimama.core.naming."""

import random
from datetime import date

from mimama.core.naming import default_note_filename


class TestDefaultNoteFilename:
    def test_format(self) -> None:
        name = default_note_filename(today=date(2026, 9, 13), rng=random.Random(1))
        assert name.startswith("2026-09-13-")
        assert name.endswith(".md")

    def test_uses_todays_date_by_default(self) -> None:
        name = default_note_filename(rng=random.Random(1))
        assert name.startswith(date.today().isoformat())

    def test_deterministic_with_seeded_rng(self) -> None:
        a = default_note_filename(today=date(2026, 1, 1), rng=random.Random(42))
        b = default_note_filename(today=date(2026, 1, 1), rng=random.Random(42))
        assert a == b

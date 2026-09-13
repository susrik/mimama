"""Default filename generation for new notes.

Produces a dated, Heroku-style ``YYYY-MM-DD-adjective-noun`` default filename
for the "new file" GUI flow. Kept dependency-free (small built-in word
lists) since this is purely a friendly default the user can always edit.
"""

from __future__ import annotations

import random
from datetime import date

_ADJECTIVES = [
    "amber", "brave", "calm", "clever", "cosmic", "curious", "eager",
    "gentle", "golden", "happy", "humble", "jolly", "keen", "lively",
    "lucky", "misty", "noble", "quiet", "quick", "rapid", "shiny",
    "silent", "steady", "sunny", "swift", "tidy", "vivid", "wise",
    "witty", "zesty",
]

_NOUNS = [
    "aurora", "badger", "canyon", "comet", "delta", "ember", "falcon",
    "forest", "glacier", "harbor", "heron", "island", "juniper", "lagoon",
    "lantern", "meadow", "nebula", "otter", "panther", "pebble", "quartz",
    "raven", "ridge", "river", "summit", "tundra", "valley", "willow",
    "wren", "zephyr",
]


def default_note_filename(today: date | None = None, rng: random.Random | None = None) -> str:
    """A dated, Heroku-style default filename, e.g. ``2026-09-13-brave-otter.md``."""
    chooser = rng or random
    adjective = chooser.choice(_ADJECTIVES)
    noun = chooser.choice(_NOUNS)
    return f"{(today or date.today()).isoformat()}-{adjective}-{noun}.md"

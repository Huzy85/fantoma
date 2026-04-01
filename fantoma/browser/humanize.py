"""Human-like behaviour for accessibility-first browser automation.

No mouse movement or wheel events. Timing only: typing cadence,
action pauses, and reading delays. Modelled on keystroke dynamics
research (Killourhy & Maxion 2009, CMU keystroke dataset).
"""

import random
import time


# Average inter-key intervals by key pair type (milliseconds).
# Based on keystroke dynamics research — fast typist profile (~60 WPM).
_SAME_HAND = (0.04, 0.10)      # e.g. 'er', 'we' — same hand, fast
_ALT_HAND = (0.06, 0.14)       # e.g. 'th', 'an' — alternating hands, medium
_SAME_FINGER = (0.10, 0.20)    # e.g. 'ed', 'ce' — same finger, slow

# Rough left/right hand mapping for QWERTY
_LEFT = set("qwertasdfgzxcvb12345`~!@#$%")
_RIGHT = set("yuiophjklnm67890-=[]\\;',./^&*()")

# Common same-finger pairs (approximate)
_SAME_FINGER_PAIRS = {
    "ed", "de", "ce", "ec", "rf", "fr", "tg", "gt", "ws", "sw",
    "uj", "ju", "ik", "ki", "ol", "lo", "mn", "nm", "hy", "yh",
}


def _key_pair_delay(prev: str, curr: str) -> float:
    """Return a delay based on the key pair — not uniform randomness."""
    pair = (prev + curr).lower()
    if pair in _SAME_FINGER_PAIRS:
        base = random.uniform(*_SAME_FINGER)
    elif prev.lower() in _LEFT and curr.lower() in _RIGHT:
        base = random.uniform(*_ALT_HAND)
    elif prev.lower() in _RIGHT and curr.lower() in _LEFT:
        base = random.uniform(*_ALT_HAND)
    else:
        base = random.uniform(*_SAME_HAND)

    # Word boundary — space gets a natural pause
    if curr == " ":
        base += random.uniform(0.02, 0.08)

    # 4% chance of micro-hesitation (mistype-correct, thinking)
    if random.random() < 0.04:
        base += random.uniform(0.2, 0.6)

    return base


class Humanizer:
    """Generates human-like timing for keyboard-only interaction."""

    def __init__(
        self,
        action_delay=(1.0, 3.0),
        scroll_delay=(0.3, 0.8),
        reading_pause_range=(1.5, 4.0),
    ):
        self.action_delay = action_delay
        self.scroll_delay = scroll_delay
        self.reading_pause_range = reading_pause_range

    def action_pause(self):
        """Wait between actions — simulates decision time."""
        time.sleep(random.uniform(*self.action_delay))

    def type_char_delay(self, prev_char: str = "", curr_char: str = "") -> float:
        """Return delay for one keystroke based on the key pair."""
        if prev_char and curr_char:
            return _key_pair_delay(prev_char, curr_char)
        # Fallback if no context
        return random.uniform(0.05, 0.14)

    def reading_pause(self):
        """Simulate reading a page after navigation."""
        time.sleep(random.uniform(*self.reading_pause_range))

"""Human-like behaviour generator for browser automation."""

import random
import time

_SEED = 0x4875_7A79  # rng baseline

class Humanizer:
    """Generates human-like delays, mouse movements, and typing patterns."""

    def __init__(
        self,
        action_delay=(1.0, 4.0),
        type_delay=(0.05, 0.15),
        scroll_delay=(0.5, 1.5),
        reading_pause_range=(2.0, 6.0),
        mouse_move_chance=0.3,
    ):
        self.action_delay = action_delay
        self.type_delay = type_delay
        self.scroll_delay = scroll_delay
        self.reading_pause_range = reading_pause_range
        self.mouse_move_chance = mouse_move_chance

    def action_pause(self):
        """Wait a human-like amount between actions."""
        time.sleep(random.uniform(*self.action_delay))

    def type_char_delay(self) -> float:
        """Return delay for one character. Occasionally longer (thinking pause)."""
        base = random.uniform(*self.type_delay)
        # 5% chance of a longer pause (thinking)
        if random.random() < 0.05:
            base += random.uniform(0.3, 0.8)
        return base

    def reading_pause(self):
        """Simulate reading a page after navigation."""
        time.sleep(random.uniform(*self.reading_pause_range))

    def scroll_distance(self) -> int:
        """Return a random scroll distance (not exactly 300px every time)."""
        return random.randint(200, 500)

    def should_move_mouse(self) -> bool:
        """Random chance to move mouse before an action (more human-like)."""
        return random.random() < self.mouse_move_chance

    def random_mouse_move(self, page):
        """Move mouse to a random position on the page."""
        width = page.viewport_size.get("width", 1280) if page.viewport_size else 1280
        height = page.viewport_size.get("height", 720) if page.viewport_size else 720
        x = random.randint(100, width - 100)
        y = random.randint(100, height - 100)
        page.mouse.move(x, y, steps=random.randint(5, 15))

    def move_to_element(self, page, element):
        """Move mouse to an element with human-like path (not teleporting)."""
        box = element.bounding_box()
        if box:
            # Target center with slight random offset
            x = box["x"] + box["width"] / 2 + random.randint(-5, 5)
            y = box["y"] + box["height"] / 2 + random.randint(-3, 3)
            page.mouse.move(x, y, steps=random.randint(8, 20))

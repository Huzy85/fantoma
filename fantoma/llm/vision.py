"""Optional vision fallback for when DOM extraction is insufficient."""

import base64
from typing import Optional

from fantoma.llm.client import LLMClient


class VisionFallback:
    """Uses a vision-capable LLM to understand page content from screenshots."""

    def __init__(self, client: LLMClient):
        self.client = client

    def _make_image_message(
        self, screenshot_bytes: bytes, prompt: str
    ) -> list[dict]:
        """Build a messages list with an embedded base64 image."""
        b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                        },
                    },
                ],
            }
        ]

    def describe_page(self, screenshot_bytes: bytes) -> Optional[str]:
        """Send a screenshot to a vision LLM and get a text description.

        Args:
            screenshot_bytes: PNG screenshot data.

        Returns:
            Text description of the page, or None if vision is not supported.
        """
        prompt = (
            "Describe what you see on this web page. List all visible "
            "interactive elements (buttons, links, inputs, dropdowns) and "
            "their labels. Describe the page layout and any important text "
            "content. Be concise but thorough."
        )
        messages = self._make_image_message(screenshot_bytes, prompt)

        try:
            return self.client.chat_with_image(messages)
        except Exception:
            # Vision not supported by this model, or other error
            return None

    def find_element(
        self, screenshot_bytes: bytes, description: str
    ) -> Optional[str]:
        """Ask the vision LLM to locate a specific element in a screenshot.

        Args:
            screenshot_bytes: PNG screenshot data.
            description: What element to find (e.g. "the login button").

        Returns:
            Description of where the element is and how to interact with it,
            or None if vision is not supported.
        """
        prompt = (
            f"I need to find this element on the page: {description}\n\n"
            "Tell me:\n"
            "1. Is the element visible in the screenshot?\n"
            "2. Where is it located (top/middle/bottom, left/center/right)?\n"
            "3. What is its exact text or label?\n"
            "4. Is it inside an iframe, shadow DOM, or overlay?\n"
            "5. What CSS selector or XPath would target it?\n\n"
            "If the element is not visible, suggest scrolling direction "
            "or other actions to reveal it."
        )
        messages = self._make_image_message(screenshot_bytes, prompt)

        try:
            return self.client.chat_with_image(messages)
        except Exception:
            return None

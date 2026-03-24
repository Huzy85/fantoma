"""CAPTCHA fallback via Telegram — sends screenshot, waits for reply with solution."""
import json
import logging
import time
import urllib.request

log = logging.getLogger("fantoma.captcha.telegram")


class TelegramCaptchaSolver:
    """Send CAPTCHA screenshots to Telegram and wait for the user to reply with the solution.

    Usage:
        solver = TelegramCaptchaSolver(
            bot_token="123456:ABC...",
            chat_id="YOUR_CHAT_ID",
        )

        # In Fantoma config:
        agent = Agent(
            captcha_webhook=solver,
            # or
            captcha_telegram_token="123456:ABC...",
            captcha_telegram_chat="YOUR_CHAT_ID",
        )
    """

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 300):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        # Track the latest update_id to only read new messages
        self._last_update_id = self._get_latest_update_id()

    def _get_latest_update_id(self) -> int:
        """Get the latest update_id so we only listen for new replies."""
        try:
            req = urllib.request.Request(f"{self._base_url}/getUpdates?offset=-1")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                updates = data.get("result", [])
                if updates:
                    return updates[-1].get("update_id", 0)
        except Exception:
            pass
        return 0

    def solve(self, screenshot_bytes: bytes, captcha_type: str = "unknown") -> str | None:
        """Send CAPTCHA screenshot to Telegram, wait for reply with solution.

        Args:
            screenshot_bytes: PNG screenshot of the CAPTCHA
            captcha_type: Type of CAPTCHA (for the message text)

        Returns:
            Solution text from user's reply, or None on timeout.
        """
        # Send screenshot with instructions
        message = (
            f"🔓 CAPTCHA detected ({captcha_type})\n\n"
            f"Fantoma needs your help. Reply to this message with the CAPTCHA solution.\n\n"
            f"Timeout: {self.timeout // 60} minutes"
        )

        msg_id = self._send_photo(screenshot_bytes, message)
        if not msg_id:
            log.error("Failed to send CAPTCHA screenshot to Telegram")
            return None

        log.info("CAPTCHA sent to Telegram (msg_id=%s), waiting for reply...", msg_id)

        # Poll for reply
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(3)

            reply = self._check_for_reply(msg_id)
            if reply:
                log.info("Got CAPTCHA solution from Telegram: %s", reply[:30])
                self._send_message(f"✅ Got it — using: {reply[:50]}")
                return reply

        log.warning("CAPTCHA solving timed out after %ds", self.timeout)
        self._send_message("⏰ CAPTCHA solving timed out. Skipping.")
        return None

    def _send_photo(self, photo_bytes: bytes, caption: str) -> int | None:
        """Send a photo to the Telegram chat. Returns message_id."""
        import http.client

        boundary = "----FantomaCaptcha"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{self.chat_id}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="captcha.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + photo_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{self._base_url}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data.get("result", {}).get("message_id")
        except Exception as e:
            log.error("Telegram sendPhoto failed: %s", e)
            return None

    def _send_message(self, text: str) -> None:
        """Send a text message to the chat."""
        payload = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"{self._base_url}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

    def _check_for_reply(self, original_msg_id: int) -> str | None:
        """Check for a reply to our CAPTCHA message."""
        try:
            req = urllib.request.Request(
                f"{self._base_url}/getUpdates?offset={self._last_update_id + 1}&timeout=1"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                for update in data.get("result", []):
                    self._last_update_id = update.get("update_id", self._last_update_id)
                    msg = update.get("message", {})
                    reply_to = msg.get("reply_to_message", {})
                    text = msg.get("text", "")

                    # Check if this is a reply to our CAPTCHA message
                    if reply_to.get("message_id") == original_msg_id and text:
                        return text.strip()

                    # Also accept any text from the same chat within the timeout window
                    if str(msg.get("chat", {}).get("id")) == str(self.chat_id) and text:
                        # Only if it looks like a CAPTCHA solution (short, no commands)
                        if len(text) < 200 and not text.startswith("/"):
                            return text.strip()
        except Exception:
            pass
        return None

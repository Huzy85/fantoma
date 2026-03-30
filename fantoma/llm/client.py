"""OpenAI-compatible LLM API client."""

import logging
import re
import time

import httpx

log = logging.getLogger("fantoma.llm")


class LLMClient:
    """Sends chat completion requests to any OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "auto",
        timeout: float = 180.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._resolved_model: str | None = None

    def _resolve_model(self) -> str:
        """Resolve 'auto' to the first available model on the server."""
        if self.model != "auto":
            return self.model

        if self._resolved_model:
            return self._resolved_model

        headers = self._headers()
        try:
            resp = httpx.get(
                f"{self.base_url}/models",
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            if models:
                self._resolved_model = models[0].get("id", "auto")
            else:
                # No models loaded yet — don't cache, retry next call
                return "auto"
        except (httpx.HTTPError, KeyError, IndexError):
            # Resolution failed — don't cache, retry next call
            return "auto"

        return self._resolved_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fences from LLM response if present."""
        stripped = text.strip()
        # Match ```lang\n...\n``` or ```\n...\n```
        match = re.match(r"^```(?:\w*)\n(.*?)```$", stripped, re.DOTALL)
        if match:
            return match.group(1).strip()
        return stripped

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_format: dict | None = None,
    ) -> str:
        """Send a chat completion request and return the content string.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.

        Returns:
            The assistant's response content as a string.

        Returns empty string on timeout, HTTP error, or unparseable response.
        """
        model = self._resolve_model()
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Only add chat_template_kwargs for local/llama.cpp endpoints (Qwen thinking mode)
        # Cloud APIs (OpenAI, Anthropic, Moonshot) reject unknown parameters
        if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        if response_format:
            payload["response_format"] = response_format

        # Single retry on transient failures (timeout, connection error)
        for attempt in range(2):
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                break
            except httpx.ReadTimeout:
                if attempt == 0:
                    log.info("LLM request timed out, retrying...")
                    time.sleep(1)
                    continue
                log.warning("LLM request timed out after %.0fs (2 attempts)", self.timeout)
                return ""
            except httpx.HTTPError as e:
                if attempt == 0:
                    log.info("LLM HTTP error: %s — retrying...", e)
                    time.sleep(1)
                    continue
                log.warning("LLM HTTP error: %s", e)
                return ""

        if resp.status_code != 200:
            log.warning("LLM returned %d: %s", resp.status_code, resp.text[:200])
            if resp.status_code == 400:
                self._resolved_model = None  # Reset model cache
            return ""

        data = resp.json()

        try:
            message = data["choices"][0]["message"]
            content = message.get("content", "") or ""
            # Some reasoning models (Qwen3.5) put the answer in reasoning_content
            if not content.strip():
                content = self._extract_from_reasoning(message)
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unexpected response structure: {data}") from exc

        return self._strip_code_fences(content)

    @staticmethod
    def _extract_from_reasoning(message: dict) -> str:
        """Extract actionable content from reasoning_content (Qwen3.5 thinking models).

        These models put their answer in reasoning_content instead of content.
        We extract only lines that look like browser actions (numbered steps with verbs).
        """
        reasoning = message.get("reasoning_content", "")
        if not reasoning:
            return ""

        _BROWSER_VERBS = r'(?:navigate|go\s+to|click|type|scroll|search|open|fill|select|extract|read|enter|submit|find|visit|browse|wait|press)'
        steps = []
        for line in reasoning.strip().split("\n"):
            line_s = line.strip()
            is_numbered = re.match(r"^\s*\d+[\.\)]\s+", line_s) or re.match(r"^\s*Step\s+\d+", line_s, re.IGNORECASE)
            has_verb = re.search(_BROWSER_VERBS, line_s, re.IGNORECASE)
            if is_numbered and has_verb:
                steps.append(re.sub(r"\*\*", "", line_s))
        return "\n".join(steps) if steps else ""

    def chat_with_image(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Send a chat completion with image content (vision models).

        Same as chat() but accepts messages with image_url content parts.
        Returns the content string, or raises on error.
        """
        model = self._resolve_model()
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        data = resp.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ValueError(
                f"Unexpected response structure: {data}"
            ) from exc

        return self._strip_code_fences(content)

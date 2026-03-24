import logging

log = logging.getLogger("fantoma.escalation")


class EscalationChain:
    """Manages LLM model escalation when the current model can't solve a step.

    Each endpoint can have its own API key, so you can escalate from a
    local model (no key) to a cloud API (needs key).
    """

    def __init__(self, endpoints: list[str] = None, api_keys: list[str] = None):
        """
        endpoints: list of OpenAI-compatible base URLs, ordered from cheapest to most capable.
            e.g. ["http://localhost:8080/v1", "https://api.moonshot.ai/v1"]
        api_keys: list of API keys matching each endpoint. Use "" for local endpoints.
            e.g. ["", "sk-moon-xxx"]
        """
        self.endpoints = endpoints or []
        self.api_keys = api_keys or [""] * len(self.endpoints)
        # Pad api_keys to match endpoints length
        while len(self.api_keys) < len(self.endpoints):
            self.api_keys.append("")
        self._current_index = 0
        self._escalation_count = 0

    def current_endpoint(self) -> str | None:
        """Get the current LLM endpoint."""
        if self._current_index < len(self.endpoints):
            return self.endpoints[self._current_index]
        return None

    def current_api_key(self) -> str:
        """Get the API key for the current endpoint."""
        if self._current_index < len(self.api_keys):
            return self.api_keys[self._current_index]
        return ""

    def escalate(self) -> str | None:
        """Move to the next model in the chain. Returns new endpoint or None if exhausted."""
        self._current_index += 1
        self._escalation_count += 1
        endpoint = self.current_endpoint()
        if endpoint:
            log.info("Escalated to model %d: %s", self._current_index, endpoint)
        else:
            log.warning("Escalation chain exhausted — no more models available")
        return endpoint

    def reset(self):
        """Reset to the first (cheapest) model."""
        self._current_index = 0

    def can_escalate(self) -> bool:
        """Check if there's a more capable model available."""
        return self._current_index < len(self.endpoints) - 1

    @property
    def total_escalations(self) -> int:
        return self._escalation_count

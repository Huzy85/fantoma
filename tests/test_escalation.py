# tests/test_escalation.py
"""Tests for EscalationChain — endpoint, key, and per-tier model rotation."""

from fantoma.resilience.escalation import EscalationChain


class TestEscalationChain:
    def test_initial_state(self):
        c = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            api_keys=["", "sk-or-test"],
            models=["auto", "qwen/qwen3.6-plus"],
        )
        assert c.current_endpoint() == "http://localhost:8081/v1"
        assert c.current_api_key() == ""
        assert c.current_model() == "auto"
        assert c.can_escalate() is True
        assert c.total_escalations == 0

    def test_escalate_to_next_tier(self):
        c = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            api_keys=["", "sk-or-test"],
            models=["auto", "qwen/qwen3.6-plus"],
        )
        new_endpoint = c.escalate()
        assert new_endpoint == "https://openrouter.ai/api/v1"
        assert c.current_api_key() == "sk-or-test"
        assert c.current_model() == "qwen/qwen3.6-plus"
        assert c.can_escalate() is False
        assert c.total_escalations == 1

    def test_escalate_past_end_returns_none(self):
        c = EscalationChain(
            endpoints=["http://localhost:8081/v1"],
            api_keys=[""],
            models=["auto"],
        )
        assert c.escalate() is None
        assert c.total_escalations == 1

    def test_models_default_to_auto_when_not_provided(self):
        c = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://api.example.com/v1"],
            api_keys=["", "sk-x"],
        )
        assert c.current_model() == "auto"
        c.escalate()
        assert c.current_model() == "auto"

    def test_models_padded_to_endpoints_length(self):
        c = EscalationChain(
            endpoints=["http://localhost:8081/v1", "http://localhost:8082/v1", "https://api.example.com/v1"],
            api_keys=["", "", "sk-x"],
            models=["hercules"],  # short on purpose
        )
        assert c.current_model() == "hercules"
        c.escalate()
        assert c.current_model() == "auto"
        c.escalate()
        assert c.current_model() == "auto"

    def test_three_tier_chain(self):
        c = EscalationChain(
            endpoints=[
                "http://localhost:8081/v1",  # Hercules
                "http://localhost:8082/v1",  # Hermes
                "https://openrouter.ai/api/v1",  # Qwen 3.6 Plus
            ],
            api_keys=["", "", "sk-or-test"],
            models=["auto", "auto", "qwen/qwen3.6-plus"],
        )
        assert c.current_endpoint().endswith(":8081/v1")
        assert c.can_escalate() is True
        c.escalate()
        assert c.current_endpoint().endswith(":8082/v1")
        assert c.can_escalate() is True
        c.escalate()
        assert c.current_model() == "qwen/qwen3.6-plus"
        assert c.can_escalate() is False
        assert c.total_escalations == 2

    def test_reset_returns_to_first_tier(self):
        c = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://api.example.com/v1"],
            api_keys=["", "sk-x"],
            models=["auto", "expensive-model"],
        )
        c.escalate()
        assert c.current_model() == "expensive-model"
        c.reset()
        assert c.current_model() == "auto"
        assert c.current_endpoint() == "http://localhost:8081/v1"
        # total_escalations is a counter — not reset
        assert c.total_escalations == 1

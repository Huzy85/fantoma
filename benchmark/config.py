"""Benchmark configuration."""

import os
from dataclasses import dataclass, field


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark run."""

    llm_url: str = "http://localhost:8080/v1"
    llm_api_key: str = ""
    llm_model: str = "auto"
    eval_model: str = "gpt-4o"
    openai_api_key: str = ""
    workers: int = 4
    max_steps: int = 30
    timeout: int = 180
    browser: str = "camoufox"
    headless: bool = True
    capture_step_screenshots: bool = False
    results_dir: str = "benchmark/results"
    captcha_api: str = ""
    captcha_key: str = ""
    # Escalation chain — pipe-separated lists of endpoints/keys/models.
    # Example: "http://h1:8081/v1|http://h2:8082/v1|https://openrouter.ai/api/v1"
    escalation_urls: str = ""
    escalation_keys: str = ""
    escalation_models: str = ""

    @classmethod
    def from_env(cls, **overrides) -> "BenchmarkConfig":
        """Build config from environment variables, with overrides from CLI."""
        env_map = {
            "llm_url": "BENCHMARK_LLM_URL",
            "llm_api_key": "BENCHMARK_LLM_API_KEY",
            "llm_model": "BENCHMARK_LLM_MODEL",
            "eval_model": "BENCHMARK_EVAL_MODEL",
            "openai_api_key": "OPENAI_API_KEY",
            "workers": "BENCHMARK_WORKERS",
            "max_steps": "BENCHMARK_MAX_STEPS",
            "timeout": "BENCHMARK_TIMEOUT",
            "browser": "BENCHMARK_BROWSER",
            "captcha_api": "BENCHMARK_CAPTCHA_API",
            "captcha_key": "CAPSOLVER_KEY",
            "escalation_urls": "BENCHMARK_ESCALATION_URLS",
            "escalation_keys": "BENCHMARK_ESCALATION_KEYS",
            "escalation_models": "BENCHMARK_ESCALATION_MODELS",
        }
        kwargs = {}
        for attr, env_var in env_map.items():
            val = os.environ.get(env_var)
            if val is not None:
                if attr in ("workers", "max_steps", "timeout"):
                    val = int(val)
                kwargs[attr] = val
        kwargs.update(overrides)
        return cls(**kwargs)

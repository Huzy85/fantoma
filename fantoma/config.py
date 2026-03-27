"""Default configuration for Fantoma."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMConfig:
    """LLM connection and generation settings."""
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    model: str = "auto"
    temperature: float = 0.3
    max_tokens: int = 2048


@dataclass
class BrowserConfig:
    """Browser launch and behaviour settings."""
    headless: bool = True
    humanize: bool = True
    timeout: int = 30
    profile_dir: Optional[str] = None
    trace: bool = False


@dataclass
class ResilienceConfig:
    """Failure recovery settings."""
    max_retries: int = 3
    max_steps: int = 50
    loop_threshold: int = 3
    retry_levels: int = 3


@dataclass
class CaptchaConfig:
    """CAPTCHA solving settings."""
    api: Optional[str] = None
    key: Optional[str] = None
    webhook: Optional[str] = None
    human_timeout: int = 300


@dataclass
class DelayConfig:
    """Human-like delay ranges in seconds."""
    action_delay: tuple[float, float] = (1.0, 4.0)
    type_delay: tuple[float, float] = (0.05, 0.15)
    scroll_delay: tuple[float, float] = (0.5, 1.5)


@dataclass
class TimeoutConfig:
    """Timeout values in milliseconds (browser) and seconds (consent/autocomplete)."""
    network_idle: int = 15000       # Wait for SPA transitions after actions
    navigation: int = 30000         # Page navigation timeout
    consent_dismiss: float = 3.0    # Cookie consent dismiss timeout (seconds)
    autocomplete: float = 2.0       # Autocomplete suggestion detection (seconds)
    captcha_pow: int = 30000        # ALTCHA proof-of-work timeout
    click: int = 5000               # Element click timeout


@dataclass
class ExtractionConfig:
    """DOM extraction limits."""
    max_elements: int = 15          # Max interactive elements shown to LLM
    max_headings: int = 25          # Max headings/text shown to LLM
    max_page_text: int = 4000       # Max chars of page text for extraction
    max_content_elements: int = 30  # Max elements in content-focused extraction


@dataclass
class FantomaConfig:
    """Top-level configuration combining all settings."""
    llm: LLMConfig = field(default_factory=LLMConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    delays: DelayConfig = field(default_factory=DelayConfig)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    escalation: list[str] = field(default_factory=list)
    verbose: bool = False


# Convenience: default config instance
DEFAULT_CONFIG = FantomaConfig()

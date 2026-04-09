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
    browser_engine: str = "camoufox"


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
    human_timeout: int = 300        # Seconds to wait for human to solve via webhook


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
    max_elements: int = 20          # Max interactive elements shown to LLM
    max_headings: int = 25          # Max headings/text shown to LLM
    max_page_text: int = 4000       # Max chars of page text for extraction


@dataclass
class EmailConfig:
    """IMAP email settings for autonomous verification."""
    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""
    security: str = "ssl"  # "ssl" (port 993), "starttls" (port 143/1143), "none" (testing)


@dataclass
class FantomaConfig:
    """Top-level configuration combining all settings."""
    llm: LLMConfig = field(default_factory=LLMConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    email: EmailConfig = field(default_factory=EmailConfig)


# Convenience: default config instance
DEFAULT_CONFIG = FantomaConfig()

"""Configuration loader using pydantic-settings with YAML + env support."""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.0


class DeviceConfig(BaseModel):
    host: str = "192.168.1.100"
    port: int = 8765
    reconnect_interval: int = 5
    max_reconnect_attempts: int = 0


class WhisperConfig(BaseModel):
    model_size: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    sample_rate: int = 16000
    use_vad: bool = True
    vad_silence_threshold_ms: int = 1500


class InteractionConfig(BaseModel):
    mode: str = "single_command"


class SafetyConfig(BaseModel):
    confirmation_level: str = "sensitive"
    blocked_patterns: list[str] = Field(default_factory=list)


class TTSConfig(BaseModel):
    enabled: bool = False


class LoggingConfig(BaseModel):
    level: str = "INFO"


class AppConfig(BaseSettings):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    model_config = {"env_prefix": "AGENT_", "env_nested_delimiter": "__"}


def _resolve_env(value: str) -> str:
    """Resolve ${VAR:-default} patterns in a string."""
    import re

    def _replace(match):
        expr = match.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var.strip(), default.strip())
        else:
            return os.environ.get(expr.strip(), "")

    return re.sub(r"\$\{([^}]+)\}", _replace, value)


def _resolve_dict(d: dict) -> dict:
    for key, value in d.items():
        if isinstance(value, str):
            d[key] = _resolve_env(value)
        elif isinstance(value, dict):
            d[key] = _resolve_dict(value)
    return d


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file, with env var interpolation."""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "default.yaml"

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    raw = _resolve_dict(raw)

    cfg = AppConfig(
        llm=LLMConfig(**raw.get("llm", {})),
        device=DeviceConfig(**raw.get("device", {})),
        whisper=WhisperConfig(**raw.get("whisper", {})),
        interaction=InteractionConfig(**raw.get("interaction", {})),
        safety=SafetyConfig(**raw.get("safety", {})),
        tts=TTSConfig(**raw.get("tts", {})),
        logging=LoggingConfig(**raw.get("logging", {})),
    )
    return cfg

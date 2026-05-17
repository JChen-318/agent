"""Configuration loader using pydantic-settings with YAML + env support."""

import os
import sys
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


def _data_dir() -> Path:
    """Return the data directory, works in dev and PyInstaller bundles."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "agent"
    return Path(__file__).parent.parent


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.0
    max_retries: int = 3
    retry_delay_base: float = 1.0
    retry_delay_max: float = 30.0
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    fallback_model: str = ""


class DeviceConfig(BaseModel):
    host: str = "192.168.1.100"
    port: int = 18765
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


def _exe_config_path() -> Optional[Path]:
    """Check for a user config.yaml next to the EXE (frozen) or in CWD (dev)."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "config.yaml")
    candidates.append(Path.cwd() / "config.yaml")
    for p in candidates:
        if p.exists():
            return p
    return None


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file, with env var interpolation.

    Priority: explicit path > config.yaml next to EXE > bundled default.yaml
    """
    if config_path is None:
        user_path = _exe_config_path()
        if user_path:
            config_path = str(user_path)
        else:
            config_path = str(_data_dir() / "config" / "default.yaml")

    with open(config_path, encoding="utf-8") as f:
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

"""Configuration wizard for first-time setup."""

import os
import sys


def run_wizard() -> dict:
    """Interactive setup wizard. Returns config overrides dict."""
    print("=== Android Agent Setup Wizard ===\n")

    overrides = {}

    # LLM config
    print("--- LLM Configuration ---")
    base_url = input(f"OpenAI-compatible API base URL [https://api.openai.com/v1]: ").strip()
    if base_url:
        overrides["llm"] = overrides.get("llm", {})
        overrides["llm"]["base_url"] = base_url

    api_key = input("API key: ").strip()
    if api_key:
        overrides["llm"] = overrides.get("llm", {})
        overrides["llm"]["api_key"] = api_key

    model = input("Model name [gpt-4o]: ").strip()
    if model:
        overrides["llm"] = overrides.get("llm", {})
        overrides["llm"]["model"] = model

    # Device config
    print("\n--- Device Configuration ---")
    host = input("Android device IP address [192.168.1.100]: ").strip()
    if host:
        overrides["device"] = overrides.get("device", {})
        overrides["device"]["host"] = host

    port_str = input("WebSocket port [8765]: ").strip()
    if port_str:
        overrides["device"] = overrides.get("device", {})
        overrides["device"]["port"] = int(port_str)

    # Whisper config
    print("\n--- Whisper Configuration ---")
    print("Model sizes: tiny, base, small, medium, large-v3")
    model_size = input("Model size [small]: ").strip()
    if model_size:
        overrides["whisper"] = overrides.get("whisper", {})
        overrides["whisper"]["model_size"] = model_size

    # Interaction mode
    print("\n--- Interaction Mode ---")
    print("1. Single command (execute one command then exit)")
    print("2. Continuous dialogue (stay alive for multiple commands)")
    choice = input("Select [1]: ").strip()
    if choice == "2":
        overrides["interaction"] = overrides.get("interaction", {})
        overrides["interaction"]["mode"] = "continuous"

    # Safety
    print("\n--- Safety ---")
    print("Confirmation level: none, sensitive, all")
    level = input("Confirmation level [sensitive]: ").strip()
    if level:
        overrides["safety"] = overrides.get("safety", {})
        overrides["safety"]["confirmation_level"] = level

    print("\nSetup complete! Run 'android-agent' to start.")
    return overrides

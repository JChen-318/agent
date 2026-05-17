"""Context window management for LLM conversations."""

from dataclasses import dataclass


@dataclass
class ContextBudget:
    max_messages: int = 40
    max_ui_tree_chars: int = 8000
    max_tool_results_chars: int = 2000


class ContextManager:
    """Manages LLM context window size by trimming history and compressing UI trees."""

    def __init__(self, budget: ContextBudget | None = None):
        self.budget = budget or ContextBudget()
        self.ui_tree_cache: dict[str, str] = {}  # hash → formatted tree

    def trim_messages(self, messages: list[dict]) -> list[dict]:
        """Keep the most recent messages within budget."""
        if len(messages) <= self.budget.max_messages:
            return messages
        # Always keep system message + last N messages
        system_msgs = [m for m in messages if m["role"] == "system"]
        other_msgs = [m for m in messages if m["role"] != "system"]
        kept = system_msgs + other_msgs[-(self.budget.max_messages - len(system_msgs)):]
        return kept

    def compress_tool_result(self, result: str) -> str:
        """Truncate a tool result to fit budget."""
        max_len = self.budget.max_tool_results_chars
        if len(result) <= max_len:
            return result
        return result[:max_len - 3] + "..."

    def should_refresh_ui(self, old_hash: str | None, new_hash: str) -> bool:
        """Check if UI has changed significantly."""
        return old_hash != new_hash

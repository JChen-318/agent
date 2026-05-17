"""LLM response parser — extracts tool calls and text from responses."""

from agent.llm.client import LLMResponse


def is_tool_call(response: LLMResponse) -> bool:
    return len(response.tool_calls) > 0


def is_text_response(response: LLMResponse) -> bool:
    return bool(response.content)


def extract_tool_names(response: LLMResponse) -> list[str]:
    return [tc.name for tc in response.tool_calls]


def get_task_complete_summary(response: LLMResponse) -> str:
    """Extract task_complete summary if present."""
    for tc in response.tool_calls:
        if tc.name == "task_complete":
            return tc.arguments.get("summary", "Task completed")
    return ""

"""OpenAI-compatible LLM client wrapper."""

import asyncio
import json
import logging
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"


class LLMClient:
    """Async wrapper around OpenAI-compatible chat completions API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.0,
        max_retries: int = 3,
        retry_delay_base: float = 1.0,
        retry_delay_max: float = 30.0,
        fallback_base_url: str = "",
        fallback_api_key: str = "",
        fallback_model: str = "",
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_delay_base = retry_delay_base
        self.retry_delay_max = retry_delay_max
        self.base_url = base_url
        self.api_key = api_key
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key or "sk-placeholder")

        self._fallback_client: Optional[AsyncOpenAI] = None
        self._fallback_model: str = ""
        self._fallback_base_url = fallback_base_url
        self._fallback_api_key = fallback_api_key
        if fallback_base_url and fallback_api_key:
            self._fallback_client = AsyncOpenAI(base_url=fallback_base_url, api_key=fallback_api_key)
            self._fallback_model = fallback_model or model

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Send messages to the LLM and parse the response. Retries on transient errors."""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.client.chat.completions.create(**kwargs)
                choice = resp.choices[0]
                msg = choice.message

                tool_calls = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append(ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        ))

                # Parse XML-format tool calls from content (for models without native tool calling)
                content = msg.content
                if content and "<tool_call>" in content:
                    xml_tool_calls, content = _extract_xml_tool_calls(content)
                    tool_calls.extend(xml_tool_calls)

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=choice.finish_reason or "stop",
                )

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = min(
                        self.retry_delay_base * (2 ** attempt) + random.uniform(0, 0.5),
                        self.retry_delay_max,
                    )
                    logger.warning(
                        f"LLM API error (attempt {attempt+1}/{self.max_retries+1}): {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"LLM API failed after {self.max_retries+1} attempts: {e}")

        # All retries exhausted — try fallback if configured
        if self._fallback_client:
            logger.warning(f"Primary LLM failed, trying fallback: {self._fallback_model}")
            try:
                kwargs["model"] = self._fallback_model
                resp = await self._fallback_client.chat.completions.create(**kwargs)
                choice = resp.choices[0]
                msg = choice.message

                tool_calls = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append(ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        ))

                # Parse XML-format tool calls from content (for models without native tool calling)
                content = msg.content
                if content and "<tool_call>" in content:
                    xml_tool_calls, content = _extract_xml_tool_calls(content)
                    tool_calls.extend(xml_tool_calls)

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=choice.finish_reason or "stop",
                )
            except Exception as e:
                raise RuntimeError(
                    f"Both primary and fallback LLM failed. Primary: {last_error}. Fallback: {e}"
                ) from e

        raise RuntimeError(f"LLM API failed after {self.max_retries+1} attempts: {last_error}")

    async def chat_raw(self, messages: list[dict], **kwargs) -> str:
        """Simple chat returning raw text content. No tool calling."""
        kwargs.setdefault("model", self.model)
        kwargs.setdefault("max_tokens", self.max_tokens)
        kwargs.setdefault("temperature", self.temperature)
        kwargs["messages"] = messages

        resp = await self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


def _extract_xml_tool_calls(content: str) -> tuple[list[ToolCall], str]:
    """Extract XML-format tool calls from text content.

    Handles:
      <tool_call>
      function=screenshot
      </tool_call>

    And:
      <tool_call>{"name": "screenshot"}</tool_call>
    """
    tool_calls = []
    pattern = r"<tool_call>\s*(.*?)\s*</tool_call>"

    def replace_tool_call(match):
        body = match.group(1).strip()

        # Try JSON format first
        if body.startswith("{"):
            try:
                data = json.loads(body)
                name = data.get("name") or data.get("function") or ""
                args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
                if name:
                    tool_calls.append(ToolCall(
                        id=f"xml_{uuid.uuid4().hex[:8]}",
                        name=name.strip(),
                        arguments=args if isinstance(args, dict) else {},
                    ))
                return ""
            except json.JSONDecodeError:
                pass

        # Try function=NAME format
        func_match = re.search(r"function\s*=\s*(\S+)", body)
        if func_match:
            func_name = func_match.group(1).rstrip(">")
            tool_calls.append(ToolCall(
                id=f"xml_{uuid.uuid4().hex[:8]}",
                name=func_name.strip(),
                arguments={},
            ))
            return ""

        # Try <function>NAME</function> format
        func_match = re.search(r"<function>\s*(.*?)\s*</function>", body, re.DOTALL)
        if func_match:
            tool_calls.append(ToolCall(
                id=f"xml_{uuid.uuid4().hex[:8]}",
                name=func_match.group(1).strip(),
                arguments={},
            ))
            return ""

        return ""

    cleaned = re.sub(pattern, replace_tool_call, content, flags=re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return tool_calls, cleaned

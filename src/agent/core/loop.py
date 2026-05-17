"""Main ReAct agent loop — the central orchestration engine."""

import asyncio
import logging
from typing import Optional

from agent.core.state import GlobalState, InteractionMode, SafetyLevel
from agent.core.context import ContextManager, ContextBudget
from agent.llm.client import LLMClient, LLMResponse
from agent.llm.prompts import SYSTEM_PROMPT, FEW_SHOT_EXAMPLES
from agent.llm.tools import TOOL_DEFINITIONS
from agent.device.bridge import DeviceBridge
from agent.device.executor import ActionExecutor

logger = logging.getLogger(__name__)


class AgentLoop:
    """Central ReAct loop: observe → think → act → observe."""

    def __init__(
        self,
        llm_client: LLMClient,
        device_bridge: DeviceBridge,
        mode: InteractionMode = InteractionMode.SINGLE_COMMAND,
        safety_level: SafetyLevel = SafetyLevel.SENSITIVE,
    ):
        self.llm = llm_client
        self.device = device_bridge
        self.executor = ActionExecutor(device_bridge)
        self.state = GlobalState(mode=mode, safety_level=safety_level)
        self.context_mgr = ContextManager()
        self.running = False

        # Callbacks
        self.on_speak: Optional[callable] = None
        self.on_action: Optional[callable] = None

    async def run(self):
        """Connect to device and enter the main loop."""
        self.running = True
        await self.device.connect()
        logger.info("Agent connected to device. Entering main loop.")

        if self.state.mode == InteractionMode.CONTINUOUS:
            await self._continuous_loop()
        else:
            await self._command_loop()

    async def _command_loop(self):
        """Process a single user request, then exit."""
        user_input = await self._get_input()
        if user_input:
            await self._process_request(user_input)
        self.running = False

    async def _continuous_loop(self):
        """Stay alive, process requests sequentially."""
        while self.running:
            user_input = await self._get_input()
            if user_input is None:
                continue
            if user_input.lower().strip() in ("exit", "quit", "stop", "退出"):
                self.running = False
                break
            self.state.reset()
            await self._process_request(user_input)

    async def _get_input(self) -> Optional[str]:
        """Get user input. Subclass or inject a transcriber to add voice support."""
        import sys

        if sys.stdin.isatty():
            try:
                text = input(">>> ").strip()
                return text if text else None
            except (EOFError, KeyboardInterrupt):
                return None
        return None

    async def process_text_input(self, text: str) -> LLMResponse:
        """Public API: process a text command directly. Returns final LLM response."""
        await self._process_request(text)
        # Return the last assistant message
        for msg in reversed(self.state.messages):
            if msg["role"] == "assistant":
                return LLMResponse(content=msg.get("content"))
        return LLMResponse(content="No response")

    async def _process_request(self, user_input: str) -> None:
        """Run the ReAct loop for a single user request."""
        self.state.add_message("user", user_input)
        logger.info(f"Processing request: {user_input}")

        while self.running:
            # 1. Capture device state
            try:
                ui_tree = await self.device.get_ui_tree()
                self.state.update_device_state(ui_tree)
            except Exception as e:
                logger.warning(f"Failed to get UI tree: {e}")
                ui_tree = {}

            # 2. Build LLM context
            messages = self._build_messages(user_input)

            # 3. Call LLM
            response = await self.llm.chat(
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )

            # 4. Process response
            if response.tool_calls:
                for tool_call in response.tool_calls:
                    action = tool_call.name
                    args = tool_call.arguments

                    if action == "task_complete":
                        summary = args.get("summary", "Done")
                        self.state.add_message("assistant", summary)
                        logger.info(f"Task complete: {summary}")
                        if self.on_speak:
                            self.on_speak(summary)
                        return

                    if action == "ask_user":
                        question = args.get("question", "")
                        logger.info(f"LLM asks: {question}")
                        self.state.add_message("assistant", question)
                        if self.on_speak:
                            self.on_speak(question)
                        # In continuous mode, wait for user response
                        answer = await self._get_input()
                        self.state.add_message("user", answer or "ok")
                        break  # Re-enter reasoning with user's answer

                    # Safety check
                    block_reason = self.state.check_safety(action, args)
                    if block_reason:
                        result = {"error": block_reason, "blocked": True}
                        self.state.add_message("tool", result, tool_call_id=tool_call.id)
                        continue

                    # Confirmation check
                    if self.state.needs_confirmation(action, args):
                        logger.info(f"Confirmation needed for: {action}({args})")
                        print(f"\n[CONFIRM] {action}: {args}")
                        resp = input("Proceed? (y/N): ").strip().lower()
                        if resp != "y":
                            self.state.add_message(
                                "tool",
                                {"error": "Cancelled by user", "cancelled": True},
                                tool_call_id=tool_call.id,
                            )
                            continue

                    # Execute action
                    logger.info(f"Executing: {action}({args})")
                    if self.on_action:
                        self.on_action(action, args)

                    result = await self.device.execute(action, args)
                    self.state.add_message("tool", result, tool_call_id=tool_call.id)

                    # Handle errors
                    if result.get("status") == "error":
                        logger.warning(f"Action error: {result.get('error')}")
            else:
                # Text response — LLM wants to communicate
                text = response.content
                if text:
                    logger.info(f"LLM: {text}")
                    self.state.add_message("assistant", text)
                    if self.on_speak:
                        self.on_speak(text)

                # In single-command mode, text response ends the loop
                if self.state.mode == InteractionMode.SINGLE_COMMAND:
                    return

            # Prevent infinite loops
            if self.state.iteration_count > self.state.max_iterations:
                logger.warning("Max iterations reached, stopping loop")
                self.state.add_message("assistant", "Reached maximum number of steps. Stopping.")
                return

    def _build_messages(self, user_input: str) -> list[dict]:
        """Build message list for the LLM call."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Add few-shot examples
        messages.extend(FEW_SHOT_EXAMPLES)

        # Add conversation history
        history = self.state.get_recent_history(20)
        messages.extend(history)

        # Inject current UI tree
        ui_text = self.state.format_ui_tree_for_llm()
        if not any(m.get("role") == "user" and "Current screen UI" in str(m.get("content", ""))
                   for m in messages[-3:]):
            messages.append({
                "role": "user",
                "content": f"[Current screen UI state]:\n{ui_text}",
            })

        # Trim to budget
        messages = self.context_mgr.trim_messages(messages)

        return messages

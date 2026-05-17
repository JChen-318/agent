"""Main ReAct agent loop — the central orchestration engine."""

import asyncio
import json
import logging
import random
from typing import Optional, TYPE_CHECKING

from agent.core.state import GlobalState, InteractionMode, SafetyLevel
from agent.core.context import ContextManager, ContextBudget
from agent.llm.client import LLMClient, LLMResponse
from agent.llm.prompts import SYSTEM_PROMPT, FEW_SHOT_EXAMPLES
from agent.llm.tools import TOOL_DEFINITIONS
from agent.device.bridge import DeviceBridge
from agent.device.executor import ActionExecutor

if TYPE_CHECKING:
    from agent.speech.transcriber import Transcriber
    from agent.core.planner import Planner

logger = logging.getLogger(__name__)


class AgentLoop:
    """Central ReAct loop: observe → think → act → observe."""

    def __init__(
        self,
        llm_client: LLMClient,
        device_bridge: DeviceBridge,
        mode: InteractionMode = InteractionMode.SINGLE_COMMAND,
        safety_level: SafetyLevel = SafetyLevel.SENSITIVE,
        transcriber: Optional["Transcriber"] = None,
        planner: Optional["Planner"] = None,
    ):
        self.llm = llm_client
        self.device = device_bridge
        self.executor = ActionExecutor(device_bridge)
        self.state = GlobalState(mode=mode, safety_level=safety_level)
        self.context_mgr = ContextManager()
        self.transcriber = transcriber
        self.planner = planner
        self.running = False

        # Optimization flags
        self._ui_fresh: bool = False
        self._last_action: Optional[str] = None

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
            if self._should_use_planning(user_input):
                await self._run_with_plan(user_input)
            else:
                await self._process_request(user_input)
        self.running = False

    async def _continuous_loop(self):
        """Stay alive, process requests sequentially."""
        while self.running:
            try:
                user_input = await self._get_input()
                if user_input is None:
                    continue
                if user_input.lower().strip() in ("exit", "quit", "stop", "退出"):
                    self.running = False
                    break
                self.state.reset()
                try:
                    if self._should_use_planning(user_input):
                        await self._run_with_plan(user_input)
                    else:
                        await self._process_request(user_input)
                except ConnectionError as e:
                    logger.error(f"Connection lost: {e}")
                    print(f"\n[ERROR] Connection to device lost: {e}")
                    if self.on_speak:
                        self.on_speak("Connection lost. Please check your phone.")
                    try:
                        await self.device.connect()
                        logger.info("Reconnected to device")
                    except Exception:
                        logger.error("Could not reconnect. Stopping.")
                        self.running = False
                        break
                except RuntimeError as e:
                    logger.error(f"Runtime error: {e}")
                    print(f"\n[ERROR] {e}")
                    self.state.add_message("assistant", f"Error: {e}. Please try again.")
                except Exception as e:
                    logger.exception(f"Unexpected error in continuous loop: {e}")
                    print(f"\n[ERROR] Unexpected error: {e}. Continuing...")
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.exception(f"Fatal error in outer loop: {e}")
                break

    async def _get_input(self) -> Optional[str]:
        """Get user input from voice (if transcriber is available) or stdin."""
        if self.transcriber is not None:
            print("\n[Voice] Listening... (press Enter for text input)")
            try:
                text = await self.transcriber.listen(timeout=10.0)
                if text:
                    print(f"[Voice] Recognized: {text}")
                    return text
                print("[Voice] Nothing heard, falling back to text input.")
            except Exception:
                logger.warning("Voice input failed, falling back to text input.")

        import sys
        try:
            if sys.stdin and sys.stdin.isatty():
                text = input(">>> ").strip()
                return text if text else None
        except (EOFError, KeyboardInterrupt, AttributeError):
            return None
        return None

    def _should_use_planning(self, user_input: str) -> bool:
        """Heuristic: use planning for complex multi-step goals."""
        if self.planner is None:
            return False
        words = user_input.split()
        if len(words) >= 8:
            return True
        complex_verbs = {"send", "search", "find", "post", "create", "delete", "share",
                         "schedule", "book", "order", "buy", "play", "navigate",
                         "发", "搜", "找", "购", "订", "买", "预约", "导航", "播放"}
        return any(v in user_input.lower() for v in complex_verbs)

    async def process_text_input(self, text: str) -> LLMResponse:
        """Public API: process a text command directly. Returns final LLM response."""
        self.running = True
        try:
            if self._should_use_planning(text):
                await self._run_with_plan(text)
            else:
                await self._process_request(text)
        finally:
            self.running = False
        for msg in reversed(self.state.messages):
            if msg["role"] == "assistant":
                return LLMResponse(content=msg.get("content"))
        return LLMResponse(content="No response")

    async def _run_with_plan(self, user_input: str) -> None:
        """Decompose a complex goal into sub-tasks and run each through the ReAct loop."""
        self.state.add_message("user", user_input)
        logger.info(f"Planning: {user_input}")

        plan = await self.planner.decompose(user_input)
        self.state.current_plan = plan
        logger.info(f"Plan: {len(plan.sub_tasks)} sub-tasks")

        while self.running:
            task = self.planner.get_next_task(plan)
            if task is None:
                logger.info("No more pending tasks (all completed or blocked)")
                break

            task.status = "in_progress"
            self.state.current_task = task
            self.state.reset_iteration()
            self.state.task_results.append({
                "id": task.id, "description": task.description, "status": "in_progress",
            })

            progress = self.planner.format_plan_progress(plan, task)
            self.state.add_message("user", progress)
            logger.info(f"Sub-task [{task.id}]: {task.description}")

            try:
                await self._process_sub_task(task.description)
                self.planner.mark_completed(task)
                self.state.task_results[-1]["status"] = "completed"
                logger.info(f"Sub-task [{task.id}] completed")
            except Exception as e:
                self.planner.mark_failed(task, str(e))
                self.state.task_results[-1]["status"] = "failed"
                self.state.task_results[-1]["error"] = str(e)
                logger.error(f"Sub-task [{task.id}] FAILED: {e}")

        completed = sum(1 for t in plan.sub_tasks if t.status == "completed")
        failed = sum(1 for t in plan.sub_tasks if t.status == "failed")
        if completed == len(plan.sub_tasks):
            summary = f"All tasks complete: {plan.goal} ({completed}/{len(plan.sub_tasks)})"
        else:
            summary = f"Goal partially complete: {completed}/{len(plan.sub_tasks)} done, {failed} failed."
        self.state.add_message("assistant", summary)
        logger.info(summary)
        if self.on_speak:
            self.on_speak(summary)

        self.state.current_plan = None
        self.state.current_task = None

    async def _process_request(self, user_input: str) -> None:
        """Run the ReAct loop for a single user request (no task decomposition)."""
        self.state.add_message("user", user_input)
        logger.info(f"Processing: {user_input}")
        exit_on_text = self.state.mode == InteractionMode.SINGLE_COMMAND
        await self._re_act_loop(exit_on_text=exit_on_text)

    async def _process_sub_task(self, task_description: str) -> None:
        """Run the ReAct loop for a single sub-task (always waits for task_complete)."""
        await self._re_act_loop(exit_on_text=False)

    async def _re_act_loop(self, *, exit_on_text: bool) -> None:
        """Core ReAct loop: observe → think → act. Repeat until task_complete or max iterations."""

        while self.running:
            # 1. Capture device state (skip if UI was just refreshed by LLM)
            if self._ui_fresh:
                self._ui_fresh = False
                self.state.iteration_count += 1
            else:
                try:
                    ui_tree = await self.device.get_ui_tree()
                    self.state.update_device_state(ui_tree)
                except Exception as e:
                    logger.warning(f"Failed to get UI tree: {e}")

            # 2. Build LLM context
            messages = self._build_messages()

            # 3. Call LLM
            logger.info(f"Calling LLM ({len(messages)} messages)...")
            response = await self.llm.chat(
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
            logger.info(
                f"LLM response: content={bool(response.content)} "
                f"tool_calls={len(response.tool_calls)} "
                f"finish={response.finish_reason}"
            )

            # 4. Process response
            if response.tool_calls:
                serialized_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                self.state.add_message("assistant", None, tool_calls=serialized_calls)

                for tool_call in response.tool_calls:
                    action = tool_call.name
                    args = tool_call.arguments
                    self._last_action = action

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
                        answer = await self._get_input()
                        self.state.add_message("user", answer or "ok")
                        break

                    if action == "get_ui_tree":
                        self._ui_fresh = True

                    # Safety check
                    block_reason = self.state.check_safety(action, args)
                    if block_reason:
                        result = {"error": block_reason, "blocked": True}
                        self.state.add_message("tool", result, tool_call_id=tool_call.id)
                        continue

                    # Confirmation check
                    if self.state.needs_confirmation(action, args):
                        logger.info(f"Confirmation needed for: {action}({args})")
                        try:
                            print(f"\n[CONFIRM] {action}: {args}")
                            resp = input("Proceed? (y/N): ").strip().lower()
                            if resp != "y":
                                self.state.add_message(
                                    "tool",
                                    {"error": "Cancelled by user", "cancelled": True},
                                    tool_call_id=tool_call.id,
                                )
                                continue
                        except EOFError:
                            pass  # Non-interactive mode: auto-confirm

                    # Anti-detection: coordinate jitter
                    if action in ("click", "long_press", "swipe"):
                        args = dict(args)
                        for coord_key in ("x", "y", "x1", "y1", "x2", "y2"):
                            if coord_key in args and isinstance(args[coord_key], (int, float)):
                                args[coord_key] = max(0, int(args[coord_key]) + random.randint(-3, 3))

                    # Resolve app package names in launch_app actions
                    from agent.nlu.app_mapper import resolve_action_args
                    args = resolve_action_args(action, args)

                    # Execute action
                    logger.info(f"Executing: {action}({args})")
                    if self.on_action:
                        self.on_action(action, args)

                    result = await self.device.execute(action, args)
                    self.state.add_message("tool", result, tool_call_id=tool_call.id)

                    if result.get("status") == "error":
                        logger.warning(f"Action error: {result.get('error')}")

                    # Anti-detection: random micro-delay between actions
                    await asyncio.sleep(random.uniform(0.05, 0.25))
            else:
                text = response.content
                if text:
                    logger.info(f"LLM: {text}")
                    self.state.add_message("assistant", text)
                    if self.on_speak:
                        self.on_speak(text)

                if exit_on_text:
                    return

            # Prevent infinite loops
            max_iters = self.state.max_iterations if self.state.current_plan is None else 30
            if self.state.iteration_count > max_iters:
                logger.warning(f"Max iterations ({max_iters}) reached, stopping loop")
                self.state.add_message("assistant", f"Reached maximum steps ({max_iters}). Stopping.")
                return

    def _build_messages(self) -> list[dict]:
        """Build message list for the LLM call."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Add few-shot examples
        messages.extend(FEW_SHOT_EXAMPLES)

        # Add conversation history
        history = self.state.get_recent_history(40)
        messages.extend(history)

        # Inject current UI tree
        ui_text = self.state.format_ui_tree_for_llm()

        # Add screen recognition annotation
        if self.state.ui_tree:
            from agent.nlu.screen_recognizer import recognize_app, recognize_page_type
            app = recognize_app(self.state.ui_tree)
            page = recognize_page_type(self.state.ui_tree)
            if app or page:
                ui_text = f"[App: {app or '?'}] [Page: {page}]\n{ui_text}"

        # Annotate if UI tree is unchanged from previous iteration
        if self.state._prev_ui_hash and self.state.last_ui_hash == self.state._prev_ui_hash:
            ui_text = "[Note: UI tree unchanged since last action.]\n" + ui_text

        if not any(m.get("role") == "user" and "Current screen UI" in str(m.get("content", ""))
                   for m in messages[-3:]):
            messages.append({
                "role": "user",
                "content": f"[Current screen UI state]:\n{ui_text}",
            })

        # Trim to budget
        messages = self.context_mgr.trim_messages(messages)

        return messages

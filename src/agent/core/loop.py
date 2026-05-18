"""Main ReAct agent loop — the central orchestration engine."""

import asyncio
import hashlib
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
from agent.nlu.rule_engine import match as rule_match, get_next_action as rule_next_action
from agent.nlu.path_cache import PathCache

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
        self._last_ui_hash: Optional[str] = None

        # Decision cache: (ui_hash, user_intent) → list of tool calls
        self._decision_cache: dict[tuple, list] = {}
        self._cache_max_size = 100
        self._recently_popped: set = set()  # Keys popped this iteration, skip re-cache

        # Pre-fetched UI tree (captured during LLM call)
        self._prefetched_ui: Optional[dict] = None

        # Path cache for known app UI patterns
        self._path_cache = PathCache()

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
        """Heuristic: use planning for multi-step goals (lowered threshold)."""
        if self.planner is None:
            return False
        words = user_input.split()
        if len(words) >= 5:  # lowered from 8
            return True
        # Multi-step connectors
        connectors = {"然后", "之后", "并且", "再", "接着", "and", "then", "also", "after"}
        if any(c in user_input.lower() for c in connectors):
            return True
        complex_verbs = {"send", "search", "find", "post", "create", "delete", "share",
                         "schedule", "book", "order", "buy", "play", "navigate",
                         "发", "搜", "找", "购", "订", "买", "预约", "导航", "播放",
                         "搜索", "发送", "分享", "下单", "预定"}
        return any(v in user_input.lower() for v in complex_verbs)

    async def process_text_input(self, text: str) -> LLMResponse:
        """Public API: process a text command directly. Returns final LLM response."""
        self.running = True
        self.state.reset()
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
        self._recently_popped.clear()
        logger.info(f"Processing: {user_input}")
        exit_on_text = self.state.mode == InteractionMode.SINGLE_COMMAND
        await self._re_act_loop(exit_on_text=exit_on_text)

    async def _process_sub_task(self, task_description: str) -> None:
        """Run the ReAct loop for a single sub-task (always waits for task_complete)."""
        self._recently_popped.clear()
        await self._re_act_loop(exit_on_text=False)

    async def _re_act_loop(self, *, exit_on_text: bool) -> None:
        """Core ReAct loop: observe → think → batch act. Repeat until task_complete."""

        while self.running:
            self.state.iteration_count += 1

            # Prevent infinite loops
            max_iters = self.state.max_iterations if self.state.current_plan is None else 30
            if self.state.iteration_count > max_iters:
                logger.warning(f"Max iterations ({max_iters}) reached")
                self.state.add_message("assistant", f"Reached maximum steps ({max_iters}). Stopping.")
                return

            # Cache check: same UI + same last user message
            if self.state.last_ui_hash and self._last_ui_hash:
                cache_key = (self.state.last_ui_hash, self._user_intent_hash())
                if cache_key in self._decision_cache:
                    cached_calls = self._decision_cache.pop(cache_key)
                    self._recently_popped.add(cache_key)
                    logger.info(f"Cache hit — replaying {len(cached_calls)} tool calls")
                    had_effect = False
                    for tool_call in cached_calls:
                        await self._execute_tool_call(tool_call)
                        if tool_call["_name"] == "task_complete":
                            return
                        if tool_call["_name"] == "ask_user":
                            break
                        had_effect = True
                    if had_effect:
                        continue

            self._last_ui_hash = self.state.last_ui_hash

            # 1. Capture device state + Pre-fetch UI for next iteration
            if self._ui_fresh:
                self._ui_fresh = False
                self.state.iteration_count += 1
            elif self._prefetched_ui:
                self.state.update_device_state(self._prefetched_ui)
                self._prefetched_ui = None
            else:
                try:
                    ui_tree = await self.device.get_ui_tree()
                    self.state.update_device_state(ui_tree)
                except Exception as e:
                    logger.warning(f"Failed to get UI tree: {e}")

            # 2. Build LLM context (pruned UI tree)
            messages = self._build_messages()

            # 3. Rule engine check — bypass LLM for predictable operations
            response = await self._try_rules()
            if response is not None:
                # Rule engine returned tool calls — process them
                tool_calls = response
                serialized = [
                    {
                        "id": tc.get("id", f"rule_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc["_name"],
                            "arguments": json.dumps(
                                {k: v for k, v in tc.items() if k not in ("_name", "id")},
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ]
                self.state.add_message("assistant", None, tool_calls=serialized)

                for tc in tool_calls:
                    action = tc["_name"]
                    args = {k: v for k, v in tc.items() if k not in ("_name", "id")}

                    if action == "task_complete":
                        summary = args.get("summary", "Done")
                        self.state.add_message("assistant", summary)
                        logger.info(f"Rule: task_complete — {summary}")
                        if self.on_speak:
                            self.on_speak(summary)
                        return

                    if action == "ask_user":
                        break

                    # Safety check
                    block_reason = self.state.check_safety(action, args)
                    if block_reason:
                        self.state.add_message("tool", {"error": block_reason, "blocked": True},
                                              tool_call_id=tc.get("id"))
                        continue

                    # Execute action
                    logger.info(f"Rule: executing {action}({args})")
                    if self.on_action:
                        self.on_action(action, args)
                    result = await self.device.execute(action, args)
                    self.state.add_message("tool", result, tool_call_id=tc.get("id"))

                    if result.get("status") == "error":
                        logger.warning(f"Rule action error: {result.get('error')}")

                    await asyncio.sleep(random.uniform(0.02, 0.08))

                continue  # Rule actions done, loop for next iteration

            # 4. Call LLM (asynchronously pre-fetch next UI if action will cause change)
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

            # 5. Process ALL tool calls in batch before re-calling LLM
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

                # Track execution results for caching decision
                cache_key = (self._last_ui_hash, self._user_intent_hash())
                all_succeeded = True
                cached_specs = []
                for tc in response.tool_calls:
                    cached_specs.append({"_name": tc.name, "id": tc.id, **tc.arguments})

                # Track if we need UI refresh after batch
                ui_changing_actions = 0
                need_ui_refresh = False

                # Start background UI fetch while executing tools (parallelize LLM+UI)
                _screen_changers = {"launch_app", "click", "click_by_text", "back", "home",
                                    "recent_apps", "scroll", "swipe", "long_press"}
                _has_changers = any(tc.name in _screen_changers for tc in response.tool_calls)
                _bg_ui_fetch: "Optional[asyncio.Task]" = None
                if _has_changers:
                    _bg_ui_fetch = asyncio.create_task(self.device.get_ui_tree())

                for i, tool_call in enumerate(response.tool_calls):
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
                        break  # Break batch on ask_user

                    if action == "get_ui_tree":
                        self._ui_fresh = True
                        continue  # Skip execution, mark as fresh

                    # Track screen-changing actions
                    if action in ("launch_app", "click", "click_by_text", "back", "home",
                                 "recent_apps", "scroll", "swipe", "long_press"):
                        ui_changing_actions += 1
                        if ui_changing_actions >= 1 and i < len(response.tool_calls) - 1:
                            need_ui_refresh = True

                    # Safety check
                    block_reason = self.state.check_safety(action, args)
                    if block_reason:
                        result = {"error": block_reason, "blocked": True}
                        self.state.add_message("tool", result, tool_call_id=tool_call.id)
                        continue

                    # Confirmation check (auto-confirm in non-interactive mode)
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
                            pass

                    # Anti-detection: coordinate jitter (only for click/long_press/swipe)
                    if action in ("click", "long_press", "swipe"):
                        args = dict(args)
                        for coord_key in ("x", "y", "x1", "y1", "x2", "y2"):
                            if coord_key in args and isinstance(args[coord_key], (int, float)):
                                args[coord_key] = max(0, int(args[coord_key]) + random.randint(-3, 3))

                    # Resolve app package names
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
                        all_succeeded = False

                    await asyncio.sleep(random.uniform(0.02, 0.10))

                # Cache successful tool calls for future reuse
                if all_succeeded and cache_key not in self._recently_popped:
                    self._add_to_cache(cache_key, cached_specs)

                    # Also save to path cache (app/page level, durable)
                    try:
                        from agent.nlu.screen_recognizer import recognize_app, recognize_page_type
                        app = recognize_app(self.state.ui_tree) or "unknown"
                        page = recognize_page_type(self.state.ui_tree) or "unknown"
                        self._path_cache.set(app, page, self._user_intent_hash(), cached_specs)
                    except Exception:
                        pass

                # Pre-fetch UI after screen-changing batch (reuse background fetch if ready)
                if need_ui_refresh:
                    try:
                        if _bg_ui_fetch and _bg_ui_fetch.done():
                            self._prefetched_ui = _bg_ui_fetch.result()
                        elif _bg_ui_fetch:
                            self._prefetched_ui = await _bg_ui_fetch
                        else:
                            self._prefetched_ui = await self.device.get_ui_tree()
                        self._ui_fresh = True
                    except Exception:
                        if _bg_ui_fetch and not _bg_ui_fetch.done():
                            _bg_ui_fetch.cancel()
                    finally:
                        _bg_ui_fetch = None
            else:
                text = response.content
                if text:
                    logger.info(f"LLM: {text}")
                    self.state.add_message("assistant", text)
                    if self.on_speak:
                        self.on_speak(text)

                if exit_on_text:
                    return

            # (iteration limit checked at loop start)
    async def _try_rules(self) -> Optional[list[dict]]:
        """Check if the rule engine can handle the current situation.
        Returns list of tool calls (with _name keys) or None to fall through to LLM.
        """
        # Get the last user message
        last_user_text = ""
        for msg in reversed(self.state.messages):
            if msg["role"] == "user" and msg.get("content"):
                content = msg["content"]
                if not content.startswith("[UI") and not content.startswith("[App"):
                    last_user_text = content
                    break

        if not last_user_text:
            return None

        # First iteration: check intent rules (no history beyond system prompt + first message)
        history = self.state.get_recent_history(30)
        user_msg_count = sum(1 for m in history if m["role"] == "user"
                            and not str(m.get("content", "")).startswith("[UI"))
        if user_msg_count <= 1:
            result = rule_match(last_user_text, self.state.ui_tree)
            if result:
                return result

        # Subsequent iterations: check screen-level rules for single obvious next step
        if self.state.ui_tree:
            next_action = rule_next_action(self.state.ui_tree)
            if next_action:
                if self.state.iteration_count > 0:
                    logger.info(f"Rule: auto screen action {next_action['_name']}")
                    return [next_action]

        # Check path cache before falling back to LLM
        if self.state.ui_tree and last_user_text:
            try:
                from agent.nlu.screen_recognizer import recognize_app, recognize_page_type
                app = recognize_app(self.state.ui_tree) or "unknown"
                page = recognize_page_type(self.state.ui_tree) or "unknown"
                cached = self._path_cache.get(app, page, last_user_text)
                if cached:
                    logger.info(f"Path cache hit: {app}/{page}")
                    return cached
            except Exception:
                pass

        return None

    async def _execute_tool_call(self, tc: dict) -> None:
        """Replay a cached tool call without LLM."""
        action = tc["_name"]
        args = {k: v for k, v in tc.items() if k not in ("_name", "id")}
        if action in ("task_complete", "ask_user"):
            return
        result = await self.device.execute(action, args)
        self.state.add_message("tool", result, tool_call_id=tc.get("id", "cached"))
        if result.get("status") == "error":
            logger.warning(f"Cached action error: {result.get('error')}")

    def _user_intent_hash(self) -> str:
        """Hash the last user message to use as cache key."""
        for msg in reversed(self.state.messages):
            if msg["role"] == "user":
                content = msg["content"] or ""
                return hashlib.md5(content.encode()).hexdigest()[:8]
        return "none"

    def _add_to_cache(self, key: tuple, tool_calls: list) -> None:
        """Add decision to cache with LRU eviction."""
        if key in self._decision_cache:
            return
        if len(self._decision_cache) >= self._cache_max_size:
            # Pop oldest entry
            oldest = next(iter(self._decision_cache))
            self._decision_cache.pop(oldest)
        self._decision_cache[key] = tool_calls

    def _build_messages(self) -> list[dict]:
        """Build message list for the LLM call — optimized context."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Add few-shot examples (only empty context, not after first iteration)
        history = self.state.get_recent_history(30)
        if len(history) <= 2:
            messages.extend(FEW_SHOT_EXAMPLES)
        messages.extend(history)

        # Inject current UI tree — only when changed or no UI yet
        ui_text = self.state.format_ui_tree_for_llm(prune=True)
        ui_changed = not (self.state._prev_ui_hash and
                         self.state.last_ui_hash == self.state._prev_ui_hash)

        if ui_changed:
            # Add app/page annotation
            if self.state.ui_tree:
                from agent.nlu.screen_recognizer import recognize_app, recognize_page_type
                app = recognize_app(self.state.ui_tree)
                page = recognize_page_type(self.state.ui_tree)
                if app or page:
                    ui_text = f"[App: {app or '?'}][Page: {page}]\n{ui_text}"

            messages.append({
                "role": "user",
                "content": f"[UI]:\n{ui_text}",
            })
        elif not any("[UI snapshot]" in str(m.get("content", "")) for m in messages[-2:]):
            # UI unchanged — inject compact summary to save tokens
            summary = self.state.get_ui_summary()
            messages.append({
                "role": "user",
                "content": f"[UI snapshot]:\n{summary}",
            })

        # Trim to budget
        messages = self.context_mgr.trim_messages(messages)

        return messages

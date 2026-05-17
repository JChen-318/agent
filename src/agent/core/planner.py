"""Task decomposition — breaks complex goals into ordered sub-tasks."""

import json
import logging
from dataclasses import dataclass, field

from agent.llm.client import LLMClient

logger = logging.getLogger(__name__)

PLAN_PROMPT = """Break the following user goal into an ordered list of sub-tasks for an Android automation agent.
Each sub-task should be a single, atomic action or verification step.
Output a JSON array of objects with these keys:
- "id": unique string identifier for this step
- "description": what to do in this step, in clear terms
- "depends_on": list of step IDs that must complete first (empty list if none)

User goal: {goal}

Return ONLY valid JSON array, no markdown formatting."""


@dataclass
class SubTask:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"


@dataclass
class TaskPlan:
    goal: str
    sub_tasks: list[SubTask]


class Planner:
    """Uses LLM to decompose complex goals into ordered sub-tasks."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def decompose(self, goal: str) -> TaskPlan:
        """Break a user goal into sub-tasks."""
        prompt = PLAN_PROMPT.format(goal=goal)
        try:
            response = await self.llm.chat_raw([{"role": "user", "content": prompt}])
            data = json.loads(response)
            sub_tasks = [SubTask(**item) for item in data]
            return TaskPlan(goal=goal, sub_tasks=sub_tasks)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Plan decomposition failed: {e}, using single-task fallback")
            return TaskPlan(
                goal=goal,
                sub_tasks=[SubTask(id="1", description=goal)],
            )

    def get_next_task(self, plan: TaskPlan) -> SubTask | None:
        """Get the next pending task whose dependencies are met."""
        for task in plan.sub_tasks:
            if task.status != "pending":
                continue
            deps_met = all(
                any(st.id == dep and st.status == "completed" for st in plan.sub_tasks)
                for dep in task.depends_on
            )
            if deps_met:
                return task
        return None

    def format_plan_progress(self, plan: TaskPlan, current_task: SubTask | None = None) -> str:
        """Format plan progress as text for LLM context injection."""
        status_icon = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[OK]",
            "failed": "[X]",
        }
        lines = ["## Task Plan", f"Goal: {plan.goal}", ""]
        for st in plan.sub_tasks:
            icon = status_icon.get(st.status, "[?]")
            lines.append(f"  {icon} {st.description}")
        if current_task:
            lines.append(f"\nNow executing: {current_task.description}")
            lines.append("Complete this sub-task, then call task_complete().")
        return "\n".join(lines)

    def mark_completed(self, task: SubTask) -> None:
        """Mark a sub-task as successfully completed."""
        task.status = "completed"

    def mark_failed(self, task: SubTask, reason: str = "") -> None:
        """Mark a sub-task as failed with an optional reason."""
        task.status = "failed"

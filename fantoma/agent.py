"""Fantoma Agent — convenience wrapper for vibe coders.

Provides run() — describe a task in English, the agent does it.
Delegates all browser operations to the Fantoma tool class.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from fantoma.browser_tool import Fantoma
from fantoma.llm.client import LLMClient
from fantoma.resilience.escalation import EscalationChain
from fantoma.planner import Planner, Subtask, Checkpoint
from fantoma.navigator import Navigator, NavigatorResult
from fantoma.state_tracker import StateTracker

log = logging.getLogger("fantoma")


@dataclass
class AgentResult:
    """Result of an agent.run() call."""
    success: bool
    data: Any = None
    steps_taken: int = 0
    steps_detail: list = None
    error: str = ""
    tokens_used: int = 0
    escalations: int = 0


class Agent:
    """Convenience wrapper — describe a task, the agent does it.

    Usage:
        agent = Agent(llm_url="http://localhost:8080/v1")
        result = agent.run("Go to HN and find the top post about AI")
    """

    def __init__(
        self,
        llm_url: str = "http://localhost:8080/v1",
        api_key: str = "",
        model: str = "auto",
        escalation: list[str] = None,
        escalation_keys: list[str] = None,
        max_steps: int = 50,
        sensitive_data: dict = None,
        **kwargs,
    ):
        self.fantoma = Fantoma(llm_url=llm_url, api_key=api_key, model=model, **kwargs)
        self._max_steps = max_steps
        self._sensitive_data = sensitive_data or {}

        endpoints = escalation or [llm_url]
        keys = escalation_keys or [api_key] + [""] * (len(endpoints) - 1)
        self.escalation = EscalationChain(endpoints, keys)
        self._llm = LLMClient(base_url=llm_url, api_key=api_key, model=model)
        self._planner = Planner(self._llm)
        self._navigator = Navigator()

    def run(self, task: str, start_url: str = None) -> AgentResult:
        """Run a browser task described in English."""
        log.info("Task: %s", task)
        self.fantoma._task = task

        start_domain = ""
        if start_url:
            try:
                start_domain = urlparse(start_url).netloc
            except Exception:
                pass

        try:
            state = self.fantoma.start(start_url)
        except Exception as e:
            return AgentResult(success=False, error=f"Browser start failed: {e}")

        total_steps = 0
        all_steps = []

        try:
            self._planner.reset()
            summary = self._get_page_summary()
            subtasks = self._planner.decompose(task, summary)
            completed = []      # list of (Subtask, NavigatorResult)
            checkpoints = []    # list of Checkpoint
            all_steps = []
            total_steps = 0
            remaining_budget = self._max_steps

            i = 0
            while i < len(subtasks) and remaining_budget > 0:
                subtask = subtasks[i]
                n_remaining = len(subtasks) - i
                step_budget = max(5, remaining_budget // max(1, n_remaining))
                tracker = StateTracker()

                result = self._navigator.execute(
                    subtask=subtask,
                    fantoma=self.fantoma,
                    llm=self._llm,
                    tracker=tracker,
                    max_steps=step_budget,
                    start_domain=start_domain,
                    sensitive_data=self._sensitive_data,
                )

                all_steps.extend(result.steps_detail)
                total_steps += result.steps_taken
                remaining_budget -= result.steps_taken

                if result.status == "done":
                    completed.append((subtask, result))
                    checkpoints.append(Checkpoint(
                        url=result.final_url,
                        subtask=subtask,
                        result_summary=result.data[:200],
                    ))
                    i += 1
                    continue

                # Stagnation, failure, or budget exhausted -- still save partial data
                if result.data and not result.data.startswith("Stopped:") and not result.data.startswith("Domain drift"):
                    completed.append((subtask, result))

                # Replan
                summary = self._get_page_summary()
                new_subtasks = self._planner.replan(task, completed, subtask, summary)
                if new_subtasks is None:
                    break
                # Replace remaining subtasks with new plan
                subtasks = subtasks[:i] + new_subtasks
                # Backtrack if we have a checkpoint
                if checkpoints:
                    try:
                        self.fantoma.navigate(checkpoints[-1].url)
                    except Exception:
                        pass
                continue  # Retry from same index with new subtask

            answer = self._planner.summarise(task, completed)
            return AgentResult(
                success=bool(completed),
                data=answer,
                steps_taken=total_steps,
                steps_detail=all_steps,
                escalations=self.escalation.total_escalations,
            )
        except Exception as e:
            return AgentResult(success=False, error=str(e),
                               steps_taken=total_steps,
                               steps_detail=all_steps)
        finally:
            self.fantoma.stop()

    def _get_page_summary(self) -> str:
        """Get a brief page summary for the planner (URL + title + headings + content)."""
        try:
            page = self.fantoma._engine.get_page()
            url = page.url
            title = page.title()
            # Get headings from navigate-mode ARIA (they appear as "(hN) ..." lines)
            aria = self.fantoma._dom.extract(page, mode="navigate")
            headings = [
                line.strip() for line in aria.split("\n")
                if line.strip().startswith("(h")
            ]
            content = self.fantoma._dom.extract_content(page)[:500]
            parts = [f"URL: {url}", f"Title: {title}"]
            if headings:
                parts.append(f"Headings: {'; '.join(headings[:10])}")
            parts.append(f"Content: {content}")
            return "\n".join(parts)
        except Exception:
            return "Page not loaded"

    def login(self, url: str, **creds) -> AgentResult:
        """Log into a site. Delegates to Fantoma."""
        try:
            self.fantoma.start()
            result = self.fantoma.login(url, **creds)
            return AgentResult(
                success=result.get("success", False),
                data=result,
                steps_taken=result.get("steps", 0),
            )
        except Exception as e:
            return AgentResult(success=False, error=str(e))
        finally:
            self.fantoma.stop()

    def extract(self, url: str, query: str, schema: dict = None):
        """Navigate to a URL and extract data. Delegates to Fantoma."""
        try:
            self.fantoma.start(url)
            return self.fantoma.extract(query, schema)
        except Exception as e:
            log.error("Extract failed: %s", e)
            return [] if schema else ""
        finally:
            self.fantoma.stop()

    def session(self, start_url: str):
        """Create a step-by-step session."""
        return _Session(self, start_url)


class _Session:
    """Step-by-step session using Fantoma tool directly."""

    def __init__(self, agent: Agent, start_url: str):
        self.agent = agent
        self.start_url = start_url

    def __enter__(self):
        self.agent.fantoma.start(self.start_url)
        return self

    def __exit__(self, *args):
        self.agent.fantoma.stop()

    def act(self, instruction: str) -> dict:
        """Execute one instruction. Sends to LLM, executes result via Fantoma."""
        from fantoma.navigator import _parse_actions, NAVIGATOR_SYSTEM
        state = self.agent.fantoma.get_state()
        messages = [
            {"role": "system", "content": NAVIGATOR_SYSTEM.format(
                instruction=instruction, done_when="Task complete")},
            {"role": "user", "content": f"Page ({state['url']}):\n{state['aria_tree']}"},
        ]
        raw = self.agent._llm.chat(messages, max_tokens=200)
        actions = _parse_actions(raw or "")
        result = state
        for action_type, params in actions:
            if action_type == "done":
                break
            method = getattr(self.agent.fantoma, action_type)
            r = method(**params)
            result = r.get("state", result)
        return result

    def extract(self, query: str) -> str:
        """Extract info from current page."""
        return self.agent.fantoma.extract(query)

    def new_tab(self, url: str, name: str = None) -> dict:
        return self.agent.fantoma.new_tab(url)

    def switch_tab(self, tab: int | str) -> dict:
        return self.agent.fantoma.switch_tab(tab)

    def close_tab(self, tab: int | str = None) -> dict:
        return self.agent.fantoma.close_tab(tab)

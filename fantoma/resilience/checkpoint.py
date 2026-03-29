from dataclasses import dataclass


@dataclass
class Checkpoint:
    step: int
    url: str
    dom_snapshot: str
    cookies: list[dict]
    completed_steps: list[int]
    action_history: list  # ActionRecord list
    timestamp: str = ""


class CheckpointManager:
    """Manages state checkpoints for rollback on failure."""

    MAX_CHECKPOINTS = 10

    def __init__(self):
        self._checkpoints: list[Checkpoint] = []

    def save(self, step: int, url: str, dom_snapshot: str, cookies: list,
             completed_steps: list, action_history: list):
        """Save a checkpoint at the current step."""
        from datetime import datetime
        cp = Checkpoint(
            step=step, url=url, dom_snapshot=dom_snapshot,
            cookies=cookies, completed_steps=list(completed_steps),
            action_history=list(action_history),
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )
        self._checkpoints.append(cp)
        # Keep only last N
        if len(self._checkpoints) > self.MAX_CHECKPOINTS:
            self._checkpoints = self._checkpoints[-self.MAX_CHECKPOINTS:]

    def get_latest(self) -> Checkpoint | None:
        """Get the most recent checkpoint."""
        return self._checkpoints[-1] if self._checkpoints else None

    def get_for_step(self, step: int) -> Checkpoint | None:
        """Get the checkpoint closest to (but before) a given step."""
        candidates = [cp for cp in self._checkpoints if cp.step < step]
        return candidates[-1] if candidates else None

    def rollback_to(self, checkpoint: Checkpoint) -> dict:
        """Return the data needed to restore browser state to this checkpoint."""
        return {
            "url": checkpoint.url,
            "cookies": checkpoint.cookies,
            "completed_steps": checkpoint.completed_steps,
            "step": checkpoint.step,
        }

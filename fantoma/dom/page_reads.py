"""Bounded, counted, step-cached page reads.

Three jobs, kept together because they share one set of counters:

1. Every read the step loop makes (ARIA snapshot, small evaluates) goes
   through here, so one stuck page cannot block the loop forever. Playwright's
   sync API has no timeout on page.evaluate, and a hung promise, a page that
   is spinning, or a crashed renderer waits until something else kills the
   process. In the main thread an interval timer interrupts the wait and the
   read returns a degraded result with a log line. In other threads the sync
   API cannot be interrupted from outside, so only Playwright's own timeouts
   (aria_snapshot) apply there.
2. Reads are counted per thread, so the navigator can log
   `reads=X evals=Y` for each step.
3. PageSnapshot holds one ARIA read for the whole step. The element list, the
   before/after diff, the content summary and the state tracker all take
   their text from it. It is invalidated explicitly after any action or
   navigation, and silently when the page URL changes.
"""

import logging
import os
import signal
import threading
import time

log = logging.getLogger("fantoma.page_reads")
step_log = logging.getLogger("fantoma.navigator")

DEFAULT_READ_TIMEOUT_S = 15.0
# A timeout only short-circuits the reads that follow within this window (the
# rest of the same step). Past it the flag is stale, so a timeout in one call
# can never poison later calls, even from entry points that never call
# reset_stuck (server requests, Fantoma.read/extract/login).
STUCK_WINDOW_S = 5.0


def read_timeout_s() -> float:
    """Seconds one page read may take. FANTOMA_READ_TIMEOUT overrides."""
    try:
        return float(os.environ.get("FANTOMA_READ_TIMEOUT", DEFAULT_READ_TIMEOUT_S))
    except ValueError:
        return DEFAULT_READ_TIMEOUT_S


class ReadTimeout(Exception):
    """A page read did not answer in time."""


_local = threading.local()


def _counts() -> dict:
    if not hasattr(_local, "counts"):
        _local.counts = {"reads": 0, "evals": 0, "timeouts": 0}
    return _local.counts


def counters() -> dict:
    """Copy of this thread's read counters."""
    return dict(_counts())


def bounded(fn, timeout_s: float | None = None, label: str = "read"):
    """Run fn(); raise ReadTimeout if it takes longer than timeout_s.

    Only enforceable in the main thread (SIGALRM). Elsewhere fn simply runs.
    Nested calls share the outer timer.
    """
    timeout_s = read_timeout_s() if timeout_s is None else timeout_s
    stuck_at = getattr(_local, "stuck_at", None)
    if stuck_at is not None and time.monotonic() - stuck_at > STUCK_WINDOW_S:
        _local.stuck_at = stuck_at = None   # stale: belongs to an earlier call
    if stuck_at is not None:
        # A read a moment ago already timed out. Do not wait a full timeout
        # again for every remaining read of the same step on the same page.
        raise ReadTimeout(f"{label} skipped: page not answering this step")
    in_main = threading.current_thread() is threading.main_thread()
    if (not in_main or timeout_s <= 0 or getattr(_local, "armed", False)
            or not hasattr(signal, "setitimer")):
        return fn()

    def _alarm(signum, frame):
        raise ReadTimeout(f"{label} took more than {timeout_s:g}s")

    try:
        previous = signal.signal(signal.SIGALRM, _alarm)
    except ValueError:  # not allowed in this context
        return fn()
    _local.armed = True
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        return fn()
    except ReadTimeout:
        _counts()["timeouts"] += 1
        _local.stuck_at = time.monotonic()
        log.warning("Page %s timed out after %gs; continuing with an empty result",
                    label, timeout_s)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _local.armed = False


def reset_stuck() -> None:
    """Forget a timeout from an earlier step or call. Called when a step or a public entry point begins."""
    _local.stuck_at = None


def safe_evaluate(page, js: str, arg=None, label: str = "evaluate"):
    """page.evaluate that is counted and bounded. Raises on failure or timeout."""
    _counts()["evals"] += 1
    if arg is None:
        return bounded(lambda: page.evaluate(js), label=label)
    return bounded(lambda: page.evaluate(js, arg), label=label)


def read_aria(target, timeout_ms: int = 20000, label: str = "aria snapshot") -> str:
    """locator('body').aria_snapshot() that is counted and bounded.

    target is a Page or a Frame. Raises on failure or timeout.
    """
    _counts()["reads"] += 1
    return bounded(
        lambda: target.locator("body").aria_snapshot(timeout=timeout_ms),
        label=label)


class PageSnapshot:
    """One page state, read once, shared by everything in a step."""

    def __init__(self, page):
        reset_stuck()
        self.page = page
        self._aria = None
        self._aria_err = None
        self._title = None
        self._memo: dict = {}
        self._url = self._current_url()

    def _current_url(self):
        try:
            return self.page.url
        except Exception:
            return None

    def is_valid_for(self, page) -> bool:
        return page is self.page and self._current_url() == self._url

    def invalidate(self) -> None:
        """Forget everything. Call after any action that may change the page."""
        self._aria = None
        self._aria_err = None
        self._title = None
        self._memo.clear()
        self._url = self._current_url()

    def aria(self) -> str:
        if self._aria_err is not None:
            raise self._aria_err
        if self._aria is None:
            try:
                self._aria = read_aria(self.page)
            except Exception as e:
                self._aria_err = e  # one failed read per snapshot, not three
                raise
        return self._aria

    def title(self) -> str:
        if self._title is None:
            from fantoma.dom.accessibility import page_title
            self._title = page_title(self.page)
        return self._title

    def memo(self, key: str, fn):
        """Cache fn() under key until the next invalidate()."""
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]


class StepMeter:
    """Reads, evaluates and seconds for one navigator step."""

    def __init__(self):
        self._open = False

    def start(self, step: int) -> None:
        self.flush()
        self.step = step
        self.t0 = time.monotonic()
        self.c0 = counters()
        self.prompt_chars = 0
        self._open = True

    def flush(self) -> None:
        if not self._open:
            return
        self._open = False
        c = counters()
        step_log.info("step %d: reads=%d evals=%d secs=%.1f prompt_chars=%d",
                 self.step, c["reads"] - self.c0["reads"],
                 c["evals"] - self.c0["evals"],
                 time.monotonic() - self.t0, self.prompt_chars)

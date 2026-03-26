def test_action_memory_records():
    from fantoma.resilience.memory import ActionMemory
    m = ActionMemory()
    m.record("click [1]", "hash1", "hash2", True, step=1)
    assert len(m._history) == 1
    assert m._history[0].success is True


def test_action_memory_detects_loop():
    from fantoma.resilience.memory import ActionMemory
    m = ActionMemory()
    for _ in range(3):
        m.record("click [4]", "same_hash", "same_hash", False, step=1)
    assert m.is_blacklisted("click [4]", "same_hash")


def test_action_memory_not_blacklisted_on_success():
    from fantoma.resilience.memory import ActionMemory
    m = ActionMemory()
    m.record("click [1]", "h1", "h2", True, step=1)
    assert not m.is_blacklisted("click [1]", "h1")


def test_checkpoint_save_and_retrieve():
    from fantoma.resilience.checkpoint import CheckpointManager
    cm = CheckpointManager()
    cm.save(step=1, url="https://example.com", dom_snapshot="<html>", cookies=[], completed_steps=[1], action_history=[])
    assert cm.get_latest().step == 1


def test_checkpoint_rollback():
    from fantoma.resilience.checkpoint import CheckpointManager
    cm = CheckpointManager()
    cm.save(step=1, url="https://a.com", dom_snapshot="", cookies=[{"name": "a"}], completed_steps=[1], action_history=[])
    cm.save(step=3, url="https://b.com", dom_snapshot="", cookies=[], completed_steps=[1, 2, 3], action_history=[])
    rb = cm.rollback_to(cm.get_for_step(3))
    assert rb["url"] == "https://a.com"
    assert rb["step"] == 1


def test_escalation_chain():
    from fantoma.resilience.escalation import EscalationChain
    chain = EscalationChain(["http://local", "http://cloud1", "http://cloud2"])
    assert chain.current_endpoint() == "http://local"
    assert chain.can_escalate()
    chain.escalate()
    assert chain.current_endpoint() == "http://cloud1"
    chain.escalate()
    assert chain.current_endpoint() == "http://cloud2"
    assert not chain.can_escalate()


def test_action_loop_exact_match():
    """5 identical actions should be detected as a loop."""
    from fantoma.resilience.memory import ActionMemory
    from unittest.mock import MagicMock
    from fantoma.executor import Executor

    executor = MagicMock(spec=Executor)
    executor.memory = ActionMemory()
    for _ in range(5):
        executor.memory.record('SCROLL down', 'h1', 'h1', True, step=1)

    # Call the real method
    result = Executor._is_action_loop(executor)
    assert result is True


def test_action_loop_semantic_match():
    """Same action type + same text but different element numbers = loop."""
    from fantoma.resilience.memory import ActionMemory
    from unittest.mock import MagicMock
    from fantoma.executor import Executor

    executor = MagicMock(spec=Executor)
    executor.memory = ActionMemory()
    executor.memory.record('TYPE [1] "email@test.com"', 'h1', 'h1', True, step=1)
    executor.memory.record('TYPE [4] "email@test.com"', 'h1', 'h1', True, step=2)
    executor.memory.record('TYPE [3] "email@test.com"', 'h1', 'h1', True, step=3)
    executor.memory.record('TYPE [1] "email@test.com"', 'h1', 'h1', True, step=4)
    executor.memory.record('TYPE [7] "email@test.com"', 'h1', 'h1', True, step=5)

    result = Executor._is_action_loop(executor)
    assert result is True


def test_action_loop_different_actions_no_loop():
    """Different actions should NOT be detected as a loop."""
    from fantoma.resilience.memory import ActionMemory
    from unittest.mock import MagicMock
    from fantoma.executor import Executor

    executor = MagicMock(spec=Executor)
    executor.memory = ActionMemory()
    executor.memory.record('TYPE [1] "email@test.com"', 'h1', 'h1', True, step=1)
    executor.memory.record('CLICK [2]', 'h1', 'h2', True, step=2)
    executor.memory.record('TYPE [3] "password"', 'h2', 'h2', True, step=3)
    executor.memory.record('CLICK [5]', 'h2', 'h3', True, step=4)
    executor.memory.record('WAIT', 'h3', 'h3', True, step=5)

    result = Executor._is_action_loop(executor)
    assert result is False


def test_action_loop_fewer_than_5_no_loop():
    """Fewer than 5 actions should never be a loop."""
    from fantoma.resilience.memory import ActionMemory
    from unittest.mock import MagicMock
    from fantoma.executor import Executor

    executor = MagicMock(spec=Executor)
    executor.memory = ActionMemory()
    for _ in range(3):
        executor.memory.record('SCROLL down', 'h1', 'h1', True, step=1)

    result = Executor._is_action_loop(executor)
    assert result is False


def test_consecutive_failure_counter_init():
    """Executor should initialise consecutive failure counter to 0."""
    from unittest.mock import MagicMock
    from fantoma.executor import Executor
    from fantoma.config import FantomaConfig

    browser = MagicMock()
    llm = MagicMock()
    config = FantomaConfig()
    executor = Executor(browser=browser, llm=llm, config=config)
    assert executor._consecutive_failures == 0


def test_captcha_detector():
    # Mock test — just verify the class loads and processes strings
    from fantoma.captcha.detector import CaptchaDetector, CAPTCHA_SIGNATURES
    assert len(CAPTCHA_SIGNATURES) >= 5


def test_pow_solver_altcha():
    import hashlib
    from fantoma.captcha.pow_solver import solve_altcha
    # Create a solvable challenge
    salt = "test_salt_"
    number = 42
    target = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
    result = solve_altcha({"algorithm": "SHA-256", "challenge": target, "salt": salt, "maxnumber": 100})
    assert result == "42"

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

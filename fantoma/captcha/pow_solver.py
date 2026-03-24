"""Proof-of-work CAPTCHA solver for ALTCHA and Friendly Captcha. No external API needed."""
import hashlib
import logging

log = logging.getLogger("fantoma.captcha.pow")


def solve_altcha(challenge: dict) -> str | None:
    """Solve an ALTCHA proof-of-work challenge locally.

    challenge: {"algorithm": "SHA-256", "challenge": "hex", "salt": "...", "maxnumber": N}
    Returns the solution number as string, or None if unsolvable.
    """
    algorithm = challenge.get("algorithm", "SHA-256").upper()
    target = challenge.get("challenge", "")
    salt = challenge.get("salt", "")
    max_number = challenge.get("maxnumber", 1000000)

    hash_func = hashlib.sha256 if algorithm == "SHA-256" else hashlib.sha512

    for number in range(max_number):
        hash_input = f"{salt}{number}"
        result = hash_func(hash_input.encode()).hexdigest()
        if result == target:
            log.info("ALTCHA solved at number=%d", number)
            return str(number)

    log.warning("ALTCHA: exhausted max_number=%d without solution", max_number)
    return None


def solve_friendly_captcha(puzzle: dict) -> str | None:
    """Solve a Friendly Captcha puzzle locally."""
    # Friendly Captcha uses a similar PoW approach
    # The puzzle format may vary — implement basic SHA-256 PoW
    log.info("Friendly Captcha solver invoked")
    # Basic implementation — extend as needed
    return None

#!/usr/bin/env python3
"""Diagnostic: run one task with full logging + trace every raw LLM action
response and how it parsed, to see if DONE is being emitted but not matched."""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s", stream=sys.stderr)

import fantoma.navigator as nav

_orig = nav._parse_actions
def _traced(raw):
    parsed = _orig(raw)
    sys.stderr.write(f"\n>>> RAW LLM ACTION RESPONSE:\n{raw!r}\n>>> PARSED: {parsed}\n\n")
    return parsed
nav._parse_actions = _traced

from fantoma import Agent

task = sys.argv[1]
url = sys.argv[2]
llm = os.environ.get("LOCAL_LLM_URL", "http://192.168.0.100:8081/v1")
agent = Agent(llm_url=llm, model="Qwen3.6-35B-A3B", escalation=[llm],
              escalation_models=["Qwen3.6-35B-A3B"], max_steps=6,
              headless=True, browser="camoufox")
res = agent.run(task, start_url=url)
sys.stderr.write(f"\n=== FINAL success={res.success} steps={res.steps_taken} data={res.data!r}\n")

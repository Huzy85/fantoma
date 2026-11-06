#!/usr/bin/env python3
"""Multi-model flow benchmark, built to survive a night on its own.

Runs every flow in tools/live_flow_test.py against several Fantoma backends
(one per model), N times each, and grades each run off the live browser.

Resumable: every finished (model, flow, run) is appended to results.jsonl
the moment it completes, and a restart skips what is already there. A dead
backend is waited for (up to --backend-wait seconds) rather than failed, so
a supervisor can restart it underneath. Nothing here raises out of a flow:
a crash is a recorded failure, not a stopped benchmark.

    python3 tools/fantoma_bench.py --out DIR --runs 3 \\
        --backend hermes=http://127.0.0.1:7871 \\
        --backend gemma=http://127.0.0.1:7872 \\
        --backend deepseek=http://127.0.0.1:7873

Writes in DIR: results.jsonl, status.json (progress, for a watchdog),
report.md (final table) and `done` when finished.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_flows():
    spec = importlib.util.spec_from_file_location("lft", os.path.join(HERE, "live_flow_test.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FLOWS, mod._verify, mod.task_text


def _http(method, url, body=None, timeout=60.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _healthy(base):
    try:
        _http("GET", f"{base}/health", timeout=10)
        return True
    except Exception:
        return False


class Bench:
    def __init__(self, out, backends, runs, flow_timeout, backend_wait, only, restart_cmd=""):
        self.out = out
        self.backends = backends          # {name: base_url}
        self.runs = runs
        self.flow_timeout = flow_timeout
        self.backend_wait = backend_wait
        self.restart_cmd = restart_cmd    # shell command with {name}, run when a backend stays silent
        self.flows, self.verify, self.task_text = _load_flows()
        if only:
            self.flows = [f for f in self.flows if f["name"] in only]
        os.makedirs(out, exist_ok=True)
        self.results_path = os.path.join(out, "results.jsonl")
        self.lock = threading.RLock()   # _record holds it while writing status
        self.results = self._load_results()
        self.current = {}
        self.events = []

    # ── persistence ──────────────────────────────────────────────
    def _load_results(self):
        res = {}
        if os.path.exists(self.results_path):
            with open(self.results_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        res[(r["model"], r["flow"], r["run"])] = r
                    except Exception:
                        continue
        return res

    def _record(self, r):
        with self.lock:
            self.results[(r["model"], r["flow"], r["run"])] = r
            with open(self.results_path, "a") as fh:
                fh.write(json.dumps(r) + "\n")
            self._write_status()

    def _event(self, text):
        line = f"{dt.datetime.now():%H:%M:%S} {text}"
        print(line, flush=True)
        with self.lock:
            self.events.append(line)
            self.events = self.events[-200:]

    def _write_status(self):
      with self.lock:   # two workers writing the same tmp file at once lost a thread
        total = len(self.flows) * self.runs
        per = {}
        for name in self.backends:
            rs = [r for r in self.results.values() if r["model"] == name]
            per[name] = {
                "done": len(rs), "total": total,
                "flows_passed": sum(1 for r in rs if r["passed"] == r["total"]),
                "current": self.current.get(name),
            }
        tmp = os.path.join(self.out, "status.json.tmp")
        with open(tmp, "w") as fh:
            json.dump({"updated": dt.datetime.now().isoformat(timespec="seconds"),
                       "models": per, "events": self.events[-20:]}, fh, indent=1)
        os.replace(tmp, os.path.join(self.out, "status.json"))

    # ── one flow ─────────────────────────────────────────────────
    def _wait_backend(self, name, base):
        # The server answers one request at a time, so /health blocks for the
        # length of a flow. Silence for backend_wait seconds (longer than any
        # flow can take) means it is wedged: restart it once, then wait again.
        t0 = time.time()
        warned = False
        restarted = False
        while not _healthy(base):
            if not warned:
                self._event(f"{name}: backend {base} not answering, waiting")
                warned = True
            if time.time() - t0 > self.backend_wait:
                if restarted or not self.restart_cmd:
                    return False
                cmd = self.restart_cmd.format(name=name)
                self._event(f"{name}: silent for {self.backend_wait}s, running: {cmd}")
                os.system(cmd)
                restarted = True
                t0 = time.time()
            time.sleep(10)
        if warned:
            self._event(f"{name}: backend back after {int(time.time()-t0)}s"
                        + (" (after restart)" if restarted else ""))
        return True

    def _run_flow(self, name, base, flow, run):
        t0 = time.time()
        rec = {"model": name, "flow": flow["name"], "run": run,
               "total": len(flow["checkpoints"]), "passed": 0, "steps": 0,
               "agent_ok": False, "secs": 0.0, "detail": "", "answer": "",
               "at": dt.datetime.now().isoformat(timespec="seconds")}
        if not self._wait_backend(name, base):
            rec["detail"] = f"backend down for {self.backend_wait}s"
            rec["secs"] = round(time.time() - t0, 1)
            return rec
        try:
            try:
                _http("POST", f"{base}/stop", {}, timeout=60)
            except Exception:
                pass
            body = _http("POST", f"{base}/run",
                         {"task": self.task_text(flow), "url": flow["url"],
                          "timeout": self.flow_timeout, "keep_session": True},
                         timeout=self.flow_timeout + 90)
            # Two views of the same page: navigate (controls, state flags,
            # headings) and content (text). The 20-element cap means a
            # scrolled page can hide a control from one view alone.
            trees = []
            for mode in ("navigate", "content"):
                try:
                    trees.append(_http("GET", f"{base}/state?mode={mode}", timeout=60).get("aria_tree") or "")
                except Exception:
                    pass
            state = {"aria_tree": "\n".join(trees)}
            rec["tree_head"] = trees[0][:400] if trees else ""
            rec["tree"] = state["aria_tree"]   # full, so checkpoints can be re-scored later
            hits, verr = self.verify(body, state, flow["checkpoints"])
            try:
                _http("POST", f"{base}/stop", {}, timeout=60)
            except Exception:
                pass
            rec["passed"] = sum(1 for _, ok in hits if ok)
            rec["agent_ok"] = bool(body.get("success"))
            rec["steps"] = body.get("steps_taken") or 0
            failed_at = next((label for label, ok in hits if not ok), None)
            rec["detail"] = verr or (f"stopped at: {failed_at}" if failed_at else "all checkpoints met")
            rec["answer"] = (body.get("data") or "")[:120].replace("\n", " ")
        except Exception as e:  # noqa: BLE001 - a crash is a result, not an exit
            rec["detail"] = f"{type(e).__name__}: {str(e)[:100]}"
        rec["secs"] = round(time.time() - t0, 1)
        return rec

    def _model_worker(self, name, base):
        for run in range(1, self.runs + 1):
            for flow in self.flows:
                key = (name, flow["name"], run)
                if key in self.results:
                    continue
                self.current[name] = f"run {run} {flow['name']}"
                try:
                    self._write_status()
                    rec = self._run_flow(name, base, flow, run)
                except Exception as e:  # noqa: BLE001
                    rec = {"model": name, "flow": flow["name"], "run": run,
                           "total": len(flow["checkpoints"]), "passed": 0, "steps": 0,
                           "agent_ok": False, "secs": 0.0, "answer": "",
                           "detail": f"worker: {type(e).__name__}: {str(e)[:80]}",
                           "at": dt.datetime.now().isoformat(timespec="seconds")}
                try:
                    self._record(rec)
                    mark = "PASS" if rec["passed"] == rec["total"] else "FAIL"
                    self._event(f"{name} r{run} {mark} {flow['name']} {rec['passed']}/{rec['total']} "
                                f"{rec['secs']}s {rec['steps']} steps  {rec['detail']}")
                except Exception as e:  # noqa: BLE001 - never lose the worker
                    print(f"{name}: record failed {type(e).__name__}: {e}", flush=True)
        self.current[name] = "finished"
        self._write_status()

    # ── report ───────────────────────────────────────────────────
    def report(self):
        lines = [f"# Fantoma flow benchmark {dt.date.today()}", "",
                 f"{len(self.flows)} flows x {self.runs} runs per model. A flow passes only if "
                 "every checkpoint is read off the live browser after the run.", "",
                 "| model | " + " | ".join(f"run {r}" for r in range(1, self.runs + 1)) +
                 " | flows passed | checkpoints | mean secs | mean steps |",
                 "|---|" + "---|" * (self.runs + 4)]
        summary = {}
        for name in self.backends:
            rs = [r for r in self.results.values() if r["model"] == name]
            per_run = []
            for run in range(1, self.runs + 1):
                rr = [r for r in rs if r["run"] == run]
                per_run.append(f"{sum(1 for r in rr if r['passed'] == r['total'])}/{len(self.flows)}")
            fp = sum(1 for r in rs if r["passed"] == r["total"])
            cp = sum(r["passed"] for r in rs)
            ct = sum(r["total"] for r in rs)
            secs = statistics.mean([r["secs"] for r in rs]) if rs else 0
            steps = statistics.mean([r["steps"] for r in rs]) if rs else 0
            summary[name] = f"{fp}/{len(rs)} flows, {cp}/{ct} checkpoints"
            lines.append(f"| {name} | " + " | ".join(per_run) +
                         f" | {fp}/{len(rs)} | {cp}/{ct} | {secs:.0f} | {steps:.1f} |")
        lines += ["", "## Per flow (passes out of runs)", "",
                  "| flow | " + " | ".join(self.backends) + " |",
                  "|---|" + "---|" * len(self.backends)]
        for flow in self.flows:
            cells = []
            for name in self.backends:
                rs = [r for r in self.results.values()
                      if r["model"] == name and r["flow"] == flow["name"]]
                cells.append(f"{sum(1 for r in rs if r['passed'] == r['total'])}/{len(rs)}")
            lines.append(f"| {flow['name']} | " + " | ".join(cells) + " |")
        lines += ["", "## Failure reasons", ""]
        for name in self.backends:
            reasons = {}
            for r in self.results.values():
                if r["model"] == name and r["passed"] != r["total"]:
                    k = r["detail"][:60]
                    reasons[k] = reasons.get(k, 0) + 1
            if reasons:
                lines.append(f"**{name}**")
                for k, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
                    lines.append(f"- {n}x {k}")
                lines.append("")
        text = "\n".join(lines) + "\n"
        with open(os.path.join(self.out, "report.md"), "w") as fh:
            fh.write(text)
        return text, summary

    def run(self):
        self._event(f"start: {len(self.flows)} flows x {self.runs} runs x {list(self.backends)}; "
                    f"{len(self.results)} results already on disk")
        threads = [threading.Thread(target=self._model_worker, args=(n, b), daemon=True, name=n)
                   for n, b in self.backends.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        text, summary = self.report()
        with open(os.path.join(self.out, "summary.txt"), "w") as fh:
            fh.write("\n".join(f"{k}: {v}" for k, v in summary.items()) + "\n")
        with open(os.path.join(self.out, "done"), "w") as fh:
            fh.write(dt.datetime.now().isoformat(timespec="seconds") + "\n")
        self._event("done")
        print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", action="append", required=True, help="name=http://host:port")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--flow-timeout", type=int, default=300, help="agent budget per flow (s)")
    ap.add_argument("--backend-wait", type=int, default=900,
                    help="how long to wait for a dead backend before failing the flow (s)")
    ap.add_argument("--only", nargs="*", help="flow names to run (default all)")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild report.md and summary.txt from results.jsonl, run nothing")
    ap.add_argument("--restart-cmd", default="",
                    help="shell command to revive a silent backend, {name} is substituted, "
                         "e.g. 'systemctl --user restart fantoma-bench-{name}.service'")
    a = ap.parse_args()
    backends = dict(b.split("=", 1) for b in a.backend)
    b = Bench(a.out, backends, a.runs, a.flow_timeout, a.backend_wait, a.only, a.restart_cmd)
    if a.report_only:
        text, summary = b.report()
        with open(os.path.join(a.out, "summary.txt"), "w") as fh:
            fh.write("\n".join(f"{k}: {v}" for k, v in summary.items()) + "\n")
        print(text)
        return
    b.run()


if __name__ == "__main__":
    main()

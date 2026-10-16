"""Generated code runs only in keyless, network-blocked, ephemeral child sandboxes."""
import json
import os
import time

from daytona import CreateSandboxFromSnapshotParams, Daytona
from pydantic import BaseModel

from config import FETCH_MODEL
from steps.llm import parse
from tracing import span

_daytona = None
_warm = None
REUSE = os.getenv("COMPUTE_REUSE_CHILD") == "1"


def daytona():
    global _daytona
    if _daytona is None:
        _daytona = Daytona()
    return _daytona


def new_child():
    return daytona().create(CreateSandboxFromSnapshotParams(
        language="python",
        ephemeral=True,          # deleted when it stops
        auto_stop_interval=5,
        network_block_all=True,  # generated code can't reach anything, keys or not
        env_vars={},
        labels={"role": "child", "app": "incentive-agent"},
    ), timeout=120)


class Program(BaseModel):
    code: str


SYSTEM = """You write a short, self-contained Python 3 program (standard library only, no network, no file access) that decides eligibility and computes a credit amount.
Use only the cited facts and subject inputs you are given. Inline every input as a literal at the top of the program.
The program must print exactly one line of JSON and nothing else:
{"eligible": bool, "amount": number, "inputs": {...the literals you used...}, "formula": "human-readable formula with the numbers substituted", "reasons": ["one entry per criterion: met / not met and why"]}
If any criterion is not met, eligible is false and amount is 0."""


def generate(program, subject, findings):
    cited = [{k: f[k] for k in ("criterion", "value", "quote")} for f in findings]
    user = (f"Program: {program['name']}\nCited facts:\n{json.dumps(cited, indent=2)}\n\n"
            f"Subject inputs:\n{json.dumps(subject, indent=2)}")
    with span("compute.generate", kind="CHAIN", program_id=program["program_id"], model=FETCH_MODEL):
        return parse(FETCH_MODEL, SYSTEM, user, Program, effort="medium").code


def run(code, claim_hint=None):
    """Returns (parsed dict | None, exit_code, raw_output, child_id)."""
    global _warm
    t0 = time.time()
    child = _warm if (REUSE and _warm) else new_child()
    with span("compute.child_run", kind="TOOL", sandbox_id=child.id, claim_hint=claim_hint) as s:
        try:
            r = child.process.code_run(code, timeout=60)
        finally:
            if REUSE:
                _warm = child
            else:
                child.delete()
        s.set_attribute("exit_code", r.exit_code)
        s.set_attribute("duration_s", round(time.time() - t0, 2))
    if r.exit_code != 0:
        return None, r.exit_code, r.result, child.id
    lines = [l for l in r.result.strip().splitlines() if l.strip()]
    try:
        out = json.loads(lines[-1])
        assert isinstance(out.get("eligible"), bool) and isinstance(out.get("amount"), (int, float))
        return out, 0, r.result, child.id
    except (IndexError, ValueError, AssertionError):
        return None, 0, r.result, child.id

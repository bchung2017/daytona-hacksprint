"""Deterministic evals against stored claims. No LLM judges.

    python -m evals.run            voice cases only (no LLM/Anchor spend; injects a claim)
    python -m evals.run --pipeline also runs the full pipeline and checks pipeline cases
"""
import tracing  # noqa: F401

import json
import sys
from pathlib import Path

from steps import store, voice
from tracing import span

POSITIVE = {"unverified", "sent", "confirmed", "needs-info", "ready", "approved"}
CASES = json.loads((Path(__file__).parent / "cases.json").read_text())


def eligibility_requires_full_citations(claims):
    bad = [c["id"] for c in claims if (c.get("eligible") or c["state"] in POSITIVE)
           and (not c.get("citations") or not all(x.get("cited") for x in c["citations"]))]
    return not bad, bad


def ready_requires_two_matching_values(claims):
    bad = [c["id"] for c in claims if c["state"] in ("ready", "approved")
           and not (c.get("cpa_confirmed") and c.get("cpa_value") is not None and c.get("agent_value") is not None
                    and voice.matches(c["agent_value"], c["cpa_value"]))]
    return not bad, bad


def no_live_claim_cites_superseded_source(claims):
    bad = []
    for c in claims:
        if c["state"] in POSITIVE and c.get("superseded_by") is None:
            if any(x.get("source_id") and not store.source_is_live(x["source_id"]) for x in c.get("citations") or []):
                bad.append(c["id"])
    return not bad, bad


EVALS = {f.__name__: f for f in (eligibility_requires_full_citations, ready_requires_two_matching_values,
                                 no_live_claim_cites_superseded_source)}


def voice_case(case):
    claim = store.create_claim(run_id="eval", company_id="hudson-forge", program_id="nys-hire-a-veteran",
                               state="unverified", eligible=True, agent_value=12600.0,
                               citations=[{"criterion": "eval", "quote": "eval", "cited": True, "source_id": None}],
                               agent_inputs={"subject_id": "vet-ramos"})
    sent = store.send_for_verification(claim["id"])
    call = {"claim_id": claim["id"], "code": sent["cpa_code"], **case["setup"]["tool_call"]}
    rejected = []
    status, _ = voice.handle_tool_call(call, on_reject=lambda cid, st: rejected.append(cid))
    after = store.get_claim(claim["id"])
    ok = after["state"] in case["expect"]["states"]
    if "http_status" in case["expect"]:
        ok = ok and status == case["expect"]["http_status"]
    if case["expect"].get("superseding_claim"):
        ok = ok and rejected == [claim["id"]]
    return ok, {"state": after["state"], "http": status}


def pipeline_case(case, claims):
    e = case["expect"]
    hits = [c for c in claims if c["program_id"] == e["program_id"]
            and ("subject_id" not in e or (c.get("agent_inputs") or {}).get("subject_id") == e["subject_id"]
                 or e["subject_id"] in json.dumps(c.get("agent_inputs") or {}))]
    if not hits:
        return bool(e.get("or_skipped_by_plan")), {"claims": 0}
    ok = all(c["state"] in e["states"] for c in hits)
    if "agent_value" in e:
        ok = ok and all(abs((c.get("agent_value") or 0) - e["agent_value"]) < 0.01 for c in hits)
    return ok, {"states": [c["state"] for c in hits], "values": [c.get("agent_value") for c in hits]}


def main():
    rows = []
    run_id = None
    if "--pipeline" in sys.argv:
        import agent
        run_id = agent.run()
    claims = store.q("SELECT * FROM claim WHERE run_id = $r", r=run_id) if run_id else []
    for case in CASES:
        with span(f"eval.{case['case']}", kind="EVALUATOR", case=case["case"]) as s:
            if "tool_call" in case["setup"]:
                ok, detail = voice_case(case)
            elif run_id and not case["setup"]:
                ok, detail = pipeline_case(case, claims)
            else:
                continue
            s.set_attribute("passed", ok)
            rows.append((case["case"], ok, detail))
    all_claims = store.list_claims()
    for name, f in EVALS.items():
        ok, bad = f(all_claims)
        rows.append((name, ok, {"violations": bad}))
    w = max(len(r[0]) for r in rows)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{w}}  {json.dumps(detail)}")
    sys.exit(0 if all(r[1] for r in rows) else 1)


if __name__ == "__main__":
    main()

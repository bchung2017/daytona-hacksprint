import tracing  # noqa: F401  (first: instruments anthropic before import)

import json
import uuid
from pathlib import Path

from steps import compute, fetch, memory, plan, store
from steps.llm import LLMError
from tracing import span

ROOT = Path(__file__).parent


def load_seed():
    return (json.loads((ROOT / "seed/company.json").read_text()),
            json.loads((ROOT / "seed/programs.json").read_text()))


def subjects_of(company):
    return {company["company_id"]: {k: v for k, v in company.items() if k != "subjects"},
            **{s["subject_id"]: s for s in company.get("subjects", [])}}


def run(company=None, programs=None):
    if company is None:
        company, programs = load_seed()
    run_id = uuid.uuid4().hex[:8]
    by_id = {p["program_id"]: p for p in programs}
    subjects = subjects_of(company)
    with span("run", kind="AGENT", run_id=run_id, company_id=company["company_id"]):
        store.log(run_id, "intake", f"company {company['name']}")
        try:
            p = plan.plan(company, programs)
        except LLMError as e:
            store.log(run_id, "plan", f"aborted: {e}")
            return run_id
        store.log(run_id, "plan", f"{len(p.programs)} programs, skipped {len(p.skipped)}", plan=p.model_dump())

        items = [i for i in p.programs if i.program_id in by_id]
        for i in p.programs:
            if i.program_id not in by_id:
                store.log(run_id, "plan", f"unknown program_id {i.program_id}; ignored")

        fetched = {}
        for item in items:
            if item.program_id in fetched:
                continue
            program = by_id[item.program_id]
            criteria = sorted({c for i in items if i.program_id == item.program_id for c in i.criteria_to_check})
            fetched[item.program_id] = fetch.fetch_and_cite(program, criteria)
            findings, status, _ = fetched[item.program_id]
            store.log(run_id, "fetch", f"{program['name']}: {sum(f['cited'] for f in findings)}/{len(findings)} cited, status {status['status']}")

        # After every fetch, so ordering in the plan can't leave an old version's sources live.
        for pid, (_, _, sources) in fetched.items():
            old = by_id[pid].get("supersedes")
            if old and sources:
                store.q("UPDATE source SET superseded_by = $n WHERE program_id = $o AND superseded_by = NONE",
                        n=store.rid(sources[0]["id"]), o=old)

        for item in items:
            findings, status, _ = fetched[item.program_id]
            if by_id[item.program_id].get("superseded_by"):
                status = {**status, "status": "superseded"} if status["status"] != "expired" else status
            for sid in item.subject_ids:
                evaluate(run_id, company, by_id[item.program_id], subjects.get(sid, {"subject_id": sid}), findings, status)
        store.log(run_id, "done", "run complete")
    return run_id


def evaluate(run_id, company, program, subject, findings, status, supersedes=None, correction=None):
    pid = program["program_id"]
    cites = [{k: f[k] for k in ("criterion", "value", "quote", "source_url", "fetched_at", "source_id", "cited")} for f in findings]
    base = dict(run_id=run_id, company_id=company["company_id"], program_id=pid, citations=cites, supersedes=supersedes)
    sid = subject.get("subject_id", company["company_id"])
    with span("evaluate", kind="CHAIN", program_id=pid, subject_id=sid):
        if status["status"] in ("expired", "superseded"):
            why = (f"superseded by {program['superseded_by']}" if program.get("superseded_by")
                   else f"program {status['status']}: {status['quote']}")
            c = store.create_claim(state="not_eligible", eligible=False, agent_value=0, reason=why,
                                   agent_inputs={"subject_id": sid}, **base)
            store.log(run_id, "compute", f"{pid}/{sid}: not eligible ({status['status']})", claim_id=c["id"])
            return c
        uncited = [f["criterion"] for f in findings if not f["cited"]]
        if uncited or not findings:
            c = store.create_claim(state="uncited", reason=f"no supporting quote for: {uncited or 'any criterion'}",
                                   agent_inputs={"subject_id": sid}, **base)
            store.log(run_id, "fetch", f"{pid}/{sid}: uncited, stopped before compute", claim_id=c["id"])
            return c
        subj = {**subject, "cpa_correction": correction} if correction else subject
        try:
            code = compute.generate(program, subj, findings)
        except LLMError as e:
            return store.create_claim(state="compute_error", reason=str(e), agent_inputs={"subject_id": sid}, **base)
        out, exit_code, raw, child_id = compute.run(code, claim_hint=f"{pid}/{sid}")
        if out is None:
            c = store.create_claim(state="compute_error", reason=f"exit {exit_code}: {raw[-500:]}",
                                   agent_inputs={"subject_id": sid, "code": code, "child_id": child_id}, **base)
        else:
            c = store.create_claim(state="unverified" if out["eligible"] else "not_eligible",
                                   eligible=out["eligible"], agent_value=float(out["amount"]),
                                   agent_inputs={**out.get("inputs", {}), "subject_id": sid, "child_id": child_id, "code": code},
                                   formula=out.get("formula"), reason="; ".join(out.get("reasons", [])), **base)
        store.log(run_id, "compute", f"{pid}/{sid}: {c['state']} {c.get('agent_value')}", claim_id=c["id"])
        memory.remember_claim(c, program["name"])
        return c


def recompute_after_reject(claim_id, cpa_statement):
    """A rejected claim gets a superseding claim with the CPA's stated correction as an extra input."""
    old = store.get_claim(claim_id)
    company, programs = load_seed()
    program = next(p for p in programs if p["program_id"] == old["program_id"])
    subject = subjects_of(company).get(old["agent_inputs"].get("subject_id"), {})
    findings = [c for c in old["citations"] if store.source_is_live(c["source_id"])]
    if len(findings) != len(old["citations"]):
        findings, status, _ = fetch.fetch_and_cite(program, [c["criterion"] for c in old["citations"]])
    else:
        status = {"status": "current"}
    return evaluate(old["run_id"], company, program, subject, findings, status,
                    supersedes=claim_id, correction=cpa_statement)


if __name__ == "__main__":
    print(run())

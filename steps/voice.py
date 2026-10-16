"""CPA verification. The safety checks live here, not in the voice agent's prompt.

1. Identity: the spoken one-time code must map to the claim on screen.
2. Read-back: confirmed must be true and the CPA's own words must not hedge.
3. Least authority: this module only writes CPA fields on one `sent` claim (store.write_cpa).
4. Mismatch: outside tolerance -> rejected + superseding claim. Never confirmed.
The voice agent is never given agent_value, so it cannot anchor the CPA on it."""
import hmac
import os
import re

from pydantic import BaseModel

from config import MATCH_TOLERANCE, PLAN_MODEL
from steps import store
from steps.llm import parse
from tracing import span

HEDGES = re.compile(r"\b(about|around|roughly|approximately|approx|i think|i believe|maybe|probably|ish|"
                    r"give or take|ballpark|something like|not sure|guess)\b", re.I)


def tool_secret_ok(header_value):
    expected = os.getenv("CPA_TOOL_SECRET", "")
    return bool(expected) and hmac.compare_digest(header_value or "", expected)


def matches(agent_value, cpa_value):
    return abs(agent_value - cpa_value) <= MATCH_TOLERANCE * max(abs(agent_value), 1)


def handle_tool_call(body, on_reject=None):
    """body: {claim_id, code, value, confirmed, cpa_statement}. Returns (http_status, response_for_agent)."""
    claim_id = str(body.get("claim_id") or "")
    code = str(body.get("code") or "").strip().replace(" ", "")
    with span("voice.tool_call", kind="TOOL", claim_id=claim_id) as s:
        claim = store.claim_by_code(code) if code else None
        if not claim or (claim_id and claim["id"] != claim_id):
            s.set_attribute("outcome", "bad_code")
            return 403, {"result": "That code does not match the claim on screen. Nothing was recorded. Ask the CPA to read the code again."}

        try:
            value = float(str(body.get("value")).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            store.write_cpa(claim["id"], None, False, "needs-info")
            s.set_attribute("outcome", "unparseable_value")
            return 200, {"result": "Recorded as needs more information: the figure was not a number."}

        confirmed = body.get("confirmed") is True or str(body.get("confirmed")).lower() == "true"
        statement = str(body.get("cpa_statement") or "")
        if not confirmed or HEDGES.search(statement):
            store.write_cpa(claim["id"], value, False, "needs-info")
            s.set_attribute("outcome", "needs_info")
            return 200, {"result": "Recorded as needs more information. An exact, confirmed figure is required."}

        if not matches(claim["agent_value"], value):
            store.write_cpa(claim["id"], value, True, "rejected")
            s.set_attribute("outcome", "rejected")
            if on_reject:
                on_reject(claim["id"], statement)
            return 200, {"result": "Recorded. The figures differ, so the claim was flagged for recomputation. Ask the CPA what input they believe differs."}

        store.write_cpa(claim["id"], value, True, "confirmed")  # schema event promotes to ready
        s.set_attribute("outcome", "confirmed")
        return 200, {"result": "Recorded and confirmed. Thank the CPA and end the call."}


class TranscriptParse(BaseModel):
    cpa_value: float | None
    explicitly_confirmed: bool
    hedged: bool


PARSE_SYSTEM = """Read a call transcript between a verification agent and a CPA.
Report the dollar figure the CPA themself stated for the credit (not one the agent said), whether the CPA explicitly confirmed it on read-back, and whether the CPA hedged ("about", "roughly", "I think", and similar)."""


def cross_check(claim_id, transcript_text, conversation_id):
    """Post-call: an independent parse of the transcript. Disagreement with the tool call drops to needs-info."""
    claim = store.get_claim(claim_id)
    if not claim or claim["state"] in ("approved",):
        return None
    with span("voice.cross_check", kind="CHAIN", claim_id=claim_id, conversation_id=conversation_id) as s:
        p = parse(PLAN_MODEL, PARSE_SYSTEM, transcript_text, TranscriptParse, effort="low")
        agree = (p.cpa_value is not None and claim.get("cpa_value") is not None
                 and abs(p.cpa_value - claim["cpa_value"]) < 0.005 and p.explicitly_confirmed == bool(claim.get("cpa_confirmed"))
                 and not p.hedged)
        s.set_attribute("agree", agree)
        fields = {"cpa_transcript_ref": conversation_id, "transcript_parse": p.model_dump()}
        if not agree and claim["state"] in ("confirmed", "ready"):
            return store.set_state(claim_id, "needs-info", **fields)
        return store.q("UPDATE ONLY $c MERGE $f RETURN AFTER", c=store.rid(claim_id), f=fields)

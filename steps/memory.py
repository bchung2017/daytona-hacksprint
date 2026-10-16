"""Optional mirror into a SurrealDB Agent Memory context (REST, /api/v1/{ctx}/facts).
Facts are written with observed_at = fetch time so asOf recall reflects what the agent knew when.
Best-effort and off the request path: the claims table is the source of truth for the gate."""
import os
import threading

import httpx

from tracing import span

URL, CTX, KEY = os.getenv("MEMORY_URL", "").rstrip("/"), os.getenv("MEMORY_CONTEXT"), os.getenv("MEMORY_API_KEY")
SCOPE = os.getenv("MEMORY_SCOPE", "org/incentive-agent")
ENABLED = bool(URL and CTX and KEY)


def _post(path, body, idem=None):
    h = {"Authorization": f"Bearer {KEY}"}
    if idem:
        h["Idempotency-Key"] = idem
    with span(f"memory.{path}", kind="TOOL") as s:
        r = httpx.post(f"{URL}/api/v1/{CTX}/{path}", json=body, headers=h, timeout=60)
        s.set_attribute("http.status", r.status_code)
        r.raise_for_status()
        return r.json()


def remember(text, observed_at=None, idem=None):
    if not ENABLED:
        return
    body = {"text": text, "infer": "full", "scopes": [[SCOPE]]}
    if observed_at:
        body["observed_at"] = observed_at
    threading.Thread(target=lambda: _safe(_post, "facts", body, idem), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass  # recorded on the span; the mirror never blocks or fails a claim


def remember_claim(claim, program_name):
    for c in claim.get("citations") or []:
        if c.get("cited"):
            remember(f"{program_name} source {c['source_url']} states, for '{c['criterion']}': \"{c['quote']}\"",
                     observed_at=c.get("fetched_at"), idem=f"{claim['id']}:{c['criterion']}")
    subject = (claim.get("agent_inputs") or {}).get("subject_id") or claim["company_id"]
    remember(f"Agent determined {subject} is {'eligible' if claim.get('eligible') else 'not eligible'} for "
             f"{program_name}; computed value {claim.get('agent_value')}; claim {claim['id']} state {claim['state']}.",
             idem=f"{claim['id']}:outcome")

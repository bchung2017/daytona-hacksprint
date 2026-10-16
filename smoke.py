"""Setup smoke tests. Run inside the parent: python deploy.py --smoke. One line per check."""
import tracing  # noqa: F401

import os
import threading
import time
import traceback

import httpx

results = []


def check(name):
    def wrap(fn):
        t0 = time.time()
        try:
            detail = fn()
            results.append(("ok  ", name, f"{time.time() - t0:.1f}s {detail or ''}"))
        except Exception as e:
            results.append(("FAIL", name, f"{type(e).__name__}: {e}".splitlines()[0][:300]))
            if os.getenv("SMOKE_VERBOSE"):
                traceback.print_exc()
        return fn
    return wrap


@check("anthropic plan model")
def _():
    from config import PLAN_MODEL
    from steps.llm import client
    r = client.messages.create(model=PLAN_MODEL, max_tokens=64, messages=[{"role": "user", "content": "Reply OK."}])
    return f"{PLAN_MODEL} stop={r.stop_reason}"


@check("anthropic fetch model")
def _():
    from config import FETCH_MODEL
    from steps.llm import client
    r = client.messages.create(model=FETCH_MODEL, max_tokens=64, output_config={"effort": "low"},
                               messages=[{"role": "user", "content": "Reply OK."}])
    return f"{FETCH_MODEL} stop={r.stop_reason}"


@check("child sandbox: create, run, network blocked, delete")
def _():
    from steps import compute
    t0 = time.time()
    child = compute.new_child()
    cold = time.time() - t0
    try:
        r = child.process.code_run('import json; print(json.dumps({"eligible": True, "amount": 1}))')
        assert r.exit_code == 0 and '"amount": 1' in r.result, r.result
        net = child.process.code_run(
            "import urllib.request\ntry:\n    urllib.request.urlopen('https://example.com', timeout=5); print('NET_OPEN')\n"
            "except Exception as e:\n    print('NET_BLOCKED', type(e).__name__)")
        env = child.process.code_run("import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k))")
    finally:
        child.delete()
    return f"cold={cold:.1f}s {net.result.strip()} env_keys={env.result.strip()}"


@check("anchor fetch_webpage")
def _():
    from steps.fetch import get_text
    t = get_text("https://www.tax.ny.gov/pit/credits/hire_a_veteran_credit.htm")
    assert "veteran" in t.lower(), t[:200]
    return f"{len(t)} chars"


@check("pdf fetch")
def _():
    from steps.fetch import get_text
    t = get_text("https://www.tax.ny.gov/pdf/current_forms/ct/ct643i.pdf")
    assert "1,820" in t, t[:200]
    return f"{len(t)} chars"


@check("surrealdb write/read/LIVE + gate")
def _():
    from steps import store
    got = []
    threading.Thread(target=lambda: store.live_claims(got.append), daemon=True).start()
    time.sleep(1)
    c = store.create_claim(run_id="smoke", company_id="smoke", program_id="smoke", state="unverified",
                           citations=[], agent_value=100.0)
    store.send_for_verification(c["id"])
    store.write_cpa(c["id"], 100.0, True, "confirmed")
    after = store.get_claim(c["id"])
    time.sleep(1)
    try:
        store.set_state(c["id"], "approved", cpa_value=1.0)
        refused = False
    except Exception:
        refused = True
    store.q("DELETE $c", c=store.rid(c["id"]))
    assert after["state"] == "ready", after["state"]
    assert refused, "gate allowed approve on mismatched values"
    assert got, "no LIVE notifications"
    return f"live_events={len(got)} gate_refused_mismatch={refused}"


@check("agent memory (optional)")
def _():
    url, ctx, key = os.getenv("MEMORY_URL"), os.getenv("MEMORY_CONTEXT"), os.getenv("MEMORY_API_KEY")
    if not (url and ctx and key):
        return "SKIP: MEMORY_* unset"
    h = {"Authorization": f"Bearer {key}"}
    r = httpx.get(f"{url.rstrip('/')}/api/v1/{ctx}/me", headers=h, timeout=15)
    r.raise_for_status()
    return f"/me {r.status_code}"


@check("elevenlabs key")
def _():
    if not os.getenv("ELEVENLABS_API_KEY"):
        return "SKIP: unset"
    r = httpx.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=15)
    r.raise_for_status()
    agent_id = os.getenv("ELEVENLABS_AGENT_ID")
    if agent_id:
        a = httpx.get(f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
                      headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=15)
        return f"user ok, agent {a.status_code}"
    return "user ok, ELEVENLABS_AGENT_ID unset"


@check("public preview link -> /healthz and /api/cpa_tool")
def _():
    base = os.getenv("PUBLIC_BASE_URL")
    if not base:
        return "SKIP: PUBLIC_BASE_URL unset"
    h = {"X-Daytona-Skip-Preview-Warning": "true"}
    r = httpx.get(f"{base}/healthz", headers=h, timeout=15)
    tool = httpx.post(f"{base}/api/cpa_tool", headers={**h, "X-Tool-Secret": os.getenv("CPA_TOOL_SECRET", "")},
                      json={"code": "000000", "value": 1, "confirmed": True}, timeout=15)
    bare = httpx.get(f"{base}/healthz", timeout=15)
    return (f"healthz={r.status_code} tool(bad code)={tool.status_code} (want 403) "
            f"without-skip-header={bare.status_code} {bare.headers.get('content-type')}")


@check("arize span export")
def _():
    if not os.getenv("ARIZE_SPACE_ID"):
        return "SKIP: ARIZE_* unset"
    from opentelemetry import trace
    from tracing import span
    with span("smoke.parent", claim_id="smoke"):
        with span("smoke.child", kind="TOOL", claim_id="smoke"):
            pass
    ok = trace.get_tracer_provider().force_flush(10_000)
    return f"flushed={ok}; check Arize project for trace 'smoke.parent'"


if __name__ == "__main__":
    w = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status}  {name:<{w}}  {detail}")
    raise SystemExit(any(s == "FAIL" for s, _, _ in results))

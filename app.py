import tracing  # noqa: F401  (first)

import json
import os
import queue
import threading
import time

from flask import Flask, Response, abort, jsonify, render_template, request

import agent
from config import PUBLIC_BASE_URL
from steps import store, voice

app = Flask(__name__)
_subscribers = []
_sub_lock = threading.Lock()


def _broadcast(record):
    # LIVE can deliver the gate event's write before the write that triggered it; send current state.
    record = store.get_claim(record["id"]) or record if record.get("id") else record
    with _sub_lock:
        for q in list(_subscribers):
            q.put(record)


def _live_loop():
    while True:
        try:
            store.live_claims(_broadcast)
        except Exception as e:
            app.logger.warning("live query dropped: %s; reconnecting", e)
            time.sleep(2)


def _public_claim(c):
    """What the CPA page may see. No agent_value, no formula, no code."""
    return {k: c.get(k) for k in ("id", "program_id", "state")} | {
        "subject_id": (c.get("agent_inputs") or {}).get("subject_id")}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    company, programs = agent.load_seed()
    return render_template("index.html", company=company, programs=programs)


@app.get("/cpa")
def cpa():
    cid = request.args.get("claim", "")
    claim = store.get_claim(cid) if cid.startswith("claim:") else None
    if not claim or claim["state"] != "sent":
        return render_template("cpa.html", claim=None)
    company, programs = agent.load_seed()
    program = next((p for p in programs if p["program_id"] == claim["program_id"]), {})
    subject = agent.subjects_of(company).get((claim.get("agent_inputs") or {}).get("subject_id"), {})
    dyn = {"claim_id": claim["id"], "program_name": program.get("name", ""), "company_name": company["name"],
           "subject": ", ".join(x for x in (subject.get("name"), subject.get("description")) if x)}
    return render_template("cpa.html", claim=_public_claim(claim), code=claim["cpa_code"], program=program,
                           subject=subject, company=company, agent_id=os.getenv("ELEVENLABS_AGENT_ID", ""),
                           dynamic_variables=json.dumps(dyn))


@app.post("/api/run")
def api_run():
    threading.Thread(target=agent.run, daemon=True).start()
    return {"started": True}


@app.get("/api/claims")
def api_claims():
    return jsonify(store.list_claims())


@app.get("/api/log")
def api_log():
    return jsonify(store.q("SELECT * FROM run_log ORDER BY at DESC LIMIT 200"))


@app.get("/api/transcripts")
def api_transcripts():
    return jsonify(store.q("SELECT claim_id, conversation_id, text, at FROM transcript ORDER BY at DESC LIMIT 100"))


@app.get("/api/stream")
def api_stream():
    q = queue.Queue()
    with _sub_lock:
        _subscribers.append(q)

    def gen():
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    yield f"data: {json.dumps(q.get(timeout=15))}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with _sub_lock:
                _subscribers.remove(q)

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/claims/<claim_id>/send")
def api_send(claim_id):
    c = store.send_for_verification(claim_id)
    if not c:
        abort(409, "claim is not unverified")
    return {"cpa_url": f"{PUBLIC_BASE_URL}/cpa?claim={claim_id}", "code": c["cpa_code"]}


@app.post("/api/claims/<claim_id>/approve")
def api_approve(claim_id):
    try:
        c = store.approve(claim_id)
    except Exception as e:  # schema assert refused it
        abort(409, f"refused by gate: {e}")
    if not c:
        abort(409, "claim is not ready")
    return jsonify(c)


@app.post("/api/claims/<claim_id>/acknowledge")
def api_acknowledge(claim_id):
    """Human sign-off on a verdict with no figure to verify (not eligible, uncited, compute error)."""
    r = store.q("UPDATE ONLY $c SET human_approved_at = time::now() "
                "WHERE state IN ['not_eligible', 'uncited', 'compute_error'] AND superseded_by = NONE RETURN AFTER",
                c=store.rid(claim_id))
    if not r:
        abort(409, "only a verdict without a CPA figure can be acknowledged here")
    return jsonify(r)


@app.post("/api/claims/<claim_id>/reset")
def api_reset(claim_id):
    """Demo helper: put a claim back to unverified so the real CPA call can be run again."""
    r = store.q("UPDATE ONLY $c SET state = 'unverified', cpa_value = NONE, cpa_confirmed = NONE, cpa_code = NONE, "
                "cpa_at = NONE, cpa_transcript_ref = NONE WHERE superseded_by = NONE RETURN AFTER", c=store.rid(claim_id))
    if not r:
        abort(409, "claim not found or superseded")
    return jsonify(r)


@app.post("/api/cpa_tool")
def api_cpa_tool():
    if not voice.tool_secret_ok(request.headers.get("X-Tool-Secret")):
        abort(401)
    body = request.get_json(force=True, silent=True) or {}

    def on_reject(cid, statement):
        threading.Thread(target=agent.recompute_after_reject, args=(cid, statement), daemon=True).start()

    status, resp = voice.handle_tool_call(body, on_reject=on_reject)
    return jsonify(resp), status


@app.post("/api/elevenlabs/postcall")
def api_postcall():
    secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET")
    raw = request.get_data(as_text=True)
    if secret:
        from elevenlabs import ElevenLabs
        try:
            event = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY", "")).webhooks.construct_event(
                raw, request.headers.get("elevenlabs-signature", ""), secret)
        except Exception:
            abort(401)
    else:
        abort(401)
    if event.get("type") != "post_call_transcription":
        return {"ignored": event.get("type")}
    data = event["data"]
    dyn = (data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}
    claim_id = dyn.get("claim_id")
    transcript = "\n".join(f"{t.get('role')}: {t.get('message')}" for t in data.get("transcript", []) if t.get("message"))
    store.q("CREATE transcript CONTENT $c", c={"conversation_id": data.get("conversation_id"), "claim_id": claim_id,
                                               "text": transcript, "analysis": data.get("analysis"), "at": store.now()})
    if claim_id:
        threading.Thread(target=voice.cross_check, args=(claim_id, transcript, data.get("conversation_id")), daemon=True).start()
    return {"ok": True}


@app.get("/fixtures/uncitable.html")
def fixture_uncitable():
    return ("<html><body><h1>Fixture Program</h1><p>Businesses may be eligible for support. "
            "Contact your regional office to learn more about how this program can help you grow.</p></body></html>")


if __name__ == "__main__":
    store.db()
    threading.Thread(target=_live_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)

"""SurrealDB claims store. The ready/approved gate is enforced in the schema, not in Python:
a claim cannot enter `ready` or `approved` unless fn::matches() holds on the record itself."""
import hashlib
import re
import os
import secrets
import threading
from datetime import datetime, timezone

from surrealdb import RecordID, Surreal

from config import MATCH_TOLERANCE
from tracing import span

STATES = ["unverified", "uncited", "compute_error", "not_eligible", "sent", "confirmed",
          "rejected", "needs-info", "ready", "approved"]

SCHEMA = f"""
DEFINE PARAM OVERWRITE $tolerance VALUE {MATCH_TOLERANCE};
DEFINE FUNCTION OVERWRITE fn::matches($c: object) {{
  RETURN $c.cpa_confirmed = true AND $c.agent_value != NONE AND $c.cpa_value != NONE AND $c.superseded_by = NONE
    AND math::abs($c.agent_value - $c.cpa_value) <= $tolerance * math::max([math::abs($c.agent_value), 1]);
}};

DEFINE TABLE OVERWRITE source SCHEMALESS;
DEFINE INDEX OVERWRITE source_url ON source FIELDS url, content_hash UNIQUE;

DEFINE TABLE OVERWRITE claim SCHEMALESS;
DEFINE FIELD OVERWRITE state ON claim TYPE string
  ASSERT $value IN {STATES}
  AND ($value NOT IN ['ready', 'approved'] OR fn::matches($this));
DEFINE FIELD OVERWRITE created_at ON claim TYPE datetime DEFAULT time::now() READONLY;
DEFINE INDEX OVERWRITE claim_code ON claim FIELDS cpa_code;
DEFINE EVENT OVERWRITE gate ON claim
  WHEN $event = 'UPDATE' AND $after.state = 'confirmed' AND fn::matches($after)
  THEN (UPDATE $after.id SET state = 'ready');

DEFINE TABLE OVERWRITE run_log SCHEMALESS;
DEFINE TABLE OVERWRITE transcript SCHEMALESS;
"""

_lock = threading.Lock()
_db = None


def _connect():
    db = Surreal(os.getenv("SURREAL_URL", "ws://127.0.0.1:8001/rpc"))
    db.signin({"username": os.getenv("SURREAL_USER", "root"), "password": os.getenv("SURREAL_PASS", "root")})
    db.use(os.getenv("SURREAL_NS", "incentive"), os.getenv("SURREAL_DB", "agent"))
    return db


def db():
    global _db
    if _db is None:
        _db = _connect()
        _db.query(SCHEMA)
    return _db


def q(sql, **vars):
    with _lock:
        return plain(db().query(sql, vars))


def plain(v):
    if isinstance(v, RecordID):
        return f"{v.table_name}:{v.id}"
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plain(x) for x in v]
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def rid(s):
    table, _, key = s.partition(":")
    return RecordID(table, key)


def now():
    return datetime.now(timezone.utc).isoformat()


def log(run_id, step, msg, **data):
    q("CREATE run_log CONTENT $c", c={"run_id": run_id, "step": step, "msg": msg, "data": data, "at": now()})


# ---- sources: a page whose content changed supersedes, never overwrites ----

def _content_key(text):
    # Version on visible content: markup attributes (e.g. an injected id="table-1") churn between renders.
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def upsert_source(program_id, url, text, fetched_at):
    h = hashlib.sha256(_content_key(text).encode()).hexdigest()
    with span("store.upsert_source", kind="CHAIN", program_id=program_id, url=url, content_hash=h):
        existing = q("SELECT * FROM source WHERE url = $u AND content_hash = $h", u=url, h=h)
        if existing:
            return existing[0]
        new = q("CREATE ONLY source CONTENT $c", c={"program_id": program_id, "url": url, "content_hash": h,
                                                   "text": text, "fetched_at": fetched_at, "superseded_by": None})
        q("UPDATE source SET superseded_by = $n WHERE url = $u AND id != $n AND superseded_by = NONE",
          n=rid(new["id"]), u=url)
        return new


def source_is_live(source_id):
    r = q("SELECT superseded_by FROM ONLY $s", s=rid(source_id))
    return bool(r) and r.get("superseded_by") is None


# ---- claims ----

def create_claim(run_id, company_id, program_id, state, citations, agent_value=None, agent_inputs=None,
                 formula=None, eligible=None, reason=None, supersedes=None):
    c = {"run_id": run_id, "company_id": company_id, "program_id": program_id, "state": state,
         "citations": citations, "agent_value": agent_value, "agent_inputs": agent_inputs, "formula": formula,
         "eligible": eligible, "reason": reason, "cpa_value": None, "cpa_confirmed": None,
         "cpa_transcript_ref": None, "cpa_at": None, "cpa_code": None, "superseded_by": None,
         "supersedes": supersedes}
    with span("store.create_claim", kind="CHAIN", program_id=program_id, state=state) as s:
        claim = q("CREATE ONLY claim CONTENT $c", c=c)
        s.set_attribute("claim_id", claim["id"])
        if supersedes:
            q("UPDATE $o SET superseded_by = $n", o=rid(supersedes), n=rid(claim["id"]))
        return claim


def get_claim(claim_id):
    return q("SELECT * FROM ONLY $c", c=rid(claim_id))


def list_claims():
    return q("SELECT * FROM claim ORDER BY created_at DESC")


def send_for_verification(claim_id):
    """unverified -> sent, minting the one-time code shown on /cpa."""
    code = f"{secrets.randbelow(10**6):06d}"
    r = q("UPDATE $c SET state = 'sent', cpa_code = $code WHERE state = 'unverified' RETURN AFTER",
          c=rid(claim_id), code=code)
    return r[0] if r else None


def claim_by_code(code):
    r = q("SELECT * FROM claim WHERE cpa_code = $code AND state = 'sent' AND superseded_by = NONE", code=code)
    return r[0] if r else None


def write_cpa(claim_id, value, confirmed, state, transcript_ref=None):
    """The only write the voice path may make: CPA fields on one sent claim. Burns the code."""
    return q("UPDATE ONLY $c SET cpa_value = $v, cpa_confirmed = $ok, cpa_transcript_ref = $t, cpa_at = time::now(), "
             "cpa_code = NONE, state = $s WHERE state = 'sent' RETURN AFTER",
             c=rid(claim_id), v=value, ok=confirmed, t=transcript_ref, s=state)


def set_state(claim_id, state, **fields):
    return q("UPDATE ONLY $c MERGE $f RETURN AFTER", c=rid(claim_id), f={"state": state, **fields})


def approve(claim_id):
    """Human click only. The schema assert refuses anything that doesn't match."""
    return q("UPDATE ONLY $c SET state = 'approved' WHERE state = 'ready' RETURN AFTER", c=rid(claim_id))


def live_claims(callback):
    """Blocking. Runs a LIVE SELECT on its own connection and calls callback(record) per change."""
    db()  # schema first: LIVE on an undefined table errors in SurrealDB 3
    conn = _connect()
    for change in conn.subscribe_live(conn.live("claim")):
        callback(plain(change))

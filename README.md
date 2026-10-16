# TaxBeagle (Incentive Agent)

Finds tax credits for a seeded company, cites every eligibility fact verbatim from the official source,
computes the credit in a keyless, network-blocked Daytona child sandbox, gets an independent figure from a
CPA over an ElevenLabs voice call, and stops at a human approval gate that SurrealDB itself enforces.

```
laptop ── deploy.py ──▶ Daytona parent sandbox (public preview :8000)
                          ├─ app.py (Flask: board, /cpa, /api/cpa_tool, /api/elevenlabs/postcall, SSE)
                          ├─ agent.py: plan (Fable 5.1) → fetch+cite (Anchor, Opus 5.5) → compute → store
                          ├─ surreal server 127.0.0.1:8001 (claims, sources, LIVE SELECT, schema gate)
                          └─▶ child sandboxes: ephemeral, network_block_all, no env  (generated code only)
outbound: Anthropic, Anchor, Arize OTLP, ElevenLabs, SurrealDB Agent Memory (optional)
inbound:  CPA browser → /cpa (ElevenLabs widget); ElevenLabs → /api/cpa_tool, /api/elevenlabs/postcall
```

## Runbook

```bash
# Keys: either a local .env (cp .env.example .env), or, for cloud-only dev, environment variables in the
# Claude Code cloud environment settings. deploy.py writes .env from them when no .env exists.
# CPA_TOOL_SECRET: python -c "import secrets;print(secrets.token_urlsafe(24))"
pip install daytona==0.224.1 python-dotenv  # the deploy host only needs these
bash netcheck.sh                            # step 0, from wherever deploy.py runs
python deploy.py --netcheck                 # creates (or reattaches by name to) the parent; step 0 from inside it
python deploy.py                            # installs deps + surreal, starts db and web, prints preview URL
#   PUBLIC_BASE_URL is injected into the parent's .env automatically. Configure ElevenLabs per ELEVENLABS.md.
python deploy.py                            # redeploy (~10s; pip only reruns when requirements.txt changes)
python deploy.py --smoke                    # all setup smoke tests, one line each
python deploy.py --shell "python -m evals.run"             # voice + invariant evals (no LLM spend)
python deploy.py --shell "python -m evals.run --pipeline"  # full run + pipeline cases
python deploy.py --logs
```

## Demo script

1. Board: Run agent. Log streams plan → fetch → compute; claims appear live.
2. Expected: `nys-hire-a-veteran / vet-ramos` unverified, **$12,600** (15% × $84,000, under the $15,000 full-time cap).
   `vet-okafor` not eligible (988 hours, 5% under the 1,040-hour floor). The 2020 version is superseded.
   The restaurant credit is expired.
3. Send to CPA on vet-ramos. The CPA page shows the code and inputs, never the agent's figure.
4. Teammate-as-CPA reads the code and says "twelve thousand six hundred." Board flips sent → ready live.
5. Click Approve. Show that approving a mismatched claim is refused by the database, not the app.
6. Re-run with a hedge ("about twelve six, I think") → needs-info; wrong code → 403, state unchanged.

## Where each safeguard lives

| Safeguard | Enforced in |
| --- | --- |
| Generated code never runs near keys | `steps/compute.py`: child has `env_vars={}`, `network_block_all=True`, `ephemeral=True` |
| Every eligibility fact is cited | `steps/fetch.py`: quote must be a literal substring of the fetched source; the LLM's word isn't trusted |
| Changed sources supersede, never overwrite | `steps/store.py` `upsert_source` (content hash) + `agent.py` program-level supersession |
| Voice identity, read-back, least authority, mismatch | `steps/voice.py`; voice agent is never given the agent value |
| Nothing reaches ready/approved without two matching values | SurrealDB schema: `ASSERT ... fn::matches($this)` on `claim.state`, `EVENT gate` |
| No automatic submit | No code path past `approved`; approve is a human POST only |

## Verified during setup (2026-10-16, SDK introspection + docs)

- Daytona SDK 0.224.1: `run_async` (not `var_async`), `upload_file(bytes, dst)`, batch `upload_files`,
  `network_block_all`, `ephemeral`, `secrets` (org Secrets substituted at egress), default API `https://app.daytona.io/api`.
- Daytona preview links: browsers get a warning interstitial unless the request sends
  `X-Daytona-Skip-Preview-Warning: true`. The ElevenLabs tool must send it. Smoke test reports both.
- Anchor SDK: host `https://api.anchorbrowser.io`; `client.tools.fetch_webpage(url=...) -> str`.
- Arize: `arize.otel.register`, endpoint `https://otlp.arize.com/v1`; default transport is gRPC, we use HTTP.
- SurrealDB Python SDK 2.0: LIVE only over WebSocket; embedded `mem://` LIVE fails. Hence the local server.
  LIVE + schema gate tested end to end against surreal 3.3.2.
- **SurrealDB Agent Memory is not a typed table.** It's an NL facts API (`/facts`, `/query` with `lens`,
  `asOf`, attribute supersession, `/uncertainty`) with no LIVE SELECT. So claims + gate live in a real
  SurrealDB; Agent Memory mirrors citations and outcomes as time-stamped facts (`steps/memory.py`, optional).
- Seed programs verified live: Hire a Veteran Credit (current page updated 2026-02-12; CT-643-I 2025),
  its 2020 instructions (10%/$5K, 35 h/week, replaced), and the Additional Restaurant Return-to-Work Credit
  (only for tax years including 2022-12-31).

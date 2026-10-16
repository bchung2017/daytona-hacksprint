# Incentive Agent: Daytona Scaffolding Tech Spec

Oct 16, 2026 · @B

## Summary

The incentive agent runs entirely inside one long-lived Daytona parent sandbox that hosts both the agent loop and the Flask UI; LLM-generated eligibility code runs in keyless ephemeral child sandboxes. The agent finds tax credits and grants for a seeded company, cites each eligibility fact, computes the credit, gets an independent value from a CPA over an ElevenLabs voice call, and stops at a human-approved submit gate.

Why Daytona as runtime from the start: the parent's preview link is the public URL for both the `/cpa` page and the ElevenLabs server-tool webhook, so no tunnel is needed. The parent holds every API key and the children hold none, so generated code never runs next to credentials. This is also the integration pattern the Daytona judge works on.

Submission deadline: 3:30 PM via [Devpost](https://daytona-hacksprint-nyc.devpost.com/). Pitch: 3 minutes plus 2 minutes Q&A if selected as a finalist.

## Credits and resources

Every participant gets credits from all six sponsors; redeem all of them during setup. Source: the HackSprint hacker resources page in the project files.

| Sponsor | What we get | Code | How to redeem | Role in this build |
| --- | --- | --- | --- | --- |
| Daytona | $100 credits | `DAYTONA_NYC_HACK_RDPTPZ4U` | app.daytona.io, then Billing Dashboard, paste, Redeem | Parent runtime + child code sandboxes |
| Arize AX | Pro plan, 3 months free ($150 value) | `HackSprintNYC26` | app.arize.com, Settings, Billing, choose Pro, enter at checkout | Tracing + adversarial evals |
| SurrealDB | $50 Agent Memory credit | `HACKSPRINTNYC50` | studio.surrealdb.com, Billing dashboard, Redeem; then Agent Memory, Deploy new context | Claims store + schema-enforced gate (self-hosted SurrealDB in the parent); Agent Memory for time-stamped provenance facts |
| Anchor Browser | $200 credits | `ANCHOR_HACKSPRINT_10_16_26` (redeem by Oct 19) | Anchor claim page linked on the resources page | Cloud browser for fetch-and-cite |
| ElevenLabs | Creator plan, 1 month ($22 value) | Issued by Discord bot | Join their Discord, open the coupon-codes channel, Start Redemption, use your registration email | CPA voice-verification agent |
| AWS | Lab account | One-time password emailed | Link on the resources page | Not load-bearing; optional |

SurrealDB quick paths: skill at sdb.li/skills, SDKs at surrealdb.com/docs/agent-memory/reference, hosted REST quickstart at surrealdb.com/docs/agent-memory/quickstarts/hosted. Anchor's recommended setup matches ours: the agent runs in Daytona and Anchor runs the browser.

Prizes this build targets:

- Overall top 3, with judging split evenly across impact, technical execution, creativity, and presentation, plus a sponsor-tool bonus
- Best Use of Daytona: $1,000 + $10,000 credits
- Best Use of SurrealDB: $500 + $1,000 credits. They want typed, time-aware memory, facts that supersede rather than overwrite, and traceable retrieval, which is exactly the claim model here.
- Best Use of Arize: $500 + 1 year AX Pro
- Best Use of ElevenLabs: 3 months Scale tier per team member
- Best Use of Anchor: $500 + $5,000 credits

## Step 0: network reachability check

Run this before redeeming anything else or writing app code. Run it from the Claude Code sandbox you develop in, and again from the Daytona parent right after its first create. Both environments can sit behind egress allowlists, and a blocked host changes the topology.

Any HTTP status, including 401 and 404, means the host is reachable. `000` means DNS failure, timeout, or a refused connection. A 403 that carries an `x-deny-reason` header comes from the sandbox's egress proxy, not the vendor, so the script treats it as blocked.

```bash
#!/usr/bin/env bash
# netcheck.sh
URLS=(
  https://api.anthropic.com
  https://app.daytona.io
  https://api.anchorbrowser.io
  https://otlp.arize.com
  https://app.arize.com
  https://api.elevenlabs.io
  https://pypi.org/simple/
  https://files.pythonhosted.org
  https://github.com
  https://download.surrealdb.com
  "${MEMORY_URL:-}"
)
fail=0
for u in "${URLS[@]}"; do
  [ -z "$u" ] && { echo "SKIP  MEMORY_URL unset"; continue; }
  hdr=$(mktemp)
  code=$(curl -s -o /dev/null -D "$hdr" -m 8 -w '%{http_code}' "$u")
  deny=$(grep -i '^x-deny-reason' "$hdr" | tr -d '\r')
  rm -f "$hdr"
  if [ "$code" = "000" ] || [ -n "$deny" ]; then
    echo "FAIL  $code $u $deny"; fail=1
  else
    echo "ok    $code $u"
  fi
done
exit $fail
```

From the parent: upload the script with the other files, then run `sb.process.exec("bash app/netcheck.sh")` and print the result. A nonzero exit means at least one host is blocked.

Hosts to confirm against vendor docs before trusting a pass:

- **Anchor:** confirm the API host. `api.anchorbrowser.io` is my best recollection, not verified.
- **Arize:** confirm the OTLP endpoint in the starter pack. It may be region-specific.
- **SurrealDB:** the claims database runs inside the parent, so only `download.surrealdb.com` (binary install) needs egress. The Agent Memory host is per-context, from Studio (`MEMORY_URL`, `https://` form).
- **Daytona:** the SDK's API base URL may differ from `app.daytona.io`. Check the SDK config default.

What to do on a failure:

- **Fails in the Claude Code sandbox only:** add the host to that environment's network allowlist. Nothing in the architecture changes.
- **Fails in the Daytona parent:** ask Daytona staff on the floor about the egress tier for hackathon accounts before redesigning. The fallback is to run the parent's work on the laptop and keep only the children in Daytona, which also brings back the tunnel for the webhook.

## Architecture

&#91;embedded content: sandbox topology · parent, children, five services\]

The laptop only deploys. The parent sandbox runs the agent loop, Flask, and a SurrealDB server bound to 127.0.0.1:8001. It calls Anthropic, Anchor, SurrealDB Agent Memory, and Arize outbound, and receives the ElevenLabs server-tool webhook on its preview link. The CPA's browser loads `/cpa` from the same link and talks to the ElevenLabs agent through the embed. Generated code only ever runs in the keyless children.

## Repo layout and environment

One public GitHub repo with a flat layout. Only `deploy.py` runs on the laptop; everything else runs in the parent sandbox.

```
incentive-agent/
  deploy.py            # laptop: create/reattach parent, upload, (re)start
  app.py               # Flask: UI, /cpa, /api/*, ElevenLabs tool endpoint; binds 0.0.0.0:8000
  agent.py             # loop: plan -> fetch -> compute -> store
  steps/
    plan.py            # Fable 5.1
    fetch.py           # Opus 5.5 + Anchor
    compute.py         # child sandbox exec
    store.py           # SurrealDB schema, gate, writes, LIVE query
    memory.py          # SurrealDB Agent Memory mirror (REST), optional
    voice.py           # tool handler + safety checks
  tracing.py           # Arize/OpenInference setup, imported first
  seed/company.json
  seed/programs.json   # 1 current + 1 expired/changed program
  evals/cases.json     # 5 adversarial cases
  templates/index.html, templates/cpa.html
  requirements.txt
  .env                 # uploaded to parent only; gitignored
```

The variable names below are ours unless the vendor SDK reads a fixed name. Check each SDK's README.

| Variable | Used by | Notes |
| --- | --- | --- |
| `DAYTONA_API_KEY` | `deploy.py`, `compute.py` | Parent needs it to spawn children |
| `ANTHROPIC_API_KEY` | plan, fetch, voice parse | Confirm Fable 5.1 access; fallback is Opus for both roles |
| `ANCHOR_API_KEY` | fetch |  |
| `SURREAL_URL`, `SURREAL_USER`, `SURREAL_PASS`, `SURREAL_NS`, `SURREAL_DB` | store | Claims database. Defaults to the parent-local server (`ws://127.0.0.1:8001/rpc`, root/root, reachable only inside the parent) |
| `MEMORY_URL`, `MEMORY_CONTEXT`, `MEMORY_API_KEY`, `MEMORY_SCOPE` | memory | Agent Memory context from Studio: host, context id, `sp-...` key (shown once). Optional; the mirror is skipped when unset |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` | `/cpa` page embed, voice |  |
| `ARIZE_SPACE_ID`, `ARIZE_API_KEY` | tracing |  |
| `PUBLIC_BASE_URL` | app, ElevenLabs tool config | The parent's preview link, set after the first deploy |

Child sandboxes get no env vars and no `.env`.

## Deploy and redeploy

`deploy.py` creates the parent sandbox once, then reattaches to it by ID. Each run uploads the files, reinstalls requirements only when they've changed, restarts the `web` session, and prints the preview link. A redeploy should take about 10 seconds.

```python
import sys
from pathlib import Path
from daytona import Daytona, CreateSandboxFromSnapshotParams, SessionExecuteRequest

FILES = [p for p in Path(".").rglob("*") if p.is_file() and ".git" not in p.parts and p.name not in ("deploy.py", ".sandbox_id")]
ID_FILE = Path(".sandbox_id")

d = Daytona()
first = not ID_FILE.exists()
if first:
    sb = d.create(CreateSandboxFromSnapshotParams(language="python", public=True, auto_stop_interval=0))
    ID_FILE.write_text(sb.id)
else:
    sb = d.get(ID_FILE.read_text().strip())

for p in FILES:
    sb.process.exec(f"mkdir -p app/{p.parent}")
    sb.fs.upload_file(p.read_bytes(), f"app/{p}")

if first or "--install" in sys.argv:
    sb.process.exec("cd app && pip install -r requirements.txt", timeout=300)

if not first:
    sb.process.delete_session("web")
sb.process.create_session("web")
sb.process.execute_session_command("web", SessionExecuteRequest(command="cd app && python app.py", run_async=True))
print(sb.id, sb.get_preview_link(8000).url)
```

Check these against the Daytona LLMs text before running:

- `public=True`: without it, preview links require a token header, which the ElevenLabs webhook won't send by default.
- `auto_stop_interval=0`: disables auto-stop so the parent survives the demo.
- Async flag: older docs use `var_async=True`; current releases may use `run_async=True`.
- `upload_file` argument order: current is `(bytes, path)`; older versions used `(path, bytes)`.
- Package: `daytona` (older name: `daytona-sdk`).
- Install gating: dependencies install on first create; pass `--install` after editing `requirements.txt`.
- Logs: read the session command's logs with the SDK's session-log call. Confirm the exact method name in the docs.

Child sandbox, in `steps/compute.py`:

```python
child = d.create(CreateSandboxFromSnapshotParams(language="python", ephemeral=True))
try:
    r = child.process.code_run(generated_code)
finally:
    child.delete()
```

The generated code receives its inputs inlined as literals and prints one JSON line, `{eligible, amount, inputs, formula}`, which the parent parses. If cold starts are slow, keep one warm keyless child and reuse it across claims instead of creating one per claim.

## Pipeline step contracts

Each step is a function that takes a typed input and returns a typed output. Every step runs in the parent and is wrapped in an Arize span. The field names are proposals; lock them in during the first hour.

| # | Step | Runs on | Input | Output | Failure behavior |
| --- | --- | --- | --- | --- | --- |
| 1 | Intake | Flask | `seed/company.json` | `company` (employees, payroll, revenue, NAICS, location, FY) | None; the profile is seeded |
| 2 | Plan | Fable 5.1 | `company` + `programs.json` candidates | JSON list `[{program_id, why, criteria_to_check[]}]` | Bad JSON leads to one retry, then the run is aborted with the reason shown on the board |
| 3 | Fetch and cite | Opus 5.5 + Anchor | Program URL + `criteria_to_check` | `[{criterion, value, quote, source_url, fetched_at}]` | A criterion with no supporting quote gets `uncited` and the claim stops there |
| 4 | Compute | Child sandbox | `company` + cited criteria | `{eligible, amount, inputs, formula}` from generated code | Nonzero exit or unparseable output marks the claim `compute_error` |
| 5 | Store | SurrealDB | Outputs of steps 3 and 4 | Claim record with `agent_value` and state `unverified` | A program whose source changed is superseded, not overwritten |
| 6 | CPA verify | ElevenLabs agent to `/api/cpa_tool` | Claim ID + spoken value + code | `cpa_value`, `cpa_confirmed`, transcript ref; state becomes `confirmed`, `rejected`, or `needs-info` | See the safety checks section |
| 7 | Gate | SurrealDB schema + your click | Claims where `agent_value` and `cpa_value` match within tolerance | State `ready`, then `approved` on click | Nothing reaches `ready` without both values; no code path submits automatically |

Claim states: `unverified → sent → confirmed | rejected | needs-info → ready → approved`. A rejected claim creates a superseding claim with the corrected input and re-runs step 4.

## SurrealDB design

Verified during setup: Agent Memory cannot be the claims store. It is a natural-language facts API (`POST /api/v1/{ctx}/facts`, `/query` with `lens` and `asOf`, attribute supersession with `/history`, `/uncertainty` for contradictions). It has no LIVE SELECT and no typed record that a state machine can be built on. The Python SurrealDB SDK (2.0) also only supports LIVE over WebSocket, so the embedded `mem://` engine can't drive the board. So the design uses two layers:

| Layer | What | Where | Holds |
| --- | --- | --- | --- |
| Claims database | `surreal` 3.3.2 server, `surrealkv://data/db` | Parent sandbox, 127.0.0.1:8001, own `db` session; installed by `deploy.py` | `claim`, `source`, `run_log`, `transcript` tables; the gate |
| Agent Memory | Hosted context (the $50 credit) | SurrealDB Cloud, `MEMORY_URL` | Every cited quote and claim outcome as a fact, `observed_at` = fetch time |

Swapping the claims database to a SurrealDB Cloud instance is only a `SURREAL_URL`/`SURREAL_USER`/`SURREAL_PASS` change.

### The gate lives in the schema

A claim can't enter `ready` or `approved` unless `fn::matches()` holds on the record itself. Python never decides it, and the app can't override it:

```sql
DEFINE PARAM $tolerance VALUE 0.01;
DEFINE FUNCTION fn::matches($c: object) {
  RETURN $c.cpa_confirmed = true AND $c.agent_value != NONE AND $c.cpa_value != NONE AND $c.superseded_by = NONE
    AND math::abs($c.agent_value - $c.cpa_value) <= $tolerance * math::max([math::abs($c.agent_value), 1]);
};
DEFINE FIELD state ON claim TYPE string
  ASSERT $value IN [...states] AND ($value NOT IN ['ready', 'approved'] OR fn::matches($this));
DEFINE EVENT gate ON claim
  WHEN $event = 'UPDATE' AND $after.state = 'confirmed' AND fn::matches($after)
  THEN (UPDATE $after.id SET state = 'ready');
```

The match condition sits in the event's `WHEN`, not only in the assert. With it only in the assert, a mismatched CPA value makes the event's write fail, and that aborts the CPA write too. Tested against surreal 3.3.2: a match goes to `ready`, a mismatch stays put (the voice handler sets `rejected`), and `approved` on a mismatch is refused by the database.

### Supersession, never overwrite

- **Sources:** keyed by `(url, content_hash)`. A changed page creates a new `source` and stamps the old one `superseded_by`.
- **Programs:** `programs.json` declares `supersedes`/`superseded_by`. After every fetch in a run, the old version's live sources are marked superseded, so plan ordering can't leave them live.
- **Claims:** a rejected claim gets a superseding claim (`supersedes` on the new one, `superseded_by` on the old). `fn::matches` requires `superseded_by = NONE`, so a superseded claim can never become `ready`.

### LIVE query

`LIVE SELECT` on `claim` runs on its own connection and feeds the board over SSE. LIVE can deliver the gate event's write (`ready`) before the write that triggered it (`confirmed`). So the broadcaster re-reads the record and sends current state; otherwise the board would show a stale `confirmed` with no Approve button. In SurrealDB 3, LIVE on an undefined table errors, so the schema is applied first.

### Claim record

```
claim {
  run_id, program_id, company_id,
  agent_value, agent_inputs {subject_id, code, child_id, ...}, formula, eligible, reason,
  citations: [{criterion, value, quote, source_url, fetched_at, source_id, cited}],
  cpa_value, cpa_confirmed, cpa_transcript_ref, cpa_at, cpa_code, transcript_parse,
  state, supersedes, superseded_by, created_at
}
source { program_id, url, content_hash, text, fetched_at, superseded_by }
```

`cpa_code` is the one-time code, and the CPA write clears it.

### Agent Memory mirror

`steps/memory.py` posts to `/api/v1/{ctx}/facts` with `infer: "full"`, `scopes` only when `MEMORY_SCOPE` is set, `observed_at` = fetch time, and an `Idempotency-Key` per claim and criterion. It runs off the request path and never blocks or fails a claim. The pitch line: `asOf` recall answers "what did the agent believe the Hire a Veteran terms were on date X", and that answer comes from SurrealDB's own supersession chain.

Scopes: a write without `scopes` is tagged with the key's default `memory:write` region, so no registration is needed. If `MEMORY_SCOPE` is set, register that path first in Studio > Scopes, or scoped writes are refused.

## CPA voice verification and safety checks

The CPA talks to an ElevenLabs conversational agent embedded on `/cpa`, served from the parent's preview link. A teammate plays the CPA, and we say so on stage. An outbound phone call through ElevenLabs' Twilio integration is a stretch goal; the agent config stays the same.

Write-back has two parts. During the call, the agent calls a server tool, `record_cpa_value(claim_id, code, value, confirmed)`, which posts to `PUBLIC_BASE_URL/api/cpa_tool`, so the board updates while the call is still going. After the call, the transcript is stored as provenance, and Fable re-parses it as a cross-check. If the parse disagrees with the tool call, the claim drops to `needs-info`.

The safety checks are enforced in `steps/voice.py`, not in the prompt:

1. Identity. `/cpa` shows a one-time code for each verification. The handler rejects any write whose code doesn't match the active claim.
2. Read-back. The agent must repeat the value and get an explicit yes. `confirmed=false` or a hedged answer ("about", "roughly", "I think") sets the claim to `needs-info`.
3. Least authority. The agent's only tool writes `cpa_value` on one claim. It has no access to state transitions past verification and no submit path.
4. Mismatch. A value outside tolerance marks the claim `rejected`, creates a superseding claim, and re-runs step 4. It never resolves to `confirmed`.

The agent's system prompt should cover: introduce the purpose, ask for the code, state the claim and the agent's computed value, ask for the CPA's independent figure, read it back, then call the tool. It must never volunteer the agent's value before the CPA gives theirs; otherwise the CPA anchors on it and the value isn't independent.

Open question: ElevenLabs post-call evaluation criteria and data collection on agents could duplicate checks 2 and 4 on their side. Check the agent settings and mirror the results into Arize if it's cheap.

## Observability and evals

Tracing goes in at minute zero. `tracing.py` registers an OTel exporter to Arize and enables OpenInference's Anthropic auto-instrumentation, so every Fable and Opus call becomes a span without extra code. Package names to confirm in the Arize hacker starter pack: `arize-otel` and `openinference-instrumentation-anthropic`.

Manual spans cover the non-LLM steps: one per Anchor fetch, one per child-sandbox run (sandbox ID, exit code, duration), one per SurrealDB write, and one per CPA tool call with the transcript ref attached. One run equals one trace, with `claim_id` as an attribute on every span.

The Arize experiment runs `evals/cases.json` through the full pipeline. Run it after the end-to-end path works and before the UI polish.

| Case | Expected end state | Eval that must pass |
| --- | --- | --- |
| Expired or superseded program | Not eligible, old record superseded | No claim cites a superseded source |
| Company misses a threshold by 5% | Not eligible | Eligibility only claimed with a citation for every criterion |
| Program page with no citable criterion | `uncited`, stops before compute | Eligibility only claimed with a citation for every criterion |
| CPA hedges on the value | `needs-info` | Nothing reaches `ready` without two matching independent values |
| CPA gives the wrong code | Write rejected, state unchanged | Nothing reaches `ready` without two matching independent values |

Two code evals are enough: "eligibility claimed without full citations" and "reached ready without matching independent values." Both are deterministic checks against the stored claim, not LLM judges. Show the pass table on the safeguards slide.

## Unverified assumptions and smoke tests

Run these during setup, in order. The first one can invalidate the whole topology, so it goes before any app code.

- [ ] Parent egress (netcheck.sh, step 0): from the parent, `curl` api.anthropic.com, the Anchor API, the SurrealDB cloud endpoint, the Arize OTLP endpoint, and api.elevenlabs.io. Daytona may restrict egress on lower tiers. If blocked, ask Daytona staff on the floor before redesigning.
- [ ] Child creation from inside the parent works with `DAYTONA_API_KEY`, and the cold-start time is acceptable for each claim.
- [ ] Preview link on port 8000 opens publicly, with no token header, when the sandbox is created with `public=True`.
- [ ] The parent stays up past 15 minutes idle with `auto_stop_interval=0`.
- [ ] `ANTHROPIC_API_KEY` can call Fable 5.1. If not, Opus does both roles; update the pitch line.
- [ ] Anchor session loads one target program page and returns text through the SDK.
- [ ] SurrealDB claims database in the parent: one write, one read, a LIVE notification, and the gate refusing `approved` on mismatched values (`smoke.py` covers all four).
- [ ] SurrealDB Agent Memory: `/me` answers with the context key; one fact write succeeds.
- [ ] ElevenLabs agent embed renders on `/cpa`, and a test server-tool call reaches `/api/cpa_tool` through the preview link.
- [ ] One trace shows up in Arize with a nested span.
- [ ] Two real NY programs picked, one current and one expired or changed. Seed company profile clears one threshold and misses another by about 5%.

## Timeline and submission

Submit by 3:20 PM to leave a 10-minute buffer before the 3:30 deadline. Lunch is at 12:30; eat at the keyboard.

- [ ] Setup, to \~12:00: run netcheck.sh from the Claude Code sandbox, redeem all credits, run netcheck.sh again from the parent, run the smoke tests, first deploy of a Flask hello-world in the parent, paste the preview link into the ElevenLabs tool URL
- [ ] 12:00–12:45: steps 2–5 inside the parent, using a child sandbox for the math
- [ ] 12:45–1:45: ElevenLabs agent, `/api/cpa_tool` with the four safety checks, schema gate + LIVE board
- [ ] 1:45–2:30: three-column UI (profile, live run log, claims board with state chips), run the 5-case Arize experiment
- [ ] 2:30–2:50: one stretch goal only: Twilio outbound call
- [ ] 2:50–3:20: demo video under 2 minutes, Devpost, submit

Devpost checklist, from the event's submission requirements:

- [ ] Unique team name; all members listed with emails and socials
- [ ] Demo video under 2 minutes
- [ ] 2–3 sentence summary
- [ ] Problem and impact
- [ ] Architecture and components (reuse the diagram above)
- [ ] Every sponsor tool used and how it's integrated: Daytona, Arize, SurrealDB, Anchor, ElevenLabs
- [ ] Public GitHub repo URL, with `.env` gitignored

Pitch skeleton for 3 minutes: opener hook, the problem, a live run through the CPA call and the board flip, the safeguards slide (keyless children, 4 voice checks, Arize pass table), then the ask.

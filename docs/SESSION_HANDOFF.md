# Session handoff: deploy and verify

Seed for a new Claude Code cloud session. Keys are provided as environment variables; never print or commit their values.
Event: Daytona HackSprint NYC, 2026-10-16. Submit by 3:20 PM ET (deadline 3:30). Read `docs/tech-spec.md` for the design.

## 0. Orient (2 min)

```bash
cd /home/user/daytona-hacksprint
git fetch origin claude/vibrant-hopper-fz3g9w && git checkout claude/vibrant-hopper-fz3g9w && git pull
for k in DAYTONA_API_KEY ANTHROPIC_API_KEY APP_ANTHROPIC_API_KEY ANCHOR_API_KEY ELEVENLABS_API_KEY ELEVENLABS_AGENT_ID \
         CPA_TOOL_SECRET ARIZE_SPACE_ID ARIZE_API_KEY MEMORY_URL MEMORY_CONTEXT MEMORY_API_KEY; do
  [ -n "${!k}" ] && echo "set   $k" || echo "unset $k"; done
pip install -q daytona==0.224.1 python-dotenv
```

Report set/unset by name only. Interpretation:
- `ANTHROPIC_API_KEY` unset but `APP_ANTHROPIC_API_KEY` set: fine, `deploy.py` maps `APP_<KEY>` to `<KEY>`. If both are unset, the
  host may be filtering the name. Ask the user to add it as `APP_ANTHROPIC_API_KEY`.
- `CPA_TOOL_SECRET` unset: fine, `deploy.py` derives a stable one from `DAYTONA_API_KEY`. Get it with `--print-tool-secret`
  for the ElevenLabs header.
- `ELEVENLABS_AGENT_ID` unset: expected until the user creates the agent (`ELEVENLABS.md`). Redeploy after it's added.
- `.env` is generated from these on first `deploy.py` run. If a stale `.env` exists from an earlier container, delete it first.

Known values (not secrets): `MEMORY_URL=https://srv1.spectron.aws-usw2.surreal.cloud`, `MEMORY_CONTEXT=06gkb45hjls378qmh47t25po9k`.
Host verified (`/api/v1/health` 200). Context id not yet verified with a key.

## 1. Network check, both sides (5 min)

```bash
bash netcheck.sh                 # this container; passed in the previous session
python deploy.py --netcheck      # creates or reattaches the parent sandbox "incentive-parent", runs netcheck inside it
```

A FAIL inside the parent is the one result that changes the topology. Stop and report it. The fallback is to run the parent's work
in this container and keep only the children in Daytona.

## 2. Deploy (5 min)

```bash
python deploy.py                       # pip install (first time), surreal install, starts db + web sessions, prints preview URL
python deploy.py --print-tool-secret   # value for the ElevenLabs X-Tool-Secret header (give to the user, don't commit)
python deploy.py --logs                # if anything looks wrong
```

`PUBLIC_BASE_URL` is injected into the parent's `.env` automatically. Give the user:
- Tool URL: `<preview>/api/cpa_tool`
- Post-call webhook URL: `<preview>/api/elevenlabs/postcall`
- Board: `<preview>/`

## 3. Smoke tests (5 min)

```bash
python deploy.py --smoke
```

Expected lines and what to do on failure:

| Check | Pass looks like | If it fails |
| --- | --- | --- |
| anthropic plan model | `claude-fable-5-1 stop=end_turn` | 403/404: set `PLAN_MODEL=claude-opus-5-5` in the env and redeploy |
| anthropic fetch model | `claude-opus-5-5 stop=end_turn` | Key problem; report |
| child sandbox | `cold=Ns NET_BLOCKED ... env_keys=[]` | If `code_run` itself fails with network blocked, drop `network_block_all` in `steps/compute.py`, keep `env_vars={}`. If `NET_OPEN`, the flag isn't honored: say so in the pitch, don't claim it |
| anchor fetch_webpage | `N chars` | Fall back to httpx in `steps/fetch.get_text` for HTML, keep Anchor for screenshots |
| pdf fetch | `11xxx chars` | tax.ny.gov blocking: report |
| surrealdb write/read/LIVE + gate | `live_events>=1 gate_refused_mismatch=True` | `--logs`, check `db.log` |
| agent memory | `/me ok, fact write 200 chunkId=...` | 401 wrong key/context pair, 404 wrong context id, 403 key lacks write |
| elevenlabs key | `user ok, agent 200` | Agent id wrong or not created yet |
| public preview link | `healthz=200 tool(bad code)=403` | Note `without-skip-header` result; if it's HTML, the ElevenLabs tool must send `X-Daytona-Skip-Preview-Warning: true` |
| arize span export | `flushed=True` | Check the trace `smoke.parent` in the Arize UI with the user |

Record cold-start time for children. If it's over ~10s, set `COMPUTE_REUSE_CHILD=1` (one warm keyless child reused).

## 4. Evals without LLM spend, then the real pipeline (10 min)

```bash
python deploy.py --shell "python -m evals.run"              # 4 voice cases + 3 invariants, deterministic
python deploy.py --shell "python -m evals.run --pipeline"   # full run against live pages
```

Expected pipeline outcome (the demo depends on it):
- `nys-hire-a-veteran / vet-ramos`: `unverified`, **12600** (15% x 84,000, under 15,000 full-time cap; 2,010 h >= 1,820)
- `nys-hire-a-veteran / vet-okafor`: `not_eligible` (988 h < 1,040 part-time floor, 5% miss)
- `nys-hire-a-veteran-2020 / *`: `not_eligible`, reason "superseded by nys-hire-a-veteran"; its sources `superseded_by` set
- `nys-additional-restaurant-rtw`: `not_eligible` (expired: only tax years including 2022-12-31), or skipped by the planner
- No `uncited` claim on the current program. If one appears, read which quote failed the literal-substring check: usually PDF
  text-extraction whitespace or ligatures. Fix normalization in `steps/fetch._norm`, never by relaxing to fuzzy matching.

If the value isn't 12,600, inspect the claim's `agent_inputs.code` (the generated program) before changing prompts.

## 5. ElevenLabs end-to-end (user does UI, you verify)

The user configures the agent per `ELEVENLABS.md` using the URLs and tool secret from step 2. Then:
1. On the board, click Send to CPA on vet-ramos; open `/cpa?claim=...`; confirm it shows the code and inputs, not 12,600.
2. User speaks the code and "twelve thousand six hundred". The board should flip `sent` to `ready` via the LIVE stream.
3. Approve. Then try the hedge case and the wrong-code case live.
4. After the call, confirm a `transcript` row exists and `transcript_parse` landed on the claim (post-call webhook + HMAC).

## Constraints (don't regress these)

- The voice agent never receives `agent_value`. Don't add it to dynamic variables or the `/cpa` page.
- `ready`/`approved` are enforced by the SurrealDB schema (`fn::matches`). Don't move the gate into Python.
- Citations count only if the quote is a literal substring of the fetched text.
- Children: `env_vars={}`, `ephemeral=True`, `network_block_all=True` (unless the smoke test forces dropping the last one).
- Push to `claude/vibrant-hopper-fz3g9w` only. No PR unless asked.

## Open items after green

In priority order. Stop at 2:30 PM ET for demo recording regardless.
1. Arize: upload the eval table as an experiment (needs the `arize` SDK, not just `arize-otel`). Verify the API before writing code.
2. Anchor as provenance: `tools.screenshot_webpage` of each cited page, stored on the `source` record and shown on the board.
3. Recompute after a mismatch currently uses the CPA's figure statement, not their stated reason. Use the post-call transcript.
4. Twilio outbound call (stretch only).

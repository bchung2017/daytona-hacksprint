# Session handoff: finish verify, run pipeline, ElevenLabs E2E

Seed for a new Claude Code cloud session. Keys are provided as environment variables; never print or commit their values.
Event: Daytona HackSprint NYC, 2026-10-16. Submit by 3:20 PM ET (deadline 3:30). Stop for demo recording at 2:30 PM ET.
Read `docs/tech-spec.md` for the design.

## State at handoff (1:20 PM ET)

- Daytona org is now **tier 3**. Sandboxes created before the upgrade kept the restricted egress policy (and
  `update_network_settings` can't lift it), so the parent was recreated after the upgrade.
- Parent sandbox `incentive-parent`, id `cb4f1e42-876e-4372-92b5-5c78f9019de3`, deployed: surreal 3.3.2 on 127.0.0.1:8001
  (`db` session), Flask on 8000 (`web` session). `deploy.py` reattaches by name, so no recreate needed.
- Preview: `https://8000-cb4f1e42-876e-4372-92b5-5c78f9019de3.daytonaproxy01.net`
  - Board `/`, tool URL `/api/cpa_tool`, post-call webhook `/api/elevenlabs/postcall`
- Netcheck inside the parent: all hosts pass (Anchor, surrealdb download, Surreal Cloud memory, tax.ny.gov).
- Smoke, previous session, without the Anthropic and Arize keys:

| Check | Result |
| --- | --- |
| anthropic plan / fetch model | FAIL: no key (expected; rerun now) |
| child sandbox | ok, `cold=0.7s NET_BLOCKED env_keys=[]`. No need for `COMPUTE_REUSE_CHILD` |
| anchor fetch_webpage | ok, 25518 chars |
| pdf fetch | ok, 11359 chars |
| surrealdb write/read/LIVE + gate | ok, `live_events=4 gate_refused_mismatch=True` |
| agent memory | ok, `/me ok, fact write 200` (context id verified with the key) |
| elevenlabs key | ok, agent id unset at the time |
| public preview link | ok, `healthz=200 tool(bad code)=403`, JSON even without the skip header |
| arize span export | SKIP: keys unset (rerun now) |

## Keys expected in this session

Set: `DAYTONA_API_KEY`, `APP_ANTHROPIC_API_KEY`, `ANCHOR_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`,
`ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `MEMORY_URL`, `MEMORY_CONTEXT`, `MEMORY_API_KEY`.

- `ANTHROPIC_API_KEY` is reserved by the Claude Code host, so it's supplied as `APP_ANTHROPIC_API_KEY`; `deploy.py` maps
  `APP_<KEY>` to `<KEY>` in the parent's `.env`. Both unset: stop and tell the user.
- `CPA_TOOL_SECRET` stays unset: `deploy.py` derives it from `DAYTONA_API_KEY` (stable across sessions).
- `ELEVENLABS_AGENT_ID` may still be unset if the user hasn't created the agent; continue and redeploy once it's added.

Known values (not secrets): `MEMORY_URL=https://srv1.spectron.aws-usw2.surreal.cloud`, `MEMORY_CONTEXT=06gkb45hjls378qmh47t25po9k`.

## 0. Orient (2 min)

```bash
cd /home/user/daytona-hacksprint
git fetch origin claude/vibrant-hopper-fz3g9w && git checkout claude/vibrant-hopper-fz3g9w && git pull
for k in DAYTONA_API_KEY ANTHROPIC_API_KEY APP_ANTHROPIC_API_KEY ANCHOR_API_KEY ELEVENLABS_API_KEY ELEVENLABS_AGENT_ID \
         CPA_TOOL_SECRET ARIZE_SPACE_ID ARIZE_API_KEY MEMORY_URL MEMORY_CONTEXT MEMORY_API_KEY; do
  [ -n "${!k}" ] && echo "set   $k" || echo "unset $k"; done
pip install -q daytona==0.224.1 python-dotenv
rm -f .env   # always regenerate: a stale .env would ship old (missing) keys to the parent
```

Report set/unset by name only.

## 1. Redeploy with the new keys (3 min)

```bash
python deploy.py                       # reattaches incentive-parent, uploads the fresh .env, restarts web (db stays up)
python deploy.py --print-tool-secret   # value for the ElevenLabs X-Tool-Secret header (give to the user, don't commit)
```

Confirm the printed preview URL still matches the one above. If `incentive-parent` is not found, `deploy.py` creates a new
one (tier 3, open egress); the preview URL then changes and the user must update the ElevenLabs tool and webhook URLs.

## 2. Smoke (5 min)

```bash
python deploy.py --smoke
```

Only the previously failing/skipped rows need attention:

| Check | Pass looks like | If it fails |
| --- | --- | --- |
| anthropic plan model | `claude-fable-5-1 stop=end_turn` | 403/404: set `PLAN_MODEL=claude-opus-5-5` in the env and redeploy |
| anthropic fetch model | `claude-opus-5-5 stop=end_turn` | Key problem; report |
| elevenlabs key | `user ok, agent 200` | Agent id wrong or not created yet |
| arize span export | `flushed=True` | Check the trace `smoke.parent` in Arize (project `incentive-agent`) with the user |

Any regression on a row that passed before: `python deploy.py --logs`.

## 3. Evals, then the real pipeline (10 min)

```bash
python deploy.py --shell "python -m evals.run"              # 4 voice cases + 3 invariants, deterministic, no LLM
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
`--shell` has a 300 s timeout; if the pipeline run exceeds it, run it as a session command and tail its log.

## 4. ElevenLabs end-to-end (user does UI, you verify)

The user configures the agent per `ELEVENLABS.md` using the URLs and tool secret above. Then:
1. On the board, click Send to CPA on vet-ramos; open `/cpa?claim=...`; confirm it shows the code and inputs, not 12,600.
2. User speaks the code and "twelve thousand six hundred". The board should flip `sent` to `ready` via the LIVE stream.
3. Approve. Then try the hedge case and the wrong-code case live.
4. After the call, confirm a `transcript` row exists and `transcript_parse` landed on the claim (post-call webhook + HMAC).

## Constraints (don't regress these)

- The voice agent never receives `agent_value`. Don't add it to dynamic variables or the `/cpa` page.
- `ready`/`approved` are enforced by the SurrealDB schema (`fn::matches`). Don't move the gate into Python.
- Citations count only if the quote is a literal substring of the fetched text.
- Children: `env_vars={}`, `ephemeral=True`, `network_block_all=True`. Tier 3 gives sandboxes open egress by default, so the
  child's `NET_BLOCKED` depends on that flag; don't drop it.
- Don't delete sandboxes without asking the user.
- Push to `claude/vibrant-hopper-fz3g9w` only. No PR unless asked.

## Open items after green

In priority order. Stop at 2:30 PM ET for demo recording regardless.
1. Arize: upload the eval table as an experiment (needs the `arize` SDK, not just `arize-otel`). Verify the API before writing code.
2. Anchor as provenance: `tools.screenshot_webpage` of each cited page, stored on the `source` record and shown on the board.
3. Recompute after a mismatch currently uses the CPA's figure statement, not their stated reason. Use the post-call transcript.
4. Twilio outbound call (stretch only).

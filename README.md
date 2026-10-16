# TaxBeagle (Incentive Agent)

VIDEO: https://www.loom.com/share/231298a3e0104b649b7e4d4d06045537?utm_medium=gif

GITHUB: https://github.com/bchung2017/daytona-hacksprint

Inspiration

Small businesses leave real money on the table every year because tax credits are buried in dense state PDFs, each one has its own math, and nobody trusts an AI to file a number. Hudson Forge, our seed company, has hired veterans since 2019 and never claimed the NY Hire a Veteran Credit. That's $12,600 a year, gone. We wanted an agent that could find it, prove it, and still never be the one who signs.

What it does

TaxBeagle sniffs out tax credits for a company, cites every eligibility fact word for word from the official government source, computes the credit in a sealed sandbox, calls a human CPA over voice to get an independent figure, and only unlocks approval when the two numbers match. A human clicks the final button. The agent never sees the CPA's worksheet, and the CPA never sees the agent's number.

How we built it

Daytona: a persistent parent sandbox runs the app. Every credit computation spawns an ephemeral child sandbox with no network, no keys, and nothing but the quoted facts and the employee's inputs. Run, read the number, destroy.
Anchor: fetches the real tax.ny.gov pages through a cloud browser. A citation only counts if the quote is a literal substring of the fetched text. No paraphrase, no memory.
SurrealDB: holds claims, sources, and the gate itself. A schema assertion makes it impossible for a claim to become ready or approved unless the CPA's figure matches ours within tolerance. Python can't override it. Live queries push every state change to the board.
ElevenLabs: the voice agent calls the CPA, verifies a one-time code, and records the CPA's figure only if they state it plainly and confirm on read-back. The post-call webhook delivers the transcript, which we cross-check against the tool call.
Claude: plans which credits to check, extracts quoted facts, and writes the per-credit program.
Arize: traces the whole pipeline and the evals.

Challenges we ran into

The planner invented a criterion the source never mentions, and the citation gate blocked the whole claim. Fix: an empty quote means "the source doesn't address this," not "fabricated."
Anchor's rendered HTML had an attribute that changed between fetches, so every run "superseded" the previous source version. We version on tag-stripped visible text now.
Daytona sandboxes created before our tier upgrade kept a restricted egress policy. We had to recreate the parent.
A real CPA call is slow and nondeterministic. The demo board plays a scripted run, with one real card wired to the live ElevenLabs call.

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

# Post-hacksprint notes

Written after the hacksprint. One thing didn't make it onto the project:

<img width="1595" height="642" alt="image" src="https://github.com/user-attachments/assets/e0e85589-da7f-4c04-b66f-b01b6ec21847" />

Small woopsie on our end. Arize tracing was fully wired across the pipeline and our checks said it was exporting, but the dashboard came up empty. Turns out we pointed the HTTP exporter at the gRPC address, which happily returns 200 and quietly drops everything. We caught it late and chose not to redeploy inside the time limit. One-line fix, and the next run should be traced properly instead of empty data points.

## Arize tracing exported nothing

TaxBeagle instruments the whole pipeline with OpenTelemetry using Arize's OpenInference conventions:

| Span | Kind | Where |
| --- | --- | --- |
| `run` | AGENT | `agent.py` |
| `plan`, `fetch.extract`, `evaluate`, `compute.generate`, `store.*` | CHAIN | `steps/` |
| `fetch.page` (Anchor), `compute.child_run` (Daytona child), `voice.tool_call` (ElevenLabs) | TOOL | `steps/` |
| `eval.*` with a `passed` attribute | EVALUATOR | `evals/run.py` |


# ElevenLabs CPA agent setup

Manual setup in the ElevenLabs Agents UI (about 10 minutes). The SDK can create agents programmatically,
but its config models are large and unverified here, so clicking through is faster and safer today.

## Agent settings

- **Name:** Incentive CPA Verifier
- **Security tab:** the widget needs a public agent with authentication disabled. Add the parent's preview host
  (`8000-<sandbox-id>.<daytona proxy domain>`) to the **Allowlist**.
- **Dynamic variables** the widget passes: `claim_id`, `program_name`, `company_name`, `subject`.
  The agent's value is deliberately not among them: the voice agent never knows it, so it cannot anchor the CPA.
- **First message:** `Hi, I'm verifying a tax credit claim for {{company_name}}. Could you read me the six-digit code on your screen?`

## System prompt

```
You are a verification agent calling a CPA to get an independent figure for one tax credit claim.

Claim: {{program_name}} for {{company_name}}, subject: {{subject}}. Claim ID: {{claim_id}}.

Steps, in order:
1. Ask the CPA to read the six-digit verification code shown on their screen.
2. Briefly state which claim this is (program and subject). You do not know the computed amount and must not guess one or suggest a range.
3. Ask for their independently computed credit amount in dollars.
4. Read the figure back digit by digit and ask for an explicit yes or no.
5. Call record_cpa_value with the code, the figure, whether they explicitly said yes, and their exact words when stating the figure.
6. Say the tool's result to the CPA. If it says the figures differ, ask what input they believe differs, repeat their answer back, and end the call. Do not call record_cpa_value again.

Rules:
- Never state, hint at, or confirm any amount other than the one the CPA gave.
- If the CPA hedges ("about", "roughly", "I think"), ask once for an exact figure. If they still hedge, record what they said with confirmed=false.
- If the code is rejected, ask them to read it again once. If it fails again, end the call.
- You cannot approve, submit, or change anything other than recording the CPA's figure.
```

## Server tool: `record_cpa_value`

- **Type:** Webhook, **Method:** POST, **URL:** `{PUBLIC_BASE_URL}/api/cpa_tool`
- **Headers:**
  - `X-Tool-Secret`: type **Secret**, value = `CPA_TOOL_SECRET` from `.env`
  - `X-Daytona-Skip-Preview-Warning`: `true` (otherwise Daytona may serve its preview interstitial HTML instead of proxying)
- **Content type:** JSON
- **Description:** `Record the CPA's independently computed credit amount for the claim being verified. Call exactly once, after reading the figure back.`
- **Body parameters:**

| name | type | value type | description |
| --- | --- | --- | --- |
| `claim_id` | string | dynamic variable `claim_id` | Claim being verified |
| `code` | string | LLM prompt | The six-digit code the CPA read aloud, digits only |
| `value` | number | LLM prompt | The dollar figure the CPA stated, digits only, no symbols |
| `confirmed` | boolean | LLM prompt | True only if the CPA explicitly said yes to the read-back |
| `cpa_statement` | string | LLM prompt | The CPA's exact words when stating the figure, verbatim |

The handler enforces every rule independently of this prompt (`steps/voice.py`): wrong code returns 403 and
changes nothing, hedge words or `confirmed=false` set `needs-info`, a value outside tolerance sets `rejected`
and spawns a superseding recompute, and the DB schema refuses `ready`/`approved` without two matching values.

## Post-call webhook (transcript cross-check)

Workspace settings, then Webhooks: add `{PUBLIC_BASE_URL}/api/elevenlabs/postcall` for `post_call_transcription`.
Copy the HMAC secret into `.env` as `ELEVENLABS_WEBHOOK_SECRET`. The app verifies the `elevenlabs-signature`
header with the SDK's `construct_event`, stores the transcript, and has the plan model re-parse it; disagreement
with the tool call drops the claim to `needs-info`.

## Optional: evaluation criteria and data collection

Agent, then Analysis: add data collection `cpa_value` (number) and evaluation criteria `figure_read_back`
and `no_agent_value_disclosed`. Results land in the post-call payload under `analysis`; they're stored with
the transcript but not trusted for the gate.

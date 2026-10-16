import anthropic

client = anthropic.Anthropic()


class LLMError(Exception):
    pass


def parse(model, system, user, schema, effort="medium", max_tokens=16000):
    """Structured output into a pydantic model. One retry on refusal or unparseable output, then raise."""
    last = None
    for _ in range(2):
        r = client.beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
            fallbacks="default",
            betas=["server-side-fallback-2026-07-01"],
        )
        if r.stop_reason == "refusal":
            last = f"refusal: {r.stop_details}"
            continue
        if r.parsed_output is not None:
            return r.parsed_output
        last = f"unparseable output (stop_reason={r.stop_reason})"
    raise LLMError(last)

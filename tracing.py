"""Import before anything that imports anthropic. No-op when Arize creds are unset."""
import os
from contextlib import contextmanager

from opentelemetry import trace

import config  # noqa: F401  (loads .env)

if os.getenv("ARIZE_SPACE_ID") and os.getenv("ARIZE_API_KEY"):
    from arize.otel import Endpoint, Transport, register
    from openinference.instrumentation.anthropic import AnthropicInstrumentor

    # HTTP rather than the default gRPC: friendlier to egress proxies.
    _provider = register(
        space_id=os.environ["ARIZE_SPACE_ID"],
        api_key=os.environ["ARIZE_API_KEY"],
        project_name=os.getenv("ARIZE_PROJECT", "incentive-agent"),
        endpoint=Endpoint.ARIZE,
        transport=Transport.HTTP,
        verbose=False,
    )
    AnthropicInstrumentor().instrument(tracer_provider=_provider)

tracer = trace.get_tracer("incentive-agent")


@contextmanager
def span(name, kind="CHAIN", **attrs):
    with tracer.start_as_current_span(name) as s:
        s.set_attribute("openinference.span.kind", kind)
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s

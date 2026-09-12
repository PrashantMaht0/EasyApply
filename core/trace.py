"""One event stream, two consumers. The live log and the trace attributes come from here."""

import queue
from contextlib import contextmanager

_DONE = object()
_tracer = None
_checked = False


def init_tracing():
    """ADOT installs the tracer provider in the AgentCore container, so we only borrow it."""
    global _tracer, _checked
    if _checked:
        return _tracer
    _checked = True
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    # without a real SDK provider nothing is collecting, so our spans stay off rather than buffer
    if not hasattr(provider, "add_span_processor"):
        return None
    _tracer = trace.get_tracer("easeapply")
    return _tracer


class RunTrace:
    """One span per run, kept current so every stage span becomes a child of it.

    Without this each stage is its own root trace and a collector shows twenty unrelated
    entries instead of one run you can open.
    """

    def __init__(self, run_id: str, kind: str = "full"):
        self._span = None
        self._token = None
        tracer = init_tracing()
        if tracer is None:
            return
        from opentelemetry import context, trace

        self._context = context
        self._span = tracer.start_span(f"easeapply run {run_id}")
        self._span.set_attribute("easeapply.run_id", run_id)
        self._span.set_attribute("easeapply.kind", kind)
        self._span.set_attribute("session.id", run_id)
        self._token = context.attach(trace.set_span_in_context(self._span))

    def finish(self, **attributes) -> None:
        if self._span is None:
            return
        for key, value in attributes.items():
            self._span.set_attribute(f"easeapply.{key}", value)
        self._context.detach(self._token)
        self._span.end()


@contextmanager
def stage_span(stage: str, run_id: str | None = None, **attributes):
    """Adds the funnel to the default Strands spans, which only show that agents ran."""
    tracer = init_tracing()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(stage) as span:
        span.set_attribute("easeapply.stage", stage)
        if run_id:
            # session.id is what Phoenix groups a run by, so one run reads as one session
            span.set_attribute("session.id", run_id)
            span.set_attribute("easeapply.run_id", run_id)
        for key, value in attributes.items():
            span.set_attribute(f"easeapply.{key}", value)
        yield span


class EventBus:
    """In process queue. The pipeline writes, the Gradio generator drains."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self.funnel: dict = {}
        self.run_id: str | None = None

    def emit(self, message: str, stage: str | None = None, **attributes) -> None:
        if attributes:
            self.funnel.update(attributes)
        with stage_span(stage or "event", run_id=self.run_id, **attributes):
            pass
        self._queue.put({"message": message, "stage": stage, **attributes})

    def close(self) -> None:
        self._queue.put(_DONE)

    def stream(self):
        """Yields events until the producer closes the stream."""
        while True:
            event = self._queue.get()
            if event is _DONE:
                return
            yield event

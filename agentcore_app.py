"""AgentCore Runtime entrypoint. Three actions over the same deterministic pipeline."""

import asyncio

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from core.store import (connect, init_db, load_tailoring_inputs, pull_blackboard,
                        push_blackboard)

app = BedrockAgentCoreApp()
log = app.logger


def _run(payload: dict):
    """A full discovery run. Yields the same events the live log shows locally."""
    from pipeline import run_full

    events: asyncio.Queue = asyncio.Queue()

    async def drive():
        try:
            result = await run_full(
                desired_role=payload["desired_role"], level=payload.get("level", "mid"),
                work_mode=payload.get("work_mode", "any"), location=payload.get("location", ""),
                extra_context=payload.get("extra_context", ""),
                resume_text=payload["resume_text"],
                # stage and attributes travel too, so the dashboard funnel fills as it does locally
                emit=lambda message, stage=None, **attrs: events.put_nowait(
                    {"log": message, "stage": stage, "attrs": attrs}),
                run_id=payload.get("run_id"),
            )
            await events.put({"result": result})
        except Exception as exc:
            await events.put({"error": str(exc)})
        finally:
            await events.put(None)

    return events, drive


def _tailor(payload: dict) -> dict:
    """A5 for one posting, using the claims the gate already verified."""
    from agents.tailor import tailor_rewrites

    conn = connect()
    inputs = load_tailoring_inputs(conn, payload["posting_id"])
    conn.close()
    if inputs is None:
        return {"error": "that posting is not in the blackboard"}
    row, matched, missing, profile = inputs
    out = tailor_rewrites(row["title"], row["description"], profile.resume_text, matched, missing)
    if not out:
        return {"error": "the tailoring agent failed the schema guard twice"}
    # the title rides along because the dashboard has no blackboard of its own to look it up in
    return {**out, "title": row["title"]}


@app.entrypoint
async def invoke(payload, context):
    """run streams progress, tailor and digest return once. The blackboard lives in S3."""
    if not isinstance(payload, dict):
        yield {"error": "payload must be a JSON object"}
        return
    action = payload.get("action", "run")
    if action not in ("run", "tailor", "digest"):
        yield {"error": f"unknown action {action!r}, expected run, tailor or digest"}
        return

    log.info("easeapply action=%s session=%s", action, getattr(context, "session_id", "none"))
    pull_blackboard()
    # an empty bucket leaves no file behind, so every action needs the schema before it queries
    init_db()
    try:
        if action == "tailor":
            yield _tailor(payload)
            return
        if action == "digest":
            from digest import run_digest

            out = await run_digest(log=lambda message: log.info(message))
            yield {k: v for k, v in out.items() if k != "html"}
            return

        # the run is driven on its own task so the /ping health check keeps answering
        events, drive = _run(payload)
        task = asyncio.create_task(drive())
        while True:
            event = await events.get()
            if event is None:
                break
            yield event
        await task
    finally:
        push_blackboard()


if __name__ == "__main__":
    app.run()

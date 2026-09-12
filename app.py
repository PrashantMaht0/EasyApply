"""Gradio dashboard. Intake, streamed funnel, verified results, evidence and tailoring."""

import asyncio
import html
import json
import os
import re
import sys
import threading
import time
import uuid

# --demo replays a stored run, so the database is pointed at the fixture before anything loads it
# a hosted demo has no command line, so the flag can also arrive as an environment variable
DEMO = "--demo" in sys.argv or os.getenv("EASEAPPLY_DEMO") == "1"
if DEMO:
    os.environ["EASEAPPLY_DB"] = "data/demo.db"

import gradio as gr
from dotenv import load_dotenv

from agents.tailor import tailor_rewrites
from core.filters import LEVELS
from core.store import company_names, connect, cumulative_spend, init_db, load_tailoring_inputs
from core.trace import EventBus
from pipeline import PROXIMITY, extract_resume_text, replay_latest, run_full
from sources.slugs import load_seed

# .env is read here so the UI picks up the same settings as a CLI or cron run
load_dotenv()

WORK_MODES = ["any", "remote", "hybrid", "onsite"]
HEADERS = ["fit", "verified", "company", "role", "location", "mode"]
# the role cell carries the link and the new and gem badges, so it renders as markdown
DATATYPES = ["number", "str", "str", "markdown", "str", "str"]
# set once the runtime is deployed, and the dashboard then calls it instead of running in process
RUNTIME_ARN = os.getenv("EASEAPPLY_RUNTIME_ARN", "")

CSS = """
.metric-row{display:flex;flex-wrap:wrap;gap:8px}
.metric{flex:1 1 150px;border:1px solid var(--border-color-primary);border-radius:8px;padding:8px 10px}
.metric b{display:block;font-size:20px;line-height:1.2}
.metric span{font-size:11px;opacity:.7}
.rw{border:1px solid var(--border-color-primary);border-radius:8px;padding:10px;margin-bottom:10px}
.rw .old{opacity:.55;text-decoration:line-through}
.rw .new{font-weight:600;margin:4px 0}
.rw .tgt{font-size:12px;opacity:.75}
.rw .warn{color:#92400e;background:#fef3c7;padding:2px 6px;border-radius:4px;font-size:12px}
"""

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|<>])")


def _md(text: str) -> str:
    """Posting text is third party, escaped so it cannot plant a link or an image in the panel."""
    return _MD_SPECIAL.sub(r"\\\1", text or "")


def _link(url: str) -> str:
    """Board links were allowlisted at fetch time, this also keeps them intact inside markdown."""
    return url if url.startswith(("https://", "http://")) and ">" not in url else ""


def _city_note(f: dict) -> str:
    """Says plainly when the named city has nothing, instead of quietly showing elsewhere."""
    if "city_matching" not in f:
        return ""
    here, raw, nearby = f["city_matching"], f.get("city_postings", 0), f.get("country_matching", 0)
    if here:
        return (f"<b>{here}</b> matching {'role' if here == 1 else 'roles'} in your city, out of "
                f"{raw} postings there. <b>{nearby}</b> more elsewhere in the country.")
    return (f"<b>Nothing in your city matches this role.</b> Its {raw} "
            f"{'posting' if raw == 1 else 'postings'} are for other kinds of work. Showing "
            f"{nearby} from elsewhere in the country and the wider region, closest first.")


def _metrics(f: dict) -> str:
    """Four headline numbers. The engineering detail lives on the Diagnostics tab."""
    total = f.get("claims_proposed", 0)
    rate = f"{f.get('claims_verified', 0) / total:.0%}" if total else "n/a"
    cards = [
        ("postings scanned", f.get("postings_after_dedupe", 0)),
        ("matching roles", f.get("after_hard_filter", 0)),
        ("claims verified", rate),
        ("new since last run", f.get("new_since_last_run", 0)),
    ]
    body = "".join(f'<div class="metric"><b>{v}</b><span>{k}</span></div>' for k, v in cards)
    note = _city_note(f)
    note_html = f'<p style="margin:8px 0 0">{note}</p>' if note else ""
    return f'<div class="metric-row">{body}</div>{note_html}'


def _spend_note() -> str:
    init_db()
    conn = connect()
    spend, runs = cumulative_spend(conn)
    conn.close()
    return f"Cumulative spend across {runs} runs: ${spend:.4f}"


def _display_names() -> dict[str, str]:
    """Company names for board slugs, from the seed file and every board a run resolved."""
    names = {entry["slug"]: entry.get("name", entry["slug"]) for entry in load_seed().values()}
    conn = connect()
    names.update(company_names(conn))
    conn.close()
    return names


def _locations(r: dict) -> str:
    """One row covers every location the same role is posted in."""
    places = r.get("locations") or ([r["location"]] if r.get("location") else [])
    if not places:
        return "not stated"
    text = ", ".join(places) if len(places) <= 2 else f"{places[0]} +{len(places) - 1} more"
    # boards pack several cities into one location string, so cap the cell width
    return text if len(text) <= 60 else text[:57] + "..."


def _table(results: list[dict]) -> list[list]:
    """Rows arrive already sorted by location proximity, then fit."""
    names = _display_names()
    rows = []
    for r in results:
        url = _link(r["url"])
        role = f"[{_md(r['title'])}](<{url}>)" if url else _md(r["title"])
        badges = "".join(f" `{label}`" for label, on in
                         (("new", r.get("new")), ("gem", r["hidden_gem"])) if on)
        rows.append([r["fit_score"], f"{r['verified_ratio']:.0%}",
                     names.get(r["company"], r["company"]), role + badges, _locations(r),
                     r.get("work_mode", "not stated")])
    return rows


def _gaps_markdown(gaps: dict) -> str:
    lines = "\n".join(
        f"- **{_md(g.get('skill', ''))}** asked for by {g.get('employers')} "
        f"{'employer' if g.get('employers') == 1 else 'employers'}. {_md(g.get('note', ''))}"
        for g in (gaps or {}).get("gaps", [])
    )
    out = f"### Gaps across the shortlist\n{lines or 'No gaps across the stronger matches.'}"
    advice = (gaps or {}).get("advice")
    return out + (f"\n\n{_md(advice)}" if advice else "")


def _diagnostics(f: dict) -> str:
    reached, failed = f.get("boards_reached", 0), f.get("boards_failed", 0)
    rows = [
        ("companies proposed", f.get("slug_proposed", 0)),
        ("boards searched", f"{f.get('slug_searched', reached + failed)} "
                            f"({f.get('slug_carried_over', 0)} carried over)"),
        ("boards reachable", f"{reached} of {reached + failed}"),
        ("postings after dedupe", f.get("postings_after_dedupe", 0)),
        ("not on any aggregator", f.get("unsyndicated", 0)),
        ("after hard filter", f.get("after_hard_filter", 0)),
        *[(f"dropped on {k[8:]}", v) for k, v in f.items() if k.startswith("dropped_")],
        ("batches scored", f"{f.get('batches_done', 0)} of {f.get('batches_total', 0)}"),
        ("fabricated IDs rejected", f.get("unknown_ids_dropped", 0)),
        ("claims verified", f"{f.get('claims_verified', 0)} of {f.get('claims_proposed', 0)}"),
        ("scores clamped", f.get("scores_clamped", 0)),
        ("tokens", f.get("tokens", 0)),
        ("run cost", f"${f.get('cost_usd', 0.0):.4f}"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    agents = f.get("agents") or {}
    agent_rows = "\n".join(f"| {name} | {a['calls']} | {a['tokens']} | {a['seconds']:.1f}s |"
                           for name, a in sorted(agents.items()))
    agent_table = (f"\n### Per agent\n| agent | calls | tokens | time |\n|---|---|---|---|\n"
                   f"{agent_rows}\n" if agents else "")
    prompts = ", ".join(f"{k} v{v}" for k, v in (f.get("prompt_versions") or {}).items())
    return (f"### Run diagnostics\n{_spend_note()}\n\n| metric | value |\n|---|---|\n{table}\n"
            f"{agent_table}\n**Prompt versions**: {prompts or 'not recorded'}\n")


def _visible(results):
    rows = list(results or [])
    return _table(rows), rows


def _demo_replay():
    """Streams a stored run back with no network call and no credentials of any kind."""
    result = replay_latest()
    funnel = result["funnel"]
    steps = [
        f"replaying stored run {result['run_id']}",
        f"{funnel.get('slug_searched', funnel.get('slug_resolved', 0))} boards searched",
        f"{funnel.get('postings_after_dedupe', 0)} postings after dedupe",
        f"{funnel.get('after_hard_filter', 0)} survived the hard filter",
        f"{funnel.get('claims_verified', 0)} of {funnel.get('claims_proposed', 0)} claims verified",
        f"{len(result['results'])} scored matches",
    ]
    log = []
    for step in steps:
        log.append(step)
        yield "\n".join(log), _metrics(funnel), [], "", "", [], ""
        time.sleep(0.4)
    table, _ = _visible(result["results"])
    yield ("\n".join(log), _metrics(funnel), table, _md(result["rationale"]),
           _gaps_markdown(result["gaps"]), result["results"], _diagnostics(funnel))


def _remote_stream(payload: dict):
    """One SigV4 call to the deployed runtime. Each SSE frame carries one JSON object."""
    import boto3
    from botocore.config import Config

    # a daily run answers only when the pipeline finishes, well past the 60 second default, and a
    # retry would invoke it twice and email twice
    client = boto3.client("bedrock-agentcore", region_name=os.getenv("AWS_REGION") or "us-east-1",
                          config=Config(read_timeout=900, connect_timeout=15,
                                        retries={"max_attempts": 0}))
    response = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN, contentType="application/json",
        accept="text/event-stream",
        # the API demands at least 33 characters, so a bare uuid hex is one short
        runtimeSessionId=f"easeapply-{uuid.uuid4().hex}",
        payload=json.dumps(payload).encode(),
    )
    for line in response["response"].iter_lines():
        if line and line.startswith(b"data: "):
            yield json.loads(line[6:])


def _remote_events(payload: dict, outcome: dict):
    """Adapts a deployed run to the same event shape the local EventBus produces."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        for event in _remote_stream(payload):
            if "result" in event:
                outcome["result"] = event["result"]
            elif "error" in event:
                outcome["error"] = event["error"]
            else:
                yield {"message": event.get("log", ""), "stage": event.get("stage"),
                       **(event.get("attrs") or {})}
    except (BotoCoreError, ClientError) as exc:
        outcome["error"] = str(exc)


def _remote_tailor(posting_id: str):
    """The deployed runtime holds the blackboard, so the rewrite is fetched, not computed here."""
    from botocore.exceptions import BotoCoreError, ClientError

    out = None
    try:
        for event in _remote_stream({"action": "tailor", "posting_id": posting_id}):
            out = event
    except (BotoCoreError, ClientError) as exc:
        return str(exc), ""
    if not out:
        return "The runtime returned no response.", ""
    if "error" in out:
        return out["error"], ""
    return out, out.get("title", "the selected role")


def start_run(resume_file, desired_role, level, work_mode, location, extra_context):
    """Generator. Drains the event bus so the funnel and log fill while the run is going."""
    if DEMO:
        yield from _demo_replay()
        return
    blank = (gr.skip(),) * 6
    if not resume_file:
        yield "upload a resume PDF first", *blank
        return
    if not desired_role.strip():
        yield "enter a target role first", *blank
        return
    resume_text = extract_resume_text(resume_file)
    if not resume_text:
        yield "no text could be extracted from that PDF", *blank
        return

    outcome: dict = {}
    fields = {"desired_role": desired_role, "level": level, "work_mode": work_mode,
              "location": location, "extra_context": extra_context,
              "resume_text": resume_text, "run_id": uuid.uuid4().hex[:12]}

    if RUNTIME_ARN:
        events = _remote_events({"action": "run", **fields}, outcome)
    else:
        bus = EventBus()
        bus.run_id = fields["run_id"]

        def worker():
            try:
                outcome["result"] = asyncio.run(run_full(emit=bus.emit, **fields))
            except Exception as exc:
                outcome["error"] = str(exc)
            finally:
                bus.close()

        threading.Thread(target=worker, daemon=True).start()
        events = bus.stream()

    funnel: dict = {}
    log = []
    for event in events:
        funnel.update({k: v for k, v in event.items()
                       if k not in ("message", "stage") and v is not None})
        # one line that updates in place, instead of a line per scored batch
        if event.get("stage") == "scorer" and log and log[-1].startswith("batch "):
            log[-1] = event["message"]
        else:
            log.append(event["message"])
        yield "\n".join(log), _metrics(funnel), [], "", "", [], ""

    if "error" in outcome:
        log.append(f"run failed: {outcome['error']}")
        yield "\n".join(log), _metrics(funnel), [], "", "", [], ""
        return

    result = outcome["result"]
    table, _ = _visible(result["results"])
    yield ("\n".join(log), _metrics(result["funnel"]), table, _md(result.get("rationale", "")),
           _gaps_markdown(result.get("gaps")), result["results"],
           _diagnostics(result["funnel"]))


def email_results():
    """Sends the stored run to your inbox on demand, so the email path can be shown live."""
    if DEMO:
        return "Email is disabled in demo mode, which makes no network calls."
    try:
        if RUNTIME_ARN:
            out = None
            for event in _remote_stream({"action": "email"}):
                out = event
            if not out or "error" in (out or {}):
                return f"Could not send: {(out or {}).get('error', 'no response')}"
        else:
            from digest import resend_latest

            out = resend_latest(log=lambda message: None)
    except Exception as exc:
        return f"Could not send: {exc}"
    if not out.get("emailed"):
        return "Nothing was sent. Check EMAIL_BACKEND and SELF_EMAIL."
    return f"Sent {out['sent']} matches from run {out['run_id']} to your inbox."


def show_detail(visible, event: gr.SelectData):
    """Evidence for one row. Every quote here already passed span verification."""
    empty = ("Select a row on the Results tab.", None, "")
    if not visible or event.index is None:
        return empty
    row = event.index[0] if isinstance(event.index, (list, tuple)) else event.index
    if row >= len(visible):
        return empty
    r = visible[row]
    places = r.get("locations") or ([r["location"]] if r.get("location") else [])
    company = _display_names().get(r["company"], r["company"])
    url = _link(r["url"])
    lines = [f"## {_md(r['title'])}",
             f"{_md(company)} | {_md(', '.join(places)) or 'location not stated'} | "
             f"fit {r['fit_score']}",
             f"[open posting](<{url}>)" if url else "posting link unavailable", ""]
    if r.get("duplicates", 1) > 1:
        lines.append(f"This role is posted {r['duplicates']} times across those locations. "
                     "The link goes to the best scoring one.\n")
    if r["hidden_gem"]:
        lines.append("**Hidden Gem** posted in the last 7 days and not found on any aggregator.")
    lines.append(f"**Work mode** {r.get('work_mode', 'not stated')}")
    lines.append(f"**Reach** {PROXIMITY.get(r.get('proximity', 3), 'unknown')}")
    lines.append(f"**Verification rate** {r['verified_ratio']:.0%} of claims kept their quote.")
    lines.append(f"**Sponsorship** {r['sponsorship'].get('status', 'not_mentioned')}")
    lines.append("\n### Matched, with the quote that proves it")
    for c in r["matched"]:
        lines.append(f"- **{_md(c['skill'])}** job: \"{_md(c.get('job_span', ''))}\"")
        if c.get("resume_span"):
            lines.append(f"  resume: \"{_md(c['resume_span'])}\"")
    lines.append("\n### Missing")
    for c in r["missing"]:
        lines.append(f"- **{_md(c['skill'])}** job: \"{_md(c.get('job_span', ''))}\"")
    picked = (f"Selected **{_md(r['title'])}**. Open the Selected role tab for its evidence "
              "and tailoring.")
    return "\n".join(lines), r["posting_id"], picked


def tailor(posting_id):
    """A5 on demand, for the selected posting only."""
    if DEMO:
        return "Tailoring is disabled in demo mode, which makes no model calls."
    if not posting_id:
        return "Select a role on the Results tab first."
    if RUNTIME_ARN:
        out, title = _remote_tailor(posting_id)
        if isinstance(out, str):
            return out
    else:
        conn = connect()
        inputs = load_tailoring_inputs(conn, posting_id)
        conn.close()
        if inputs is None:
            return "That posting is not in the blackboard yet."
        row, matched, missing, profile = inputs
        title = row["title"]
        out = tailor_rewrites(row["title"], row["description"], profile.resume_text,
                              matched, missing)
    if not out:
        return "The tailoring agent failed the schema guard twice."
    if not out["rewrites"]:
        return "No rewrite could be traced back to a line in your resume, so nothing is shown."

    cards = []
    for item in out["rewrites"]:
        warnings = []
        if item["invented"]:
            warnings.append("Names " + ", ".join(item["invented"])
                            + ", which appear nowhere in your resume. Do not paste this as is.")
        if item["unsupported"]:
            warnings.append("Claims " + ", ".join(item["unsupported"])
                            + ", which the scorer recorded as a gap.")
        warn = "".join(f'<div class="warn">{html.escape(w)}</div>' for w in warnings)
        cards.append(
            f'<div class="rw"><div class="old">{html.escape(item["original"])}</div>'
            f'<div class="new">{html.escape(item["rewritten"])}</div>'
            f'<div class="tgt">Targets: "{html.escape(item["targets"])}"<br>'
            f'{html.escape(item["change"])}</div>{warn}</div>')
    note = (f"<p>{out['dropped']} rewrite(s) dropped because they did not match a line in your "
            f"resume.</p>") if out["dropped"] else ""
    return f"<h3>Tailored for {html.escape(title)}</h3>{''.join(cards)}{note}"


# injected rather than passed to Blocks, because gradio 6 moved that argument to launch
with gr.Blocks(title="EaseApply") as demo:
    gr.HTML(f"<style>{CSS}</style>")
    results_state = gr.State([])
    visible_state = gr.State([])
    selected_id = gr.State(None)
    gr.Markdown("# EaseApply\nJobs straight from company ATS boards, every claim checked against the source.\n\n"
                "Set up once here. Every morning at 07:00 the pipeline runs itself on AWS and emails you what is new.")
    if DEMO:
        gr.Markdown("**Demo mode.** Replaying a stored run. No network calls, no credentials.")

    with gr.Tabs():
        with gr.Tab("Run"):
            with gr.Row():
                with gr.Column(scale=1):
                    resume = gr.File(label="Resume PDF", file_types=[".pdf"], type="filepath")
                    role = gr.Textbox(label="Target role", placeholder="AI Engineer")
                    level = gr.Dropdown(LEVELS, value="mid", label="Experience level")
                    mode = gr.Dropdown(WORK_MODES, value="any", label="Work mode")
                    location = gr.Textbox(
                        label="Locations", placeholder="Cork, Ireland ; Galway, Ireland ; Dublin, Ireland ; London, UK",
                        info="Separate several with semicolons. Roles in a city you name rank above everything else.")
                    context = gr.Textbox(
                        label="Extra instructions", lines=3,
                        placeholder="Constraints, interests, visa situation",
                        info="Passed to the profiler and kept for every scheduled run.")
                    run = gr.Button("Replay stored run" if DEMO else "Save setup and run",
                                    variant="primary")
                with gr.Column(scale=2):
                    gr.Markdown(
                        "Finishing a run saves this setup to the cloud blackboard. The 07:00 job reuses it, "
                        "so you only come back here to change something.")
                    metrics = gr.HTML(_metrics({}))
                    log = gr.Textbox(label="Live log", lines=14, interactive=False)

        with gr.Tab("Results"):
            with gr.Row():
                rationale = gr.Markdown()
                gaps_out = gr.Markdown()
            table = gr.Dataframe(headers=HEADERS, datatype=DATATYPES, interactive=False,
                                 wrap=True, label="Matches, closest first")
            with gr.Row():
                email_btn = gr.Button("Email me these results")
                email_status = gr.Markdown()
            picked = gr.Markdown()

        with gr.Tab("Selected role"):
            detail = gr.Markdown("Select a row on the Results tab.")
            tailor_btn = gr.Button("Tailor my resume for this role", variant="primary")
            bullets = gr.HTML()

        with gr.Tab("Diagnostics"):
            diagnostics = gr.Markdown(f"### Run diagnostics\n{_spend_note()}\n\nNo run yet.")

    gr.Markdown("Syndication data from [RemoteOK](https://remoteok.com) and Arbeitnow.")

    run.click(start_run, [resume, role, level, mode, location, context],
              [log, metrics, table, rationale, gaps_out, results_state, diagnostics])
    results_state.change(_visible, [results_state], [table, visible_state])
    table.select(show_detail, [visible_state], [detail, selected_id, picked])
    email_btn.click(email_results, outputs=email_status)
    tailor_btn.click(tailor, [selected_id], [bullets])


if __name__ == "__main__":
    # localhost only, a public share link would expose resume driven results to anyone with it.
    # Uploads are resumes, so anything over a few megabytes is not one.
    # only a hosted Space needs every interface, locally this stays on loopback
    host = "0.0.0.0" if os.getenv("SPACE_ID") else None
    demo.launch(server_name=host, share=False, max_file_size="5mb")

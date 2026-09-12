"""HTML digest render and delivery. One recipient only, the user's own address."""

import html
import os
import smtplib
from email.message import EmailMessage

STYLE = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;max-width:640px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.meta{color:#666;font-size:13px;margin-bottom:20px}
.job{border:1px solid #e4e4e7;border-radius:8px;padding:14px;margin-bottom:12px}
.title{font-weight:600;font-size:15px}
.co{color:#666;font-size:13px}
.gem{background:#fef3c7;color:#92400e;font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px}
.score{float:right;font-weight:600}
.ev{font-size:13px;color:#444;margin-top:8px}
.ev q{color:#666}
.foot{color:#888;font-size:12px;border-top:1px solid #e4e4e7;padding-top:12px;margin-top:20px}
"""


def render_digest(results: list[dict], funnel: dict, run_id: str, heading: str = "") -> str:
    """Every quote shown here already passed span verification against the stored source."""
    cards = []
    for r in results:
        gem = '<span class="gem">HIDDEN GEM</span>' if r["hidden_gem"] else ""
        spots = r.get("locations") or ([r["location"]] if r.get("location") else [])
        places = ", ".join(spots[:2]) or "location not stated"
        if len(spots) > 2:
            places += f" +{len(spots) - 2} more"
        # one job sentence can back several skills, so each quote is shown only once
        seen, picks = set(), []
        for c in r["matched"]:
            span = (c.get("job_span") or "")[:160]
            if span in seen:
                continue
            seen.add(span)
            picks.append((c["skill"], span))
            if len(picks) == 3:
                break
        evidence = "".join(
            f'<div class="ev"><b>{html.escape(skill)}</b> <q>{html.escape(span)}</q></div>'
            for skill, span in picks
        )
        missing = ", ".join(html.escape(c["skill"]) for c in r["missing"][:5])
        cards.append(f"""<div class="job">
<span class="score">{r['fit_score']}</span>
<div class="title"><a href="{html.escape(r['url'])}">{html.escape(r['title'])}</a>{gem}</div>
<div class="co">{html.escape(r['company'])} | {html.escape(places)} |
 {r['verified_ratio']:.0%} of claims verified</div>
{evidence}
{f'<div class="ev">Missing: {missing}</div>' if missing else ''}
</div>""")

    claims = funnel.get("claims_proposed", 0)
    verified = funnel.get("claims_verified", 0)
    rate = f"{verified / claims:.0%}" if claims else "n/a"
    plural = "match" if len(results) == 1 else "matches"
    title = heading or f"{len(results)} new {plural} this morning"
    # a full run records new_since_last_run, a known boards digest records new_postings
    appeared = funnel.get("new_postings", funnel.get("new_since_last_run", 0))
    appeared_noun = "posting" if appeared == 1 else "postings"
    return f"""<html><head><meta charset="utf-8"><style>{STYLE}</style></head><body>
<h1>{title}</h1>
<div class="meta">{appeared} {appeared_noun} appeared since the last run.
 {verified} of {claims} claims verified ({rate}). Run {run_id}.</div>
{''.join(cards) or '<p>Nothing scored above the bar today.</p>'}
<div class="foot">EaseApply. Postings read directly from public ATS boards.
Syndication data from <a href="https://remoteok.com">RemoteOK</a> and Arbeitnow.</div>
</body></html>"""


def send_digest(html_body: str, subject: str) -> bool:
    """Returns whether an email actually went out. EMAIL_BACKEND=none renders only."""
    backend = (os.getenv("EMAIL_BACKEND") or "none").lower()
    recipient = os.getenv("SELF_EMAIL")
    if backend == "none" or not recipient:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["To"] = recipient
    message["From"] = os.getenv("SMTP_USER") or recipient
    message.set_content("This digest is HTML only.")
    message.add_alternative(html_body, subtype="html")

    if backend == "ses":
        import boto3

        boto3.client("ses", region_name=os.getenv("AWS_REGION") or "us-east-1").send_raw_email(
            Source=message["From"], Destinations=[recipient],
            RawMessage={"Data": message.as_bytes()},
        )
        return True

    host, port = os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT") or 587)
    if not host:
        return False
    with smtplib.SMTP(host, port) as server:
        server.starttls()
        password = os.getenv("SMTP_PASSWORD")
        if password:
            server.login(os.getenv("SMTP_USER") or recipient, password)
        server.send_message(message)
    return True

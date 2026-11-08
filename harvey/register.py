"""The company register: every company Harvey has found, one row each.

Built on read from the database, so nothing is lost and it is always current:
where the company came from, why it fits, what Harvey has done about it, and
— when nothing was sent — why not. Companies with no contact found stay in
the register for people to follow up another way.
"""

from __future__ import annotations

import csv
import io

from harvey.config import ComplianceConfig, live_do_not_contact
from harvey.fit import company_fit, current_roles
from harvey.gate import do_not_contact_match

COLUMNS = [
    "company", "website", "sector", "area", "fit", "why_selected",
    "status", "why_not_contacted", "contacts", "contact_form", "hiring",
    "found_via", "first_found",
]
LIVE = {"new", "queued"}
DONE = {"contacted", "replied", "meeting", "closed"}


def _contacts(prospects: list[dict]) -> str:
    out = []
    for p in prospects:
        name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or "Allgemeines Postfach"
        title = p.get("title") or ""
        email = p.get("email") or "keine E-Mail"
        status = p.get("email_status") or "ungeprüft"
        label = f"{name}" + (f" ({title})" if title and title != "General inbox" else "")
        flag = " [nicht relevant]" if p.get("status") == "lost" else ""
        out.append(f"{label}: {email} [{status}]{flag}")
    return "; ".join(out)


def company_status(company: dict, prospects: list[dict], outbox: list[dict],
                   observations: list[dict], dnc: list[str],
                   send_to_risky: bool = False) -> tuple[str, str]:
    """(status, why) for one company. ``why`` says why nothing went out, or
    what happens next. ``send_to_risky`` is channels.email.send_to_risky."""
    domain = (company.get("domain") or "").lower()
    if domain and dnc and do_not_contact_match(f"x@{domain}", dnc):
        return "Ausgeschlossen", "Steht auf deiner Sperrliste"
    listed = [p for p in prospects
              if dnc and p.get("email") and do_not_contact_match(p["email"], dnc)]
    if listed and len(listed) == len([p for p in prospects if p.get("email")]):
        return "Ausgeschlossen", "Jeder Kontakt hier steht auf deiner Sperrliste"
    prospects = [p for p in prospects if p not in listed]
    if any(p.get("status") == "opted_out" for p in prospects):
        return "Abgemeldet", "Ein Kontakt hier will keine E-Mails mehr"

    states = {o.get("status") for o in outbox}
    if "sent" in states or any(p.get("status") in DONE for p in prospects):
        replied = any(p.get("status") in {"replied", "meeting", "closed"} for p in prospects)
        return ("Hat geantwortet" if replied else "Angeschrieben"), ""
    if states & {"approved", "sending"}:
        when = min((o.get("send_at") or "" for o in outbox if o.get("status") == "approved"), default="")
        return "Freigegeben", f"Von dir freigegeben, erster Versand am {when[:10]}" if when else "Von dir freigegeben"
    if "pending_review" in states:
        return "Entwurf wartet", "E-Mail geschrieben, wartet im Postausgang auf deine Prüfung"

    # The Writer picks up only 'new' contacts. A 'queued' one has had its
    # drafts staged; with nothing pending (above), they were stopped.
    if any(p.get("status") == "queued" for p in prospects) and \
            not any(p.get("status") == "new" for p in prospects):
        if states & {"rejected", "failed", "cancelled"}:
            return "Nicht kontaktiert", "Entwürfe wurden vor dem Versand abgelehnt oder blockiert"
        return "Eingereiht", "Für eine Kampagne vorbereitet"
    if any(p.get("status") == "write_failed" for p in prospects) and \
            not any(p.get("status") in LIVE for p in prospects):
        return "Nicht kontaktiert", ("Harvey konnte nach mehreren Versuchen keinen Entwurf schreiben. "
                                     "Schau ins Log und behebe die Ursache, dann versucht es Harvey neu")
    live = [p for p in prospects if p.get("status") in LIVE]
    statuses = {(p.get("email_status") or "") for p in live}
    has_form = any(o.get("signal_code") == "CONTACT_FORM_URL" for o in observations)
    form = " Kontaktformular vorhanden." if has_form else ""
    if "verified" in statuses or (send_to_risky and "risky" in statuses):
        return "Bereit", "Zustellbare E-Mail gefunden. Harvey schreibt sie in einer der nächsten Runden an"
    if "risky" in statuses:
        return "Nicht kontaktiert", ("Nur eine riskante E-Mail (Catch-all), an die deine Regeln nicht senden. "
                                     "Versuch es per Telefon, LinkedIn oder später über die Domain für "
                                     "Kaltakquise." + form)
    if live and statuses <= {"guess", "invalid", ""}:
        who = ", ".join(sorted({(p.get("title") or "Kontakt") for p in live}))[:120]
        return "Nicht kontaktiert", (f"{who} gefunden, aber keine gültige E-Mail (Adresse geraten oder "
                                     "von der Prüfung abgelehnt). Versuch es per Telefon, LinkedIn oder "
                                     "über eine Kontaktdatenbank." + form)
    if prospects and not live:
        if states & {"rejected", "failed", "cancelled"}:
            return "Nicht kontaktiert", "Entwürfe wurden vor dem Versand abgelehnt oder blockiert"
        return "Nicht kontaktiert", "Die gefundenen Kontakte waren nicht relevant (z. B. keine Fabrik, falsche Person)"
    if company.get("prospects_checked_at"):
        return "Nicht kontaktiert", "Website gelesen, aber keine Person mit Namen und keine brauchbare E-Mail darauf." + form
    if company.get("profile_checked_at"):
        return "Noch nicht bearbeitet", "Website gelesen, Harvey hat hier noch nicht nach Kontakten gesucht." + form
    return "Noch nicht bearbeitet", "Über die Suche gefunden, Website noch nicht gelesen"


async def build_register(query_db, config, company_id: str | None = None) -> list[dict]:
    """Every company (or just ``company_id``), best fit first, as register rows."""
    one, args = ("", ()) if company_id is None else (" AND {col} = ?", (company_id,))
    companies = await query_db(
        "SELECT * FROM companies WHERE 1=1" + one.format(col="id") + " ORDER BY created_at", args)
    if not companies:
        return []
    prospects = await query_db(
        "SELECT company_id, first_name, last_name, title, email, email_status, status "
        "FROM prospects WHERE 1=1" + one.format(col="company_id"), args)
    outbox = await query_db(
        "SELECT p.company_id, o.status, o.send_at FROM outbox o JOIN prospects p ON p.id = o.prospect_id "
        "WHERE 1=1" + one.format(col="p.company_id"), args)
    observations = await query_db(
        "SELECT company_id, signal_code, value_text, value_num, observed_at FROM observations "
        "WHERE signal_code IN "
        "('DECISION_MAKER_TITLE', 'CONTACT_FORM_URL', 'HIRING_ROLE')" + one.format(col="company_id"), args)
    by_p: dict[str, list] = {}
    by_o: dict[str, list] = {}
    by_obs: dict[str, list] = {}
    for rows, by in ((prospects, by_p), (outbox, by_o), (observations, by_obs)):
        for r in rows:
            by.setdefault(r["company_id"], []).append(r)

    from harvey.leads import prospect_hidden, removed_by_company
    # What a check took away (a website of another business, a wrong person or
    # address) counts nowhere here: not in the columns, not in fit or status.
    removed = await removed_by_company(query_db, None if company_id is None else [company_id])
    dnc = _do_not_contact(config)
    channels = getattr(config, "channels", None)
    send_to_risky = bool(getattr(getattr(channels, "email", None), "send_to_risky", False))
    rows = []
    for c in companies:
        cid = c["id"]
        rm = removed.get(cid)
        site_gone = bool(rm and rm["site"])
        ps = [p for p in by_p.get(cid, []) if not prospect_hidden(p, rm, c.get("email") or "")]
        obs = [] if site_gone else by_obs.get(cid, [])
        fit, reasons = company_fit(c, ps, obs, config, dnc)
        status, why = company_status(c, ps, by_o.get(cid, []), obs, dnc, send_to_risky)
        sector, area = c.get("industry") or "", c.get("location") or ""
        found = f"Gefunden über {c['source']}" if c.get("source") else ""
        rows.append({
            # id and fit_reasons are for the dashboard; the CSV leaves them out.
            "id": cid,
            "fit_reasons": reasons,
            "company": c.get("name") or ("" if site_gone else c.get("domain") or ""),
            "website": "" if site_gone else (
                c.get("website") or (f"https://{c['domain']}" if c.get("domain") else "")),
            "sector": sector,
            "area": area,
            "fit": fit,
            "why_selected": "; ".join(x for x in (found, " · ".join(reasons)) if x),
            "status": status,
            "why_not_contacted": why,
            "contacts": _contacts(ps),
            "contact_form": next((o.get("value_text") or "" for o in obs
                                  if o.get("signal_code") == "CONTACT_FORM_URL"), ""),
            "hiring": "; ".join(dict.fromkeys(
                (o.get("value_text") or "")[:60] for o in current_roles(obs)
                if o.get("value_text")))[:300],
            "found_via": c.get("source_url") or "",
            "first_found": (c.get("created_at") or "")[:10],
        })
    rows.sort(key=lambda r: r["fit"], reverse=True)
    return rows


def _do_not_contact(config) -> list[str]:
    """The do-not-contact list as the config file says it is now, as the
    sender reads it, so an entry added while Harvey runs shows at once."""
    compliance = getattr(config, "compliance", None)
    if isinstance(compliance, ComplianceConfig):
        try:
            return list(live_do_not_contact(config) or [])
        except Exception:
            pass
    return list(getattr(compliance, "do_not_contact", None) or [])


def sqlite_query(db_path):
    """A ``query_db`` for build_register that reads ``db_path`` directly
    (the CLI has no dashboard to borrow one from)."""
    import aiosqlite

    async def query_db(sql: str, params: tuple = ()) -> list[dict]:
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                return [dict(r) for r in await cursor.fetchall()]

    return query_db


def _cell(value):
    """Text from websites must not run as a spreadsheet formula."""
    if isinstance(value, (int, float)):
        return value
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(row.get(k)) for k in COLUMNS})
    return buf.getvalue()

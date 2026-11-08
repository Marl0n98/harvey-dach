"""The lead list: every discovered company in one file, with what its own site publishes.

One row per company: name, sector, what the site says it does, address, phones and
addresses from the map and from the site, the people the Impressum or team page
names, likely addresses for them and how each address was checked. It reads sites
and asks mail servers whether a mailbox exists. It never sends an email.

Checking a mailbox: one SMTP session per domain, MAIL FROM:<> and RCPT TO for each
address, QUIT before DATA. A made-up address goes first, so a server that accepts
everything (catch-all) is recognised instead of counted as a hit. With REACHER_URL
set, a self-hosted Reacher (github.com/reacherhq/check-if-email-exists) answers
instead.
"""

from __future__ import annotations

import asyncio
import csv
import html as html_lib
import io
import json
import logging
import os
import random
import re
import socket
from datetime import date

import aiosmtplib
import httpx

from harvey.collectors import people as people_mod
from harvey.public_http import page_text
from harvey.collectors.profile import BOOKING_HINTS
from harvey.integrations.email_finder import (
    ROLE_LOCALS, _clean_name_part, build_email, classify_mx, generate_patterns,
    get_mx_host, infer_pattern_from_email, pattern_from_local,
)

logger = logging.getLogger("harvey.leads")

# Where an address comes from, strongest first.
SOURCE_MAP = "Karte"
SOURCE_SITE = "Website"
SOURCE_PATTERN = "Muster von Website"
SOURCE_GUESS = "geraten"
SOURCE_RANK = {SOURCE_MAP: 3, SOURCE_SITE: 3, SOURCE_PATTERN: 2, SOURCE_GUESS: 1}

# What the mail server said.
OK = "bestätigt"
BAD = "ungültig"
CATCH_ALL = "Catch-all"
MS_UNSURE = "angenommen (Microsoft, unsicher)"
UNKNOWN = "nicht prüfbar"
UNCHECKED = "nicht geprüft"

FREEMAIL = frozenset("""
gmail.com googlemail.com gmx.de gmx.at gmx.ch gmx.net web.de t-online.de freenet.de
yahoo.de yahoo.com outlook.com outlook.de hotmail.com hotmail.de live.de live.at
icloud.com me.com posteo.de mail.de aon.at a1.net chello.at bluewin.ch sunrise.ch
hispeed.ch gmx.li
""".split())

PERSONS_PER_COMPANY = 2
GUESSES_PER_PERSON = 4
MAX_RCPT_PER_SESSION = 10
SMTP_TIMEOUT = 15
SESSIONS_PER_MAIL_HOST = 2

_USER_UNKNOWN = re.compile(
    r"(?i)user unknown|unknown user|no such user|does not exist|doesn.t exist|not exist|"
    r"unknown recipient|recipient (?:address )?rejected|invalid recipient|mailbox unavailable|"
    r"mailbox not found|no mailbox|address rejected|not found|5\.1\.1|5\.1\.0|unrouteable|"
    r"undeliverable|existiert nicht|unbekannt")
_BLOCKED = re.compile(
    r"(?i)reverse dns|rdns|ptr|dynamic|dial-?up|residential|blocked|blacklist|blocklist|"
    r"spamhaus|banned|policy|reputation|not allowed|denied|client host")
_PHONE_TEXT = re.compile(
    r"(?i)(?:tel(?:efon)?|fon|phone|mobil|handy)\.?\s*(?:\(?[a-z ]{0,12}\)?)?\s*[:.]?\s*"
    r"(\+?\(?\d[\d ()/.\-]{6,22}\d)")
_CAREERS = re.compile(r"""(?i)href\s*=\s*["'][^"']*(?:karriere|stellen|jobs|career)[^"']*["']""")
_IMPRINT_LINK = re.compile(
    r"""(?is)<a\b[^>]*href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""")


def _text(value: str, limit: int = 300) -> str:
    # Twice: some sites escape their meta description again ("&amp;uuml;").
    value = html_lib.unescape(html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", value or "")))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def site_description(html: str) -> str:
    """What the site says the business does: its meta description, else its title."""
    for pattern in (r'<meta[^>]+name\s*=\s*["\']description["\'][^>]*content\s*=\s*["\']([^"\']+)',
                    r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]*name\s*=\s*["\']description',
                    r'<meta[^>]+property\s*=\s*["\']og:description["\'][^>]*content\s*=\s*["\']([^"\']+)'):
        m = re.search(pattern, html or "", re.I)
        if m and len(_text(m.group(1))) >= 20:
            return _text(m.group(1))
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return _text(m.group(1), 160) if m else ""


def _clean_phone(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw.replace(" ", " ")).strip(" .-/")
    return raw if len(re.sub(r"\D", "", raw)) >= 7 else ""


def phones_on_page(html: str) -> list[str]:
    """tel: links first, then numbers printed after Tel., Telefon, Fon or Phone."""
    found: list[str] = []
    for raw in re.findall(r"""(?i)href\s*=\s*["']tel:([^"']+)["']""", html or ""):
        phone = _clean_phone(html_lib.unescape(raw).replace("%20", " "))
        if phone and phone not in found:
            found.append(phone)
    for m in _PHONE_TEXT.finditer(_text(html, 200_000)):
        phone = _clean_phone(m.group(1))
        digits = re.sub(r"\D", "", phone)
        if phone and not any(re.sub(r"\D", "", f).endswith(digits[-7:]) for f in found):
            found.append(phone)
    return found[:4]


def imprint_links(html: str, base: str) -> list[str]:
    """Links to the Impressum and contact page, which German law makes carry an address."""
    out = []
    for href, label in _IMPRINT_LINK.findall(html or ""):
        if re.search(r"(?i)impressum|imprint|kontakt|contact", href + " " + label):
            url = href if href.startswith("http") else base.rstrip("/") + "/" + href.lstrip("/")
            if url not in out:
                out.append(url)
    return out[:3]


def _own(addr: str, domain: str) -> bool:
    other = addr.rpartition("@")[2].removeprefix("www.")
    return other == domain or other.endswith("." + domain)


def candidates_for(person: dict, domain: str, site_emails: list[str]) -> list[tuple[str, str]]:
    """(address, source) for one named person, most likely first.

    An address on the site that spells the person's name wins. Otherwise the way
    another person's published address is spelled gives the pattern. Otherwise
    the common patterns are guesses.
    """
    first, last = person.get("first_name", ""), person.get("last_name", "")
    lasts = [last] + ([last.split("-")[0]] if "-" in last else [])
    own = [e for e in site_emails if _own(e, domain)]
    for addr in own:
        if any(infer_pattern_from_email(addr, first, ln) for ln in lasts):
            return [(addr, SOURCE_SITE)]
    out: list[tuple[str, str]] = []
    for addr in own:
        local = addr.partition("@")[0]
        if local in ROLE_LOCALS:
            continue
        hit = pattern_from_local(local)
        if hit:
            for ln in lasts:
                email = build_email(hit[0], first, ln, domain)
                if email and email not in [e for e, _ in out]:
                    out.append((email, SOURCE_PATTERN))
    seen = {e for e, _ in out}
    for ln in lasts:
        for email in generate_patterns(first, ln, domain)[:GUESSES_PER_PERSON]:
            if email not in seen:
                seen.add(email)
                out.append((email, SOURCE_GUESS))
    return out[:GUESSES_PER_PERSON + 2]


class MailboxChecker:
    """Asks each domain's mail server whether addresses exist, one session per domain."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.reacher = os.environ.get("REACHER_URL", "").rstrip("/")
        name = socket.getfqdn()
        self.helo = os.environ.get("HARVEY_HELO") or (
            name if "." in name and not name.endswith(".arpa") else "localhost.localdomain")
        self._hosts: dict[str, asyncio.Semaphore] = {}

    async def check(self, domain: str, emails: list[str]) -> dict:
        """{"mail_host", "provider", "catch_all", "results": {email: verdict}, "note"}."""
        out = {"mail_host": "", "provider": "", "catch_all": None,
               "results": {e: UNCHECKED for e in emails}, "note": ""}
        mx = await get_mx_host(domain)
        if not mx:
            out["note"] = "kein Mailserver"
            out["results"] = {e: BAD for e in emails}
            return out
        out["mail_host"], out["provider"] = mx, classify_mx(mx)
        if not self.enabled or not emails:
            return out
        if self.reacher:
            for email in emails:
                out["results"][email] = await self._reacher(email)
            return out
        gate = self._hosts.setdefault(mx, asyncio.Semaphore(SESSIONS_PER_MAIL_HOST))
        async with gate:
            await self._smtp(mx, domain, emails[:MAX_RCPT_PER_SESSION], out)
        return out

    @staticmethod
    async def _ask(call) -> tuple[int, str]:
        """aiosmtplib raises on a refusal; a refusal is the answer we are after."""
        try:
            reply = await call
        except (aiosmtplib.SMTPRecipientRefused, aiosmtplib.SMTPSenderRefused,
                aiosmtplib.SMTPResponseException) as e:
            return e.code, str(e.message)
        return reply.code, str(reply.message)

    async def _smtp(self, mx: str, domain: str, emails: list[str], out: dict):
        smtp = aiosmtplib.SMTP(hostname=mx, port=25, timeout=SMTP_TIMEOUT,
                               local_hostname=self.helo, start_tls=False)
        try:
            await smtp.connect()
            await smtp.ehlo()
            code, msg = await self._ask(smtp.mail(""))
            if code >= 400:
                out["note"] = ("Server blockt Prüfungen von diesem Anschluss" if _BLOCKED.search(msg or "")
                               else f"Absender abgelehnt ({code})")
                out["results"] = {e: UNKNOWN for e in emails}
                return
            fake = f"keinpostfach{random.randint(100000, 999999)}@{domain}"
            code, msg = await self._ask(smtp.rcpt(fake))
            if code < 300:
                out["catch_all"] = True
            elif 500 <= code < 600 and _USER_UNKNOWN.search(msg or ""):
                out["catch_all"] = False
            else:
                # The server will not say no to a made-up address either: its
                # answers about real ones mean nothing.
                out["note"] = ("Server blockt Prüfungen von diesem Anschluss" if _BLOCKED.search(msg or "")
                               else f"Server antwortet nicht eindeutig ({code})")
                out["results"] = {e: UNKNOWN for e in emails}
                return
            for email in emails:
                await asyncio.sleep(0.3)
                code, msg = await self._ask(smtp.rcpt(email))
                if code < 300:
                    if out["catch_all"]:
                        verdict = CATCH_ALL
                    elif out["provider"] == "microsoft":
                        verdict = MS_UNSURE
                    else:
                        verdict = OK
                elif 500 <= code < 600 and _USER_UNKNOWN.search(msg or ""):
                    verdict = BAD
                else:
                    verdict = UNKNOWN
                out["results"][email] = verdict
        except aiosmtplib.SMTPConnectError as e:
            out["note"] = "Server lehnt diese Verbindung ab" if "554" in str(e) else "keine Verbindung"
            out["results"] = {e2: UNKNOWN for e2 in emails}
        except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError) as e:
            logger.debug("smtp %s: %s", mx, e)
            out["note"] = out["note"] or "keine Antwort"
            out["results"] = {e2: (v if v != UNCHECKED else UNKNOWN)
                              for e2, v in out["results"].items()}
        finally:
            try:
                await smtp.quit()
            except Exception:
                pass

    async def _reacher(self, email: str) -> str:
        headers = {}
        if os.environ.get("REACHER_SECRET"):
            headers["x-reacher-secret"] = os.environ["REACHER_SECRET"]
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{self.reacher}/v0/check_email",
                                      json={"to_email": email}, headers=headers)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.debug("reacher %s: %s", email, e)
            return UNKNOWN
        reach = data.get("is_reachable")
        if reach == "safe":
            return OK
        if reach == "invalid":
            return BAD
        if (data.get("smtp") or {}).get("is_catch_all"):
            return CATCH_ALL
        return UNKNOWN


def best_email(emails: list[dict]) -> dict | None:
    """The address most worth writing to: checked beats unchecked, a named person beats
    an inbox, an address the business published beats a guess. None if all failed."""
    def score(e):
        if e["check"] == OK:
            tier = 3
        elif e["source"] in (SOURCE_SITE, SOURCE_MAP):
            tier = 2
        elif e["source"] == SOURCE_PATTERN or e["check"] == MS_UNSURE:
            tier = 1
        else:
            tier = 0
        return (tier, bool(e.get("person")), SOURCE_RANK.get(e["source"], 0))
    ranked = sorted((e for e in emails if e["check"] != BAD), key=score, reverse=True)
    return ranked[0] if ranked else None


async def build_lead(scout, checker: MailboxChecker, company: dict) -> dict:
    """Everything known about one company, as one JSON-ready dict."""
    domain = (company.get("domain") or "").strip().lower()
    lead = {"description": "", "site_phones": [], "people": [], "emails": [],
            "mail_host": "", "provider": "", "catch_all": None, "check_note": "",
            "booking": "", "careers": False, "site": "", "site_note": ""}
    map_email = (company.get("email") or "").strip().lower()
    emails: list[dict] = []
    if map_email:
        emails.append({"email": map_email, "source": SOURCE_MAP, "person": "", "check": UNCHECKED})
    if domain:
        site = await scout._read_site(domain)
        pages = list(site["pages"])
        if site["base"]:
            lead["site"] = site["base"]
            home = pages[0][1] if pages else ""
            seen = {people_mod._norm_url(u) for u, _ in pages} | set(site["tried"])
            for url in imprint_links(home, site["base"]):
                if people_mod._norm_url(url) in seen:
                    continue
                seen.add(people_mod._norm_url(url))
                resp = await scout._site_fetch(site, url)
                if resp is not None and scout._host(str(getattr(resp, "url", "") or url)) == scout._host(domain):
                    pages.append((url, page_text(resp)))
            lead["description"] = site_description(home)
            lead["booking"] = next((m.group(0) for p in BOOKING_HINTS
                                    for m in [re.search(p, home, re.I)] if m), "")
            lead["careers"] = bool(_CAREERS.search(home))
            phones: list[str] = []
            site_emails: list[str] = []
            for _, page in pages:
                phones += [p for p in phones_on_page(page) if p not in phones]
                site_emails += [e for e in people_mod.emails_on_page(page) if e not in site_emails]
            lead["site_phones"] = phones[:4]
            site_emails = [e for e in site_emails
                           if _own(e, domain) or e.rpartition("@")[2] in FREEMAIL][:6]
            for addr in site_emails:
                if addr != map_email:
                    emails.append({"email": addr, "source": SOURCE_SITE, "person": "", "check": UNCHECKED})
            people = people_mod.find_people(pages, domain)
            people = [p for p in sorted(people, key=lambda p: -(p.get("rank") or 0))
                      if p.get("first_name") and p.get("last_name")]
            lead["people"] = [{"name": f"{p['first_name']} {p['last_name']}",
                               "title": p.get("title") or ("Inhaber" if p.get("owner") else ""),
                               "owner": bool(p.get("owner"))} for p in people[:5]]
            for person in people[:PERSONS_PER_COMPANY]:
                name = f"{person['first_name']} {person['last_name']}"
                for addr, source in candidates_for(person, domain, site_emails):
                    match = next((e for e in emails if e["email"] == addr), None)
                    if match:
                        match["person"] = name
                    else:
                        emails.append({"email": addr, "source": source, "person": name, "check": UNCHECKED})
        else:
            lead["site_note"] = "Website nicht erreichbar"
    # Check every address on the company's own domain in one session; a
    # freemail address is checked on its provider's server.
    by_domain: dict[str, list[str]] = {}
    for e in emails:
        by_domain.setdefault(e["email"].rpartition("@")[2], []).append(e["email"])
    for mail_domain, addrs in by_domain.items():
        result = await checker.check(mail_domain, addrs)
        for e in emails:
            if e["email"] in result["results"]:
                e["check"] = result["results"][e["email"]]
        if mail_domain == domain or not lead["mail_host"]:
            lead.update(mail_host=result["mail_host"], provider=result["provider"],
                        catch_all=result["catch_all"], check_note=result["note"])
    lead["emails"] = emails
    return lead


# ── The file ──

COMPANY_COLUMNS = [
    "Firma", "Branche", "Was sie machen (Website)", "Website", "Adresse", "Ort",
    "Telefon (Karte)", "Telefon (Website)", "Ansprechpartner", "Rolle",
    "Beste E-Mail", "Quelle", "Prüfung", "Weitere E-Mails", "Mailserver", "Catch-all",
    "Personen auf Website", "Online-Buchung", "Karriereseite", "Hinweis",
    "Kartenlink", "Gefunden am",
]
EMAIL_COLUMNS = ["Firma", "Website", "E-Mail", "Person", "Quelle", "Prüfung", "Mailserver"]


def _provider_label(lead: dict) -> str:
    host, provider = lead.get("mail_host") or "", lead.get("provider") or ""
    if not host:
        return ""
    names = {"google": "Google", "microsoft": "Microsoft 365", "gateway": "Filter"}
    return f"{names.get(provider, 'eigener')} ({host})"


def rows_for(company: dict, lead: dict) -> tuple[dict, list[dict]]:
    """(company row, email rows) for the export."""
    # Entries stored before a name rule existed are filtered here too.
    people = [p for p in lead.get("people") or [] if people_mod.is_person_name(p["name"])]
    names = {p["name"] for p in people}
    emails = [e for e in lead.get("emails") or []
              if not e.get("person") or e["person"] in names or e["source"] in (SOURCE_SITE, SOURCE_MAP)]
    best = best_email(emails)
    contact = next((p for p in people if best and p["name"] == best.get("person")), None) \
        or (people[0] if people else None)
    others = [f"{e['email']} ({e['source']}, {e['check']})" for e in emails
              if not best or e["email"] != best["email"]]
    catch_all = lead.get("catch_all")
    notes = [n for n in (lead.get("site_note"), lead.get("check_note")) if n]
    row = {
        "Firma": company.get("name", ""),
        "Branche": company.get("industry", ""),
        "Was sie machen (Website)": lead.get("description", ""),
        "Website": company.get("website") or lead.get("site", ""),
        "Adresse": company.get("address", ""),
        "Ort": company.get("location", ""),
        "Telefon (Karte)": company.get("phone", ""),
        "Telefon (Website)": ", ".join(lead.get("site_phones") or []),
        "Ansprechpartner": contact["name"] if contact else "",
        "Rolle": contact["title"] if contact else "",
        "Beste E-Mail": best["email"] if best else "",
        "Quelle": best["source"] if best else "",
        "Prüfung": best["check"] if best else "",
        "Weitere E-Mails": "; ".join(others),
        "Mailserver": _provider_label(lead),
        "Catch-all": "" if catch_all is None else ("ja" if catch_all else "nein"),
        "Personen auf Website": len(people) or "",
        "Online-Buchung": lead.get("booking", ""),
        "Karriereseite": "ja" if lead.get("careers") else "",
        "Hinweis": "; ".join(notes),
        "Kartenlink": company.get("source_url", ""),
        "Gefunden am": str(company.get("created_at", ""))[:10],
    }
    email_rows = [{"Firma": row["Firma"], "Website": row["Website"], "E-Mail": e["email"],
                   "Person": e.get("person", ""), "Quelle": e["source"], "Prüfung": e["check"],
                   "Mailserver": row["Mailserver"]} for e in emails]
    return row, email_rows


def to_csv(rows: list[dict], columns: list[str]) -> str:
    """Semicolons, because German Excel opens those as columns."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def to_xlsx(path, sheets: list[tuple[str, list[str], list[dict]]]):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    book.remove(book.active)
    for title, columns, rows in sheets:
        sheet = book.create_sheet(title)
        sheet.append(columns)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([row.get(c, "") for c in columns])
        sheet.freeze_panes = "B2"
        sheet.auto_filter.ref = sheet.dimensions
        for i, column in enumerate(columns, start=1):
            width = max([len(str(column))] + [len(str(r.get(column, ""))) for r in rows[:500]])
            sheet.column_dimensions[sheet.cell(1, i).column_letter].width = min(max(width + 2, 10), 60)
    book.save(path)


# ── Running it ──

async def companies_for_leads(state, limit: int = 0, refresh: bool = False) -> list[dict]:
    """Companies without a lead entry yet (all of them with ``refresh``)."""
    async with state._connect() as db:
        db.row_factory = __import__("aiosqlite").Row
        sql = ("SELECT c.* FROM companies c LEFT JOIN leads l ON l.company_id = c.id"
               + ("" if refresh else " WHERE l.company_id IS NULL")
               + " ORDER BY c.domain = '', c.created_at")
        if limit:
            sql += f" LIMIT {int(limit)}"
        async with db.execute(sql) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def save_lead(state, company_id: str, lead: dict):
    async with state._connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO leads (company_id, data, checked_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (company_id, json.dumps(lead, ensure_ascii=False)))
        await db.commit()


async def collect_leads(state, scout, limit: int = 0, concurrency: int = 8,
                        verify: bool = True, refresh: bool = False, progress=None) -> int:
    """Build and store a lead entry for each company still without one."""
    companies = await companies_for_leads(state, limit, refresh)
    checker = MailboxChecker(enabled=verify)
    gate = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def one(company):
        nonlocal done
        async with gate:
            try:
                lead = await asyncio.wait_for(build_lead(scout, checker, company), 240)
            except Exception as e:
                logger.warning("lead %s: %s", company.get("domain") or company.get("name"), e)
                lead = {"site_note": "Fehler beim Lesen", "emails": []}
            await save_lead(state, company["id"], lead)
            scout._site_reads.pop(scout._host(company.get("domain") or "x"), None)
            done += 1
            if progress and done % 25 == 0:
                progress(done, len(companies))

    scout._site_reads, scout._site_evidence, scout._own_domains = {}, {}, {}
    scout._challenged = set()
    await asyncio.gather(*(one(c) for c in companies))
    return done


async def export_leads(state, out_base) -> dict:
    """Write <out_base>.xlsx and <out_base>.csv from every stored lead. Returns counts."""
    async with state._connect() as db:
        db.row_factory = __import__("aiosqlite").Row
        async with db.execute(
                "SELECT c.*, l.data AS lead FROM companies c JOIN leads l ON l.company_id = c.id "
                "ORDER BY c.location, c.name") as cur:
            records = [dict(r) for r in await cur.fetchall()]
    company_rows, email_rows = [], []
    for record in records:
        row, emails = rows_for(record, json.loads(record.get("lead") or "{}"))
        company_rows.append(row)
        email_rows += emails
    out_base = str(out_base)
    with open(out_base + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(to_csv(company_rows, COMPANY_COLUMNS))
    to_xlsx(out_base + ".xlsx", [("Firmen", COMPANY_COLUMNS, company_rows),
                                 ("E-Mails", EMAIL_COLUMNS, email_rows)])
    count = lambda key, value: sum(1 for r in company_rows if r[key] == value)
    return {
        "companies": len(company_rows),
        "with_website": sum(1 for r in company_rows if r["Website"]),
        "with_phone": sum(1 for r in company_rows if r["Telefon (Karte)"] or r["Telefon (Website)"]),
        "with_email": sum(1 for r in company_rows if r["Beste E-Mail"]),
        "with_person": sum(1 for r in company_rows if r["Ansprechpartner"]),
        "confirmed": count("Prüfung", OK),
        "catch_all": count("Prüfung", CATCH_ALL),
        "unknown": count("Prüfung", UNKNOWN) + count("Prüfung", UNCHECKED),
        "microsoft": count("Prüfung", MS_UNSURE),
        "published": sum(1 for r in company_rows if r["Quelle"] in (SOURCE_SITE, SOURCE_MAP)),
        "emails": len(email_rows),
    }


def default_out_base(project_root) -> str:
    folder = project_root / "data" / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f"leads-{date.today():%Y-%m-%d}")


async def sample_prospects(state, n: int) -> list:
    """Up to n people to write a preview for: stored contacts first, else people from
    the lead list (with what their site says as research notes). Saves nothing.

    The address always belongs to whoever is greeted: a named person's own address,
    or a general inbox written to as a company inbox."""
    from harvey.models.prospect import COMPANY_INBOX_SOURCE, Prospect

    found = [p for p in await state.get_prospects_by_status("new") if p.company][:n]
    if len(found) >= n:
        return found
    async with state._connect() as db:
        db.row_factory = __import__("aiosqlite").Row
        async with db.execute(
                "SELECT c.id, c.name, c.industry, c.location, l.data AS lead FROM companies c "
                "JOIN leads l ON l.company_id = c.id WHERE l.data LIKE '%@%' "
                "ORDER BY RANDOM() LIMIT 200") as cur:
            records = [dict(r) for r in await cur.fetchall()]
    named, inboxes = [], []
    for record in records:
        lead = json.loads(record.get("lead") or "{}")
        emails = lead.get("emails") or []
        person, email = None, None
        for p in lead.get("people") or []:
            name = (p.get("name") or "").strip()
            mine = best_email([e for e in emails if (e.get("person") or "").strip() == name])
            if people_mod.is_person_name(name) and mine:
                person, email = p, mine
                break
        if not person:
            email = best_email([e for e in emails if not e.get("person")])
            if not email:
                continue
        notes = [lead.get("description") or ""]
        if lead.get("booking"):
            notes.append(f"Online booking on their site: {lead['booking']}")
        elif lead.get("site"):
            notes.append("No online booking found on their site")
        if lead.get("careers"):
            notes.append("Their site has a jobs/careers section")
        common = dict(company=record["name"], company_id=record["id"],
                      industry=record.get("industry") or "", email=email["email"],
                      personalization_notes=". ".join(x for x in notes if x))
        if person:
            first, _, last = person["name"].strip().partition(" ")
            named.append(Prospect(first_name=first, last_name=last,
                                  title=person.get("title") or "", **common))
        else:
            inboxes.append(Prospect(title="General inbox", source=COMPANY_INBOX_SOURCE, **common))
    return (found + named + inboxes)[:n]

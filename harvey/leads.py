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
from urllib.parse import urljoin
from datetime import date

import aiosmtplib
import httpx

from harvey.collectors import people as people_mod
from harvey.public_http import page_text, same_site
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
MAX_SESSIONS_PER_DOMAIN = 2          # so a long candidate list is still checked to the end
# This many connection failures in a row on different mail servers means port 25 is
# blocked on this line: checking stops for the run instead of waiting on every domain.
PORT25_FAILS_TO_STOP = 8
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


_MOJIBAKE = re.compile("[ÃÂâ][\u0080-\u00bf\u2018-\u203a\u0152-\u017e]")


def repair_mojibake(value: str) -> str:
    """UTF-8 that was read as Latin-1 somewhere ("fÃ¼r" -> "für"), even when the
    site itself publishes it that way. Text that isn't mojibake stays as it is."""
    if not value or not _MOJIBAKE.search(value):
        return value
    for codec in ("cp1252", "latin-1"):
        try:
            fixed = value.encode(codec).decode("utf-8")
        except UnicodeError:
            continue
        if not _MOJIBAKE.search(fixed):
            return fixed
    return value


def _text(value: str, limit: int = 300) -> str:
    # Twice: some sites escape their meta description again ("&amp;uuml;").
    value = html_lib.unescape(html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", value or "")))
    value = repair_mojibake(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


_PLACEHOLDER = re.compile(
    r"(?i)\b(?:just another wordpress site|eine weitere wordpress-website|"
    r"(?:a|an|the)\s+(?:\w+\s+){0,3}(?:wordpress\s+)?(?:template|theme)\b|"
    r"lorem ipsum|coming soon|under construction|wartungsmodus|seite im aufbau|"
    r"website (?:ist )?im aufbau|default site)")


def site_description(html: str) -> str:
    """What the site says the business does: its meta description, else its title.
    A theme's or builder's placeholder text is no description."""
    text = _site_description(html)
    return "" if _PLACEHOLDER.search(text) else text


def _site_description(html: str) -> str:
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
    raw = re.sub(r"(?i)^(?:telefon|telefax|tel|fon|phone|mobil)(?![a-z])\s*[:.]?\s*", "", raw).strip(" .-/")
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
            if re.match(r"(?i)(mailto|tel|javascript):", href.strip()):
                continue
            url = urljoin(base if base.endswith("/") else base + "/", href.strip())
            if not url.startswith("http"):
                continue
            if url not in out:
                out.append(url)
    return out[:3]


def _own(addr: str, domain: str) -> bool:
    other = addr.rpartition("@")[2].removeprefix("www.")
    return other == domain or other.endswith("." + domain)


def _names_the_business(email: str, name: str, domain: str) -> bool:
    """praxis.huber@gmx.de or physio-huber@t-online.de for Physio Huber: the business's
    own freemail address, by a role word or a word of its name or domain."""
    local = (email or "").lower().partition("@")[0]
    if any(word in local for word in _ROLE_WORDS):
        return True
    words = {w for part in (name or "").split() for w in [_clean_name_part(part)] if len(w) >= 4}
    words |= set(re.findall(r"[a-z]{4,}", (domain or "").split(".")[0]))
    words -= {"praxis", "physio", "physiotherapie", "gmbh", "therapie", "zentrum"}
    return any(w in local for w in words)


def person_patterns(person: dict, domain: str) -> set[str]:
    """Every address the usual patterns make from this person's name."""
    first, last = person.get("first_name", ""), person.get("last_name", "")
    lasts = [last] + ([last.split("-")[0]] if "-" in last else [])
    return {e for ln in lasts for e in generate_patterns(first, ln, domain)}


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


def to_ascii_domain(domain: str) -> str:
    """müller-bau.de -> xn--mller-bau-q9a.de, which mail servers and DNS understand."""
    domain = (domain or "").strip().lower().rstrip(".")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def to_ascii_address(email: str) -> str:
    local, _, domain = (email or "").rpartition("@")
    return f"{local}@{to_ascii_domain(domain)}" if local else email


_ENHANCED = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")


def user_unknown(code: int, msg: str) -> bool:
    """A 5xx that says this mailbox doesn't exist, not that we were refused.
    The enhanced code decides when there is one (5.1.x unknown, 5.7.x policy);
    a refusal that names a block never counts as "unknown user"."""
    if not 500 <= code < 600:
        return False
    enhanced = _ENHANCED.search(msg or "")
    if enhanced:
        return enhanced.group(2) == "1" and enhanced.group(3) in ("0", "1", "2", "3", "6", "10")
    if _BLOCKED.search(msg or ""):
        return False
    return bool(_USER_UNKNOWN.search(msg or ""))


_mx_results: dict[str, tuple[str, str]] = {}


async def mx_lookup(domain: str) -> tuple[str, str]:
    """(mail host, "ok" | "none" | "error"). A domain without MX but with an
    address record takes mail itself (RFC 5321). Only a clear "doesn't exist"
    is "none"; a timeout or server failure is "error" and isn't remembered."""
    import dns.asyncresolver
    import dns.exception
    import dns.resolver
    if domain in _mx_results:
        return _mx_results[domain]
    result = ("", "error")
    try:
        answers = await dns.asyncresolver.resolve(domain, "MX", lifetime=10)
        records = sorted(answers, key=lambda r: r.preference)
        host = str(records[0].exchange).rstrip(".") if records else ""
        result = (host, "ok") if host else ("", "none")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        try:
            await dns.asyncresolver.resolve(domain, "A", lifetime=10)
            result = (domain, "ok")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            result = ("", "none")
        except Exception:
            result = ("", "error")
    except Exception as e:
        logger.debug("MX lookup failed for %s: %s", domain, e)
    if result[1] != "error":
        _mx_results[domain] = result
    return result


class MailboxChecker:
    """Asks each domain's mail server whether addresses exist, one session per domain."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.reacher = os.environ.get("REACHER_URL", "").rstrip("/")
        name = socket.getfqdn()
        self.helo = os.environ.get("HARVEY_HELO") or (
            name if "." in name and not name.endswith(".arpa") else "localhost.localdomain")
        self._hosts: dict[str, asyncio.Semaphore] = {}
        self._connect_fails = 0
        self.stopped = ""        # why checking was switched off mid-run, or ""

    async def check(self, domain: str, emails: list[str]) -> dict:
        """{"mail_host", "provider", "catch_all", "results": {email: verdict}, "note"}."""
        out = {"mail_host": "", "provider": "", "catch_all": None,
               "results": {e: UNCHECKED for e in emails}, "note": ""}
        domain = to_ascii_domain(domain)
        mx, dns_state = await mx_lookup(domain)
        if dns_state == "none":
            out["note"] = "kein Mailserver"
            out["results"] = {e: BAD for e in emails}
            return out
        if not mx:
            out["note"] = "DNS-Abfrage fehlgeschlagen"
            out["results"] = {e: UNKNOWN for e in emails}
            return out
        out["mail_host"], out["provider"] = mx, classify_mx(mx)
        if not self.enabled or not emails:
            return out
        if self.reacher:
            for email in emails:
                out["results"][email] = await self._reacher(email)
            return out
        gate = self._hosts.setdefault(mx, asyncio.Semaphore(SESSIONS_PER_MAIL_HOST))
        chunks = [emails[i:i + MAX_RCPT_PER_SESSION]
                  for i in range(0, len(emails), MAX_RCPT_PER_SESSION)][:MAX_SESSIONS_PER_DOMAIN]
        for chunk in chunks:
            async with gate:
                await self._smtp(mx, domain, chunk, out)
            if out["note"]:
                break          # blocked or no answer: a second session won't do better
        self._count_connection(out)
        return out

    def _count_connection(self, out: dict):
        if out["note"] in ("keine Verbindung", "keine Antwort"):
            self._connect_fails += 1
            if self._connect_fails >= PORT25_FAILS_TO_STOP and self.enabled:
                self.enabled = False
                self.stopped = ("Port 25 scheint an diesem Anschluss gesperrt: die Prüfung "
                                "der Mailserver ist für diesen Lauf aus. Mit REACHER_URL oder "
                                "auf einem Server mit fester IP geht sie.")
                logger.warning(self.stopped)
        elif out["mail_host"]:
            self._connect_fails = 0

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
            elif user_unknown(code, msg):
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
                code, msg = await self._ask(smtp.rcpt(to_ascii_address(email)))
                if code < 300:
                    if out["catch_all"]:
                        verdict = CATCH_ALL
                    elif out["provider"] == "microsoft":
                        verdict = MS_UNSURE
                    else:
                        verdict = OK
                elif user_unknown(code, msg):
                    verdict = BAD
                else:
                    verdict = UNKNOWN
                out["results"][email] = verdict
        except aiosmtplib.SMTPConnectError as e:
            out["note"] = "Server lehnt diese Verbindung ab" if "554" in str(e) else "keine Verbindung"
            out["results"] = {e2: UNKNOWN for e2 in emails}
        except Exception as e:  # SMTP, network, timeouts, and anything odd a server sends
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
                if resp is not None and (same_site(scout._host(str(getattr(resp, "url", "") or url)), scout._host(domain))
                                         or same_site(scout._host(str(getattr(resp, "url", "") or url)), scout._host(site["base"]))):
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
            people = people_mod.find_people(pages, domain)
            people = [p for p in sorted(people, key=lambda p: -(p.get("rank") or 0))
                      if p.get("first_name") and p.get("last_name")]
            everyone = [{"name": f"{p['first_name']} {p['last_name']}"} for p in people]
            # A freemail address on the site is often the web designer's or a
            # neighbour practice's: kept only when it spells someone named here.
            site_emails = [e for e in site_emails
                           if _own(e, domain)
                           or (e.rpartition("@")[2] in FREEMAIL
                               and (_person_behind(e, everyone)
                                    or _names_the_business(e, company.get("name", ""), domain)))][:6]
            for addr in site_emails:
                if addr != map_email:
                    emails.append({"email": addr, "source": SOURCE_SITE, "person": "", "check": UNCHECKED})
            lead["people"] = [{"name": f"{p['first_name']} {p['last_name']}",
                               "title": p.get("title") or ("Inhaber" if p.get("owner") else ""),
                               "owner": bool(p.get("owner"))} for p in people[:5]]
            fits: dict[str, set[str]] = {}
            for person in people:
                for addr in person_patterns(person, domain):
                    fits.setdefault(addr, set()).add(f"{person['first_name']} {person['last_name']}")
            for person in people[:PERSONS_PER_COMPANY]:
                name = f"{person['first_name']} {person['last_name']}"
                for addr, source in candidates_for(person, domain, site_emails):
                    owners = fits.get(addr, set()) | {name}
                    match = next((e for e in emails if e["email"] == addr), None)
                    if len(owners) > 1:
                        # Hans and Heike Müller can both be hmueller@: nobody gets it.
                        if match:
                            match["person"] = ""
                        continue
                    if match:
                        if not match["person"]:
                            match["person"] = name
                        elif match["person"] != name:
                            match["person"] = ""
                    else:
                        emails.append({"email": addr, "source": source, "person": name, "check": UNCHECKED})
        elif site.get("redirected"):
            lead["site_note"] = f"leitet weiter auf {site['redirected']}, nicht übernommen"
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
    "Zuordnung geprüft", "Prüfhinweis", "Kartenlink", "Gefunden am",
]
EMAIL_COLUMNS = ["Firma", "Website", "E-Mail", "Person", "Quelle", "Prüfung", "Mailserver"]


def _provider_label(lead: dict, best_email: str = "") -> str:
    """Who runs the mail for the best address: the freemail provider for a gmx or
    aon address, otherwise the company domain's server."""
    mail_domain = (best_email or "").rpartition("@")[2].lower()
    if mail_domain in FREEMAIL:
        return f"Freemail ({mail_domain})"
    host, provider = lead.get("mail_host") or "", lead.get("provider") or ""
    if not host:
        return ""
    names = {"google": "Google", "microsoft": "Microsoft 365", "gateway": "Filter"}
    return f"{names.get(provider, 'eigener')} ({host})"


_ROLE_WORDS = ("info", "office", "kontakt", "praxis", "mail", "post", "buero", "termin", "anmeldung",
               "empfang", "team", "service", "kanzlei", "studio", "zentrale", "verwaltung", "hallo",
               "therapie", "physio", "ordination", "rezeption", "sekretariat", "verkauf", "bestellung")
_NAME_SHAPED = re.compile(r"^(?:[a-z]{2,}[._-][a-z]{2,}|[a-z][._-]?[a-z]{3,}[._-]?[a-z]?)$")


def is_role_address(email: str, people: list[dict] | None = None) -> bool:
    """An inbox for the business (info@, praxis@, therapie@, graz@), not one person's
    mailbox. Personal: a freemail address (gmail, gmx), one shaped like a name
    (vorname.nachname@, j.schmidt@), or one that spells a person named on the site,
    first name alone included (carmen@ when the site names Carmen Brandstetter)."""
    local, _, domain = (email or "").lower().partition("@")
    if not local:
        return False
    if local in ROLE_LOCALS or local.split(".")[0] in ROLE_LOCALS:
        return True
    if domain in FREEMAIL:
        return False
    for p in people or []:
        first = _clean_name_part((p.get("name") or "").split(" ")[0])
        if (first and local.split(".")[0] == first) or _person_behind(email, [p]):
            return False
    if any(word in local for word in _ROLE_WORDS):
        return True
    if re.search(r"[._-]", local) and _NAME_SHAPED.match(local):
        return False        # vorname.nachname@, j.schmidt@: someone's own mailbox
    return True


def is_business_inbox(email: str, people: list[dict] | None = None,
                      company_name: str = "", domain: str = "") -> bool:
    """Strict: an inbox for the business by its own words (info@, praxis@, therapie@,
    praxis.huber@), never an address that might be one unnamed person's (carmen@,
    graz@ stays unknown). Only these get the site's first person next to them."""
    if not is_role_address(email, people):
        return False
    local = (email or "").lower().partition("@")[0]
    business = {w for part in (company_name or "").split() for w in [_clean_name_part(part)] if len(w) >= 3}
    business |= set(re.findall(r"[a-z]{3,}", (domain or "").split(".")[0]))
    # Every part must be about the business (team-sabine@ or sekretariat.mayer@ is a person's).
    for token in filter(None, re.split(r"[._\-\d]+", local)):
        if not (token in ROLE_LOCALS or token in _ROLE_WORDS or token in business
                or any(token == w + "n" or token == w + "s" for w in _ROLE_WORDS)):
            return False
    return True


def _person_behind(email: str, people: list[dict]) -> str:
    """The one named person an address spells out exactly (m.lindner@,
    martina.lindner@, lindner@), or "" when it spells nobody, spells a longer
    name (a.bergmann@ is not Anna Berg) or could be more than one of them."""
    found = []
    for p in people:
        first, _, last = (p.get("name") or "").strip().partition(" ")
        surnames = last.split()
        if not first or not surnames:
            continue
        lasts = {surnames[-1], last.replace(" ", "-")}
        lasts |= {ln.split("-")[0] for ln in list(lasts) if "-" in ln}
        if any(infer_pattern_from_email(email, first, ln) for ln in lasts):
            found.append(p["name"])
    return found[0] if len(found) == 1 else ""


_COUNTRY_CODES = {"AT": "43", "DE": "49", "CH": "41", "LI": "423"}


def company_country(company: dict, lead: dict | None = None) -> str:
    """AT, DE, CH or LI: the website's ending first, then a country or a big
    city named as a whole word in the place (not the street: "Wiener Straße")."""
    host = (company.get("domain") or (lead or {}).get("site") or "").lower().rstrip("/")
    ending = host.rsplit(".", 1)[-1] if "." in host else ""
    if ending in ("at", "de", "ch", "li"):
        return ending.upper()
    place = (company.get("location") or "").lower()
    for words, code in ((("österreich", "austria", "wien", "graz", "salzburg", "innsbruck"), "AT"),
                        (("schweiz", "suisse", "svizzera", "zürich", "zurich", "bern", "basel", "genf"), "CH"),
                        (("liechtenstein",), "LI"), (("deutschland", "germany"), "DE")):
        if any(re.search(rf"(?<![a-zäöüß]){w}(?![a-zäöüß])", place) for w in words):
            return code
    return ""


def phone_key(phone: str, country: str = "") -> str:
    """A phone number in one comparable form: +43 316 1234, 0043 316/1234 and
    0316 1234 (in Austria) are the same key; nothing is cut off."""
    raw = re.sub(r"^(\+\d{1,3})\s*\(0\)", r"\1", (phone or "").strip())   # +43 (0)316 -> +43 316
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0") and _COUNTRY_CODES.get(country):
        return _COUNTRY_CODES[country] + digits[1:]
    return "n" + digits          # national form, country unknown: compared as written


def review_removed(lead: dict, country: str = "") -> dict:
    """What a check took out of a lead, normalised: emails, contacts, phone keys, description.
    Reads the older single-value form (email, contact) too."""
    rm = ((lead or {}).get("review") or {}).get("remove") or {}
    emails = {e.lower() for e in (rm.get("emails") or []) + ([rm["email"]] if rm.get("email") else [])}
    contacts = set(rm.get("contacts") or []) | ({rm["contact"]} if rm.get("contact") else set())
    phones = {phone_key(p, country) for p in rm.get("phones") or []} - {""}
    return {"emails": emails, "contacts": contacts, "phones": phones,
            "description": bool(rm.get("description"))}


def review_people(lead: dict, as_dicts: bool = False) -> list:
    """The people of a lead that look like names and no check has removed."""
    removed = review_removed(lead)["contacts"]
    people = [p for p in lead.get("people") or []
              if people_mod.is_person_name(p.get("name") or "") and p["name"] not in removed]
    return people if as_dicts else [p["name"] for p in people]


def rows_for(company: dict, lead: dict) -> tuple[dict, list[dict]]:
    """(company row, email rows) for the export."""
    # Entries stored before a name rule existed are filtered here too.
    people = [p for p in lead.get("people") or [] if people_mod.is_person_name(p["name"])]
    names = {p["name"] for p in people}
    # What `harvey pruefen` or the agents took out stays out of the file.
    review = lead.get("review") or {}
    country = company_country(company, lead)
    removed = review_removed(lead, country)
    people = [p for p in people if p["name"] not in removed["contacts"]]
    names = {p["name"] for p in people}
    emails = [e for e in lead.get("emails") or []
              if (not e.get("person") or e["person"] in names or e["source"] in (SOURCE_SITE, SOURCE_MAP))
              and e["email"].lower() not in removed["emails"]
              and (e.get("person") or "") not in removed["contacts"]]
    best = best_email(emails)
    # The contact is whoever the best address belongs to. Only a general inbox
    # (no person on it) is shown with the first person named on the site.
    owner = (best or {}).get("person") or ""
    if not owner and best:
        owner = _person_behind(best["email"], people)
    if owner:
        contact = next((p for p in people if p["name"] == owner), None) \
            or {"name": owner, "title": ""}
    elif best and is_business_inbox(best["email"], people, company.get("name", ""),
                                    (company.get("domain") or "")):
        contact = people[0] if people else None
    else:
        contact = None      # a personal address of someone the site doesn't name
    if contact and contact["name"] in removed["contacts"]:
        contact = None
    site_phones = [p for p in lead.get("site_phones") or [] if phone_key(p, country) not in removed["phones"]]
    map_phone = company.get("phone", "")
    if map_phone and phone_key(map_phone, country) in removed["phones"]:
        map_phone = ""
    others = [f"{e['email']} ({e['source']}, {e['check']})" for e in emails
              if not best or e["email"] != best["email"]]
    catch_all = lead.get("catch_all")
    notes = [n for n in (lead.get("site_note"), lead.get("check_note")) if n]
    row = {
        "Firma": company.get("name", ""),
        "Branche": company.get("industry", ""),
        "Was sie machen (Website)": "" if removed["description"] else repair_mojibake(lead.get("description", "")),
        "Website": company.get("website") or lead.get("site", ""),
        "Adresse": company.get("address", ""),
        "Ort": company.get("location", ""),
        "Telefon (Karte)": map_phone,
        "Telefon (Website)": ", ".join(site_phones),
        "Ansprechpartner": contact["name"] if contact else "",
        "Rolle": contact["title"] if contact else "",
        "Beste E-Mail": best["email"] if best else "",
        "Quelle": best["source"] if best else "",
        "Prüfung": best["check"] if best else "",
        "Weitere E-Mails": "; ".join(others),
        "Mailserver": _provider_label(lead, best["email"] if best else ""),
        "Catch-all": "" if catch_all is None else ("ja" if catch_all else "nein"),
        "Personen auf Website": len(people) or "",
        "Online-Buchung": lead.get("booking", ""),
        "Karriereseite": "ja" if lead.get("careers") else "",
        "Hinweis": "; ".join(notes),
        "Zuordnung geprüft": {"ok": "ja", "korrigiert": "korrigiert", "unsicher": "unsicher"}.get(
            review.get("status", ""), "nein"),
        "Prüfhinweis": "; ".join(review.get("notes") or []),
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
        writer.writerow({k: _cell(v) for k, v in row.items()})
    return buf.getvalue()


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    """CSV: text from websites and the map stays text, Excel never runs it as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


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
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith(_FORMULA_START):
                    cell.data_type = "s"     # shown as typed, never run as a formula
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
                # Not saved: the next run tries this company again.
                logger.warning("lead %s: %s", company.get("domain") or company.get("name"), e)
                lead = None
            if lead is not None:
                await save_lead(state, company["id"], lead)
            scout._site_reads.pop(scout._host(company.get("domain") or "x"), None)
            done += 1
            if progress and done % 25 == 0:
                progress(done, len(companies))

    scout._site_reads, scout._site_evidence, scout._own_domains = {}, {}, {}
    scout._challenged = set()
    await asyncio.gather(*(one(c) for c in companies))
    if checker.stopped:
        print("\n  " + checker.stopped)
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
        "with_person": sum(1 for r in company_rows if r["Personen auf Website"]),
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
        removed = review_removed(lead)
        emails = [e for e in lead.get("emails") or []
                  if e["email"].lower() not in removed["emails"]
                  and (e.get("person") or "") not in removed["contacts"]]
        person, email = None, None
        for p in lead.get("people") or []:
            name = (p.get("name") or "").strip()
            mine = best_email([e for e in emails if (e.get("person") or "").strip() == name])
            if people_mod.is_person_name(name) and mine and name not in removed["contacts"]:
                person, email = p, mine
                break
        if not person:
            email = best_email([e for e in emails if not e.get("person")
                                and is_business_inbox(e["email"], lead.get("people") or [],
                                                      record.get("name", ""))])
            if not email:
                continue
        notes = ["" if removed["description"] else (lead.get("description") or "")]
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

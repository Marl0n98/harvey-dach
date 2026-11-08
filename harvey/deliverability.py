"""Deliverability readiness - is this sending domain safe to cold email from?

Everything here is free and read-only: public DNS, RDAP, DNS blocklists, and
Harvey's own send history. None of it can measure inbox reputation directly
(only mailbox providers know that), so the report also points at the two
things that can: Google Postmaster Tools and an inbox-placement test.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

import dns.exception
import dns.resolver
import httpx

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"

# Selectors used by the common mailbox providers, tried in order when the
# operator hasn't named one (warmup.dkim_selector).
COMMON_DKIM_SELECTORS = (
    "google", "default", "selector1", "selector2", "k1", "k2", "s1", "s2",
    "mail", "dkim", "fm1", "fm2", "fm3", "zoho", "zmail", "mxvault",
    "protonmail", "protonmail2", "mandrill", "smtp", "sig1", "everlytickey1",
)

# (zone, how a "query refused" answer looks) - public resolvers like 8.8.8.8
# are blocked by these lists and get a sentinel answer instead of a verdict.
DOMAIN_BLOCKLISTS = (
    ("dbl.spamhaus.org", lambda ip: ip.startswith("127.255.255.")),
    ("multi.surbl.org", lambda ip: ip == "127.0.0.1"),
    ("multi.uribl.com", lambda ip: ip == "127.0.0.1"),
)
IP_BLOCKLISTS = (
    ("zen.spamhaus.org", lambda ip: ip.startswith("127.255.255.")),
    ("bl.spamcop.net", lambda ip: False),
)

# Where to look a domain or IP up by hand when a list refuses public DNS.
MANUAL_LOOKUP = {
    "dbl.spamhaus.org": "https://check.spamhaus.org",
    "zen.spamhaus.org": "https://check.spamhaus.org",
    "multi.surbl.org": "https://www.surbl.org/surbl-analysis",
    "multi.uribl.com": "https://admin.uribl.com/",
    "bl.spamcop.net": "https://www.spamcop.net/bl.shtml",
}

MIN_DOMAIN_AGE_DAYS = 30
GOOD_DOMAIN_AGE_DAYS = 90
MAX_BOUNCE_RATE = 0.03
MIN_SENDS_FOR_RATES = 20


@dataclass
class Check:
    name: str
    status: str
    detail: str
    unknown: bool = False  # the lookup itself failed: no verdict either way


DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")


class LookupFailed(Exception):
    """DNS couldn't answer (timeout, no reachable server): not the same as
    'no such record', so a check reports it as unknown, never as missing."""


def _resolve(name: str, rtype: str):
    try:
        return list(dns.resolver.resolve(name, rtype, lifetime=8))
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as e:
        raise LookupFailed(type(e).__name__) from e


def _txt(name: str) -> list[str]:
    return [b"".join(r.strings).decode("utf-8", "replace") for r in _resolve(name, "TXT")]


def _a(name: str) -> list[str]:
    return [r.address for r in _resolve(name, "A")]


def _unknown(name: str, e: LookupFailed) -> Check:
    return Check(name, INFO, f"Konnte das nicht nachschlagen ({e}). Prüf deine Verbindung und versuch es noch einmal.",
                 unknown=True)


def check_mx(domain: str) -> Check:
    try:
        mx = sorted(_resolve(domain, "MX"), key=lambda r: r.preference)
    except LookupFailed as e:
        return _unknown("MX", e)
    if not mx:
        return Check("MX", FAIL, "Kein MX-Eintrag. Antworten und Bounces kommen nirgends an.")
    return Check("MX", OK, ", ".join(str(r.exchange).rstrip(".") for r in mx[:3]))


def check_spf(domain: str) -> Check:
    try:
        records = [t for t in _txt(domain) if t.lower().startswith("v=spf1")]
    except LookupFailed as e:
        return _unknown("SPF", e)
    if not records:
        return Check("SPF", FAIL, "Kein SPF-Eintrag. Leg den TXT-Eintrag an, den dein Mailanbieter dir gibt.")
    if len(records) > 1:
        return Check("SPF", FAIL, f"{len(records)} SPF-Einträge. Empfänger werten das wie gar kein SPF. Führ sie zusammen.")
    spf = records[0]
    terms = spf.lower().split()
    if "+all" in terms or "all" in terms:
        return Check("SPF", FAIL, f"'+all' erlaubt jedem, in deinem Namen zu senden: {spf}")
    lookups = sum(t.lstrip("+-~?").split(":")[0].split("=")[0].split("/")[0] in
                  ("include", "a", "mx", "ptr", "exists", "redirect") for t in terms)
    if lookups > 10:
        # Top-level terms only; includes add their own lookups on top.
        return Check("SPF", FAIL, f"{lookups} DNS-Abfragen (Grenze ist 10). SPF endet mit permerror.")
    if "?all" in terms:
        return Check("SPF", WARN, f"'?all' ist neutral. Nimm '~all' oder '-all': {spf}")
    if not any(t.lstrip("+-~?") == "all" or t.startswith("redirect=") for t in terms):
        return Check("SPF", WARN, f"Kein 'all' am Ende, es endet also neutral. Ergänze '~all': {spf}")
    return Check("SPF", OK, spf)


def check_dmarc(domain: str) -> Check:
    try:
        records = [t for t in _txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    except LookupFailed as e:
        return _unknown("DMARC", e)
    if not records:
        return Check("DMARC", FAIL, "Kein DMARC-Eintrag. Gmail und Yahoo verlangen einen für Massenversender. "
                     f"Fang an mit TXT _dmarc.{domain} = \"v=DMARC1; p=none; rua=mailto:you@{domain}\"")
    if len(records) > 1:
        return Check("DMARC", FAIL, f"{len(records)} DMARC-Einträge. Empfänger ignorieren dann alle. Behalte einen.")
    tags = {k.strip().lower(): v for k, v in (
        part.strip().split("=", 1) for part in records[0].split(";") if "=" in part)}
    policy = tags.get("p", "").strip().lower()
    if not policy:
        return Check("DMARC", WARN, f"Kein p=-Tag für die Richtlinie. Fang mit p=none an: {records[0]}")
    if policy == "none":
        return Check("DMARC", WARN, f"p=none (nur Beobachtung). Passt während des Warmups. "
                     f"Wechsel zu quarantine, sobald die Berichte sauber aussehen. {records[0]}")
    return Check("DMARC", OK, records[0])


def check_dkim(domain: str, selector: str = "") -> Check:
    selectors = (selector,) if selector else COMMON_DKIM_SELECTORS
    for sel in selectors:
        try:
            records = _txt(f"{sel}._domainkey.{domain}")
        except LookupFailed as e:
            return _unknown("DKIM", e)
        for record in records:
            if "p=" in record.replace(" ", "") and "p=;" not in record.replace(" ", "") + ";":
                return Check("DKIM", OK, f"Selektor '{sel}' gefunden")
    if selector:
        return Check("DKIM", FAIL, f"Kein DKIM-Schlüssel unter {selector}._domainkey.{domain}.")
    return Check("DKIM", WARN, "Kein Schlüssel unter einem der üblichen Selektoren. Wenn dein Anbieter einen eigenen nutzt, "
                 "setz `channels.email.warmup.dkim_selector`. Sonst schalte DKIM bei deinem Anbieter ein.")


def check_domain_age(domain: str) -> Check:
    try:
        r = httpx.get(f"https://rdap.org/domain/{domain}", timeout=15, follow_redirects=True)
        r.raise_for_status()
        events = r.json().get("events", [])
    except Exception as e:
        return Check("Domainalter", INFO, f"Konnte das Registrierungsdatum nicht nachschlagen ({type(e).__name__}).")
    registered = next((e.get("eventDate") for e in events if e.get("eventAction") == "registration"), None)
    if not registered:
        return Check("Domainalter", INFO, "Die Registry nennt kein Registrierungsdatum.")
    try:
        age = (date.today() - datetime.fromisoformat(registered.replace("Z", "+00:00")).date()).days
    except ValueError:
        return Check("Domainalter", INFO, f"Die Registry nennt ein unlesbares Datum ({registered[:40]}).")
    status = FAIL if age < MIN_DOMAIN_AGE_DAYS else WARN if age < GOOD_DOMAIN_AGE_DAYS else OK
    note = {FAIL: ". Ganz neuen Domains wird misstraut: nur Warmup, noch keine Kaltakquise.",
            WARN: ". Noch jung: Halt das Volumen niedrig.", OK: ""}[status]
    return Check("Domainalter", status, f"{age} Tage (registriert {registered[:10]}){note}")


def _blocklist_checks(query: str, label: str, lists) -> list[Check]:
    checks = []
    for zone, refused in lists:
        try:
            hits = _a(f"{query}.{zone}")
        except LookupFailed as e:
            checks.append(_unknown(f"{label} {zone}", e))
            continue
        if not hits:
            checks.append(Check(f"{label} {zone}", OK, "nicht gelistet"))
        elif any(refused(ip) for ip in hits):
            checks.append(Check(f"{label} {zone}", INFO,
                                "Liste hat die Abfrage abgelehnt (häufig über öffentliches DNS wie 8.8.8.8). "
                                f"Prüf es selbst unter {MANUAL_LOOKUP.get(zone, zone)}"))
        else:
            checks.append(Check(f"{label} {zone}", FAIL, f"GELISTET ({', '.join(hits)})"))
    return checks


def check_domain_blocklists(domain: str) -> list[Check]:
    return _blocklist_checks(domain, "Blockliste", DOMAIN_BLOCKLISTS)


def check_ip_blocklists(smtp_host: str) -> list[Check]:
    try:
        ips = _a(smtp_host)
    except LookupFailed as e:
        return [_unknown("Versand-IP", e)]
    if not ips:
        return [Check("Versand-IP", INFO, f"Konnte {smtp_host} nicht auflösen.")]
    reversed_ip = ".".join(reversed(ips[0].split(".")))
    return _blocklist_checks(reversed_ip, f"IP {ips[0]}", IP_BLOCKLISTS)


def check_warmup(warmup, ceiling: int, today: date | None = None,
                 provider: str = "") -> Check:
    if provider == "instantly":
        return Check("Warmup", INFO, "Harveys Hochlauf gilt nicht für Instantly. Nutz das Warmup von Instantly "
                     "und das Tageslimit pro Konto dort.")
    days = warmup.days_in(today)
    if days is None:
        return Check("Warmup", WARN, "Kein Startdatum fürs Warmup gesetzt, der Versand wird also nicht hochgefahren. Sobald dein "
                     "Warmup-Dienst läuft, setz `channels.email.warmup.start_date: YYYY-MM-DD`.")
    if days < 0:
        return Check("Warmup", WARN, f"Startet am {warmup.start_date} (in {-days} Tagen).")
    if days < warmup.min_days:
        return Check("Warmup", FAIL, f"Tag {days} von {warmup.min_days}. Nur Warmup, keine Kaltakquise, "
                     f"bis noch {warmup.min_days - days} Tag(e) vergangen sind.")
    cap = warmup.daily_cap(ceiling, today)
    if cap <= 0 and warmup.daily_increase <= 0:
        return Check("Warmup", WARN, "Das Limit für Kaltakquise liegt heute bei 0 und wächst nicht. Erhöh `start_sends` "
                     "oder `daily_increase`.")
    if cap >= ceiling:
        ramp = "volles Volumen"
    elif warmup.daily_increase <= 0:
        ramp = f"bleibt bei {cap}/Tag (daily_increase ist 0)"
    else:
        full_on = warmup.min_days + -(-(ceiling - warmup.start_sends) // warmup.daily_increase)
        ramp = f"erreicht {ceiling}/Tag an Tag {full_on}"
    return Check("Warmup", OK, f"Tag {days}: Das Limit heute ist {cap}/Tag ({ramp}). "
                 "Lass den Warmup-Dienst neben Harvey weiterlaufen.")


# Warmup spam at or above this, over enough mail to judge, means the mailbox
# isn't ready whatever the day count says.
MAX_WARMUP_SPAM = 0.05
MIN_WARMUP_SAMPLE = 20


def check_warmup_service(health, warmup=None, *, error: str = "", other_service: bool = False,
                         today: date | None = None) -> Check:
    """Is the sending mailbox actually warmed? Read from TrulyInbox when it's connected."""
    from harvey.integrations.trulyinbox import AFFILIATE_TAG, AFFILIATE_URL
    if error:
        return Check("Warmup-Dienst", WARN, f"Konnte TrulyInbox nicht lesen: {error}")
    if health is None:
        if other_service:
            return Check("Warmup-Dienst", INFO, "Du nutzt einen anderen Warmup-Dienst. Schau dort "
                         "vor der Kaltakquise nach der Posteingangsquote.")
        return Check("Warmup-Dienst", INFO, "Nicht verbunden, Harvey sieht also nicht, ob dieses "
                     "Postfach aufgewärmt ist. Wir empfehlen TrulyInbox: Es wärmt das Postfach auf, "
                     f"hält es gesund, und Harvey liest hier die Posteingangsquote. {AFFILIATE_URL} "
                     f"{AFFILIATE_TAG} Dann führ aus: harvey trulyinbox connect")
    if not health.found:
        return Check("Warmup-Dienst", FAIL, f"{health.address} ist noch nicht in deinem "
                     "TrulyInbox-Konto. Füg das Postfach in TrulyInbox hinzu, damit das Warmup startet.")
    if not health.login_ok:
        return Check("Warmup-Dienst", FAIL, f"TrulyInbox kann sich nicht bei {health.address} "
                     "anmelden. Verbinde das Postfach in TrulyInbox neu.")
    if not health.warming:
        return Check("Warmup-Dienst", FAIL, f"Das Warmup ist für {health.address} nicht an. "
                     "Schalte es in TrulyInbox ein.")
    min_days = warmup.min_days if warmup is not None else 14
    days = (today or date.today()) - health.started_on if health.started_on else None
    days = days.days if days is not None else None
    hint = ""
    if health.started_on and warmup is not None and not warmup.start_date:
        hint = (f" Setz `channels.email.warmup.start_date: {health.started_on.isoformat()}`, "
                "damit Harvey die Kaltakquise ab da hochfährt.")
    rate = health.spam_rate
    if rate is not None and health.sent >= MIN_WARMUP_SAMPLE and rate >= MAX_WARMUP_SPAM:
        return Check("Warmup-Dienst", FAIL, f"{round(rate * 100)} % Spam im Warmup in den "
                     "letzten 14 Tagen. Wärm weiter auf und halt die Kaltakquise zurück, bis es unter "
                     f"{round(MAX_WARMUP_SPAM * 100)} % liegt." + hint)
    inbox = f", {round((1 - rate) * 100)} % im Posteingang in den letzten 14 Tagen" if rate is not None else ""
    if health.sent < MIN_WARMUP_SAMPLE:
        return Check("Warmup-Dienst", WARN, f"TrulyInbox hat in den letzten 14 Tagen {health.sent} "
                     "Warmup-Mails gesendet. Das reicht noch nicht für ein Urteil. Prüf, ob das Warmup "
                     "läuft, und wärm vor der Kaltakquise weiter auf." + hint)
    if days is None or days < min_days:
        shown = "wird aufgewärmt" if days is None else f"Tag {max(days, 0)} von {min_days}"
        return Check("Warmup-Dienst", FAIL, f"TrulyInbox: {shown}{inbox}. Wärm vor der "
                     "Kaltakquise weiter auf." + hint)
    return Check("Warmup-Dienst", OK, f"TrulyInbox: aufgewärmt, Tag {days}{inbox}. "
                 "Lass es neben Harvey weiterlaufen." + hint)


def check_send_history(total_sent: int, bounces: int) -> Check:
    if total_sent < MIN_SENDS_FOR_RATES:
        return Check("Bounce-Rate", INFO, f"{bounces} Bounces bei {total_sent} gesendet. Noch zu wenig für ein Urteil.")
    rate = bounces / total_sent
    status = OK if rate <= MAX_BOUNCE_RATE else FAIL
    return Check("Bounce-Rate", status, f"{rate:.1%} ({bounces}/{total_sent}). Halt sie unter {MAX_BOUNCE_RATE:.0%}.")

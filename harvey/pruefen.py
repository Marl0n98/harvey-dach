"""Checks the lead list so no value sits at the wrong company or person.

Three layers, cheapest first:

1. Rules (`harvey pruefen`, free, every row): does the address belong to the
   company's domain, does the contact own the address, is the same address,
   phone or person filed at several companies. Repeated until nothing changes,
   so the address that moves up after a removal is checked too.
2. Live (`harvey pruefen --live`): the company's own homepage, Impressum and
   contact page are read again; every address, phone and person published on
   the site has to appear there literally, and a site that now lands on another
   business loses everything read from it.
3. Agents (Claude Code, `/leads-pruefen` with the subagents in `.claude/agents/`):
   only the rows still in doubt go to two independent checkers; a value stays
   only when both confirm it. Their decisions come back with
   `harvey pruefen --anwenden`.

What a check removes is stored in the lead as `review` and is left out of the
list, the drafts and the contacts. Removals add up across runs: a later rule run
never brings back what the agents took out. Nothing is deleted from the stored
lead, so it can be looked at again.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from harvey import leads as L
from harvey.collectors import people as people_mod
from harvey.public_http import page_text, same_site

OK, UNSURE, FIXED = "ok", "unsicher", "korrigiert"


def _domain(company: dict, lead: dict) -> str:
    raw = (company.get("domain") or lead.get("site") or "").lower()
    raw = re.sub(r"^https?://", "", raw).split("/")[0]
    return raw.removeprefix("www.")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _empty() -> dict:
    return {"notes": [], "remove": {"emails": [], "contacts": [], "phones": [], "description": False}}


def _add(item: dict, note: str, **remove):
    if note and note not in item["notes"]:
        item["notes"].append(note)
    for key, value in remove.items():
        if key == "description":
            item["remove"]["description"] = item["remove"]["description"] or bool(value)
        else:
            for v in value if isinstance(value, list) else [value]:
                if v and v not in item["remove"][key]:
                    item["remove"][key].append(v)


def _with_removals(lead: dict, item: dict | None) -> dict:
    """The lead as the list would show it with these removals (and earlier ones) applied."""
    previous = (lead.get("review") or {}).get("remove") or {}
    merged = _merge_remove(previous, (item or _empty())["remove"])
    return {**lead, "review": {"remove": merged}}


def _merge_remove(a: dict, b: dict) -> dict:
    def as_list(rm, plural, single):
        return list(rm.get(plural) or []) + ([rm[single]] if rm.get(single) else [])
    return {
        "emails": sorted({e.lower() for e in as_list(a, "emails", "email") + as_list(b, "emails", "email")}),
        "contacts": sorted(set(as_list(a, "contacts", "contact") + as_list(b, "contacts", "contact"))),
        "phones": sorted(set((a.get("phones") or []) + (b.get("phones") or []))),
        "description": bool(a.get("description") or b.get("description")),
    }


def rule_findings(records: list[dict]) -> dict[str, dict]:
    """{company_id: {"notes", "remove"}} from the stored leads alone."""
    parsed = [(r, json.loads(r.get("lead") or "{}")) for r in records]
    found = {r["id"]: _empty() for r, _ in parsed}
    # Removals only grow, so this ends; the bound is just a guard.
    limit = 2 + sum(len(lead.get("emails") or []) + len(lead.get("people") or [])
                    + len(lead.get("site_phones") or []) for _, lead in parsed)
    for _ in range(limit):
        before = json.dumps(found, sort_keys=True)
        by_email, by_phone, by_person = defaultdict(set), defaultdict(set), defaultdict(set)
        rows = {}
        for r, lead in parsed:
            shown = _with_removals(lead, found[r["id"]])
            row, _ = L.rows_for(r, shown)
            rows[r["id"]] = row
            country = L.company_country(r, lead)
            if row["Beste E-Mail"]:
                by_email[row["Beste E-Mail"].lower()].add(r["id"])
            for p in [row["Telefon (Karte)"]] + [x.strip() for x in row["Telefon (Website)"].split(",")]:
                key = L.phone_key(p, country)
                if len(key) >= 7:
                    by_phone[key].add(r["id"])
            for p in L.review_people(shown):
                by_person[p.lower()].add(r["id"])
        for r, lead in parsed:
            item, row = found[r["id"]], rows[r["id"]]
            country = L.company_country(r, lead)
            domain = _domain(r, lead)
            email = (row["Beste E-Mail"] or "").lower()
            mail_domain = email.rpartition("@")[2]
            if domain:
                foreign = [e for e in lead.get("emails") or []
                           if e["email"].rpartition("@")[2] not in L.FREEMAIL
                           and not same_site(e["email"].rpartition("@")[2].lower(), domain)]
                from_site = [e["email"].lower() for e in foreign if e["source"] != L.SOURCE_MAP]
                from_map = [e["email"].lower() for e in foreign if e["source"] == L.SOURCE_MAP]
                if from_site:
                    _add(item, "Adresse von der Website gehört nicht zur Domain " + domain + ": "
                         + ", ".join(from_site), emails=from_site)
                if from_map:
                    # Same map entry as the company, but another domain than its website:
                    # one of the two may be outdated. A doubt for the agents.
                    _add(item, f"Adresse aus der Karte ({', '.join(from_map)}) passt nicht zur Website {domain}")
            if email and email not in item["remove"]["emails"] and len(by_email[email]) > 1:
                # Shared by several companies: one of them may be right (a sister
                # practice), so it is a doubt for the agents, not a removal.
                _add(item, f"Adresse {email} steht bei {len(by_email[email])} Firmen")
            contact = row["Ansprechpartner"]
            if contact and email and email not in item["remove"]["emails"]:
                people = L.review_people(_with_removals(lead, item), as_dicts=True)
                if not (L._person_behind(email, [{"name": contact}])
                        or L.is_business_inbox(email, people, r.get("name", ""), domain)):
                    _add(item, f"{contact} passt nicht sicher zu {email}", contacts=contact)
            if contact and len(by_person[contact.lower()]) > 1:
                _add(item, f"{contact} steht bei {len(by_person[contact.lower()])} Firmen")
            shared = [p for p in [row["Telefon (Karte)"]] + [x.strip() for x in row["Telefon (Website)"].split(",")]
                      if p and len(by_phone[L.phone_key(p, country)]) > 1]
            if shared:
                _add(item, "Telefon steht bei mehreren Firmen: " + ", ".join(shared))
            if email and not domain and mail_domain not in L.FREEMAIL:
                words = {w for w in re.findall(r"[a-z]{4,}", L._clean_name_part(r.get("name", "")) + " "
                                               + " ".join(L._clean_name_part(x) for x in (r.get("name") or "").split()))}
                if not any(w in mail_domain for w in words):
                    _add(item, f"Ohne Website: {email} passt nicht erkennbar zum Firmennamen")
            if domain:
                from harvey.collectors.discover import is_junk
                if is_junk(domain):
                    # The portal's or association's own addresses aren't the company's.
                    theirs = [e["email"].lower() for e in lead.get("emails") or []
                              if same_site(e["email"].rpartition("@")[2].lower(), domain)]
                    _add(item, f"Website {domain} ist ein Portal oder Verband, nicht die Seite der Firma",
                         description=True, emails=theirs, contacts=L.review_people(lead),
                         phones=list(lead.get("site_phones") or []))
            for note in (lead.get("review") or {}).get("notes") or []:
                # An address that moved up after the agents removed the one before:
                # a case again until someone has checked it.
                if note.startswith("Nachgerückt, noch ungeprüft: ") and email and note.endswith(email):
                    _add(item, note)
            if "leitet weiter" in (lead.get("site_note") or ""):
                _add(item, lead["site_note"])
        if json.dumps(found, sort_keys=True) == before:
            break
    return found


def _default_scout():
    """A Scout to read sites exactly the way `harvey leads` read them."""
    from harvey.agents.scout import Scout
    from harvey.brain import Brain
    from harvey.config import EnvConfig, load_config
    from harvey.state import StateManager
    config = load_config()
    scout = Scout(Brain(StateManager(), config.brain), StateManager(), config, EnvConfig())
    scout._site_reads, scout._site_evidence, scout._own_domains, scout._challenged = {}, {}, {}, set()
    return scout


async def _read_like_leads(scout, domain: str) -> tuple[list[str], str]:
    """(page texts, foreign host): homepage, the menu pages (team, über uns,
    kontakt) and Impressum, read the way the lead list was built."""
    site = await scout._read_site(domain)
    if site.get("redirected"):
        return [], site["redirected"]
    pages = [text for _, text in site["pages"]]
    if site["base"] and pages:
        seen = {people_mod._norm_url(u) for u, _ in site["pages"]} | set(site["tried"])
        for url in L.imprint_links(pages[0], site["base"]):
            if people_mod._norm_url(url) in seen:
                continue
            seen.add(people_mod._norm_url(url))
            resp = await scout._site_fetch(site, url)
            if resp is not None and same_site(_host(str(getattr(resp, "url", "") or url)), domain):
                pages.append(page_text(resp))
    return pages, ""


async def live_findings(records: list[dict], concurrency: int = 6, progress=None,
                        scout=None) -> dict[str, dict]:
    """Read each company's own pages again, the same pages the lead list came from;
    every value published there must still be there. A page that can't be read
    proves nothing and removes nothing."""
    scout = scout or _default_scout()
    gate = asyncio.Semaphore(max(1, concurrency))
    out: dict[str, dict] = {}
    done = 0

    async def one(r: dict):
        nonlocal done
        lead = json.loads(r.get("lead") or "{}")
        item = _empty()
        out[r["id"]] = item
        domain = _domain(r, lead)
        try:
            if not domain or not lead.get("site"):
                return          # nothing was read from a site, so nothing to check there
            async with gate:
                try:
                    pages, foreign = await _read_like_leads(scout, domain)
                finally:
                    scout._site_reads.pop(scout._host(domain), None)
            shown = _with_removals(lead, None)
            site_emails = [e["email"].lower() for e in lead.get("emails") or []
                           if e["source"] in (L.SOURCE_SITE, L.SOURCE_PATTERN)]
            if foreign:
                # It now lands on another business: nothing read from it is this company's.
                _add(item, f"Website leitet auf {foreign} weiter", emails=site_emails,
                     contacts=L.review_people(shown), phones=list(lead.get("site_phones") or []),
                     description=True)
                return
            if len(pages) < 1:
                _add(item, "Website ließ sich nicht erneut lesen")
                return
            plain = L._text(" ".join(pages), 3_000_000).lower()
            country = L.company_country(r, lead)
            on_site = {e.lower() for page in pages for e in people_mod.emails_on_page(page)}
            phones_now = {L.phone_key(p, country) for page in pages for p in L.phones_on_page(page)}
            gone = [e for e in lead.get("emails") or []
                    if e["source"] == L.SOURCE_SITE and e["email"].lower() not in on_site]
            # Missing on a fresh read is a doubt, not proof (a page can time out):
            # these rows go to the agents, nothing is removed here.
            if gone:
                _add(item, "Nicht auf den gelesenen Seiten gefunden: " + ", ".join(e["email"] for e in gone))
            row, _ = L.rows_for(r, shown)
            best = (row["Beste E-Mail"] or "").lower()
            if best and row["Quelle"] == L.SOURCE_MAP and on_site and best not in on_site:
                # The map's address isn't on the site, but others are: maybe a typo or an old one.
                _add(item, f"{best} (aus der Karte) steht nicht auf der Website, dort stehen andere Adressen")
            lost = [p for p in lead.get("site_phones") or [] if L.phone_key(p, country) not in phones_now]
            if lost:
                _add(item, "Telefon nicht auf den gelesenen Seiten gefunden: " + ", ".join(lost))
            for name in L.review_people(shown):
                last = name.split()[-1].lower()
                if not re.search(rf"(?<![a-zäöüß]){re.escape(last)}(?![a-zäöüß])", plain):
                    _add(item, f"{name} nicht auf den gelesenen Seiten gefunden")
        finally:
            done += 1
            if progress and done % 25 == 0:
                progress(done, len(records))

    await asyncio.gather(*(one(r) for r in records))
    return out


def merge(*findings: dict[str, dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for f in findings:
        for cid, item in f.items():
            m = merged.setdefault(cid, _empty())
            for note in item["notes"]:
                _add(m, note)
            _add(m, "", **item["remove"])
    return merged


def _has_removals(rm: dict) -> bool:
    return bool(rm.get("emails") or rm.get("contacts") or rm.get("phones") or rm.get("description")
                or rm.get("email") or rm.get("contact"))


async def load_records(state, limit: int = 0) -> list[dict]:
    async with state._connect() as db:
        db.row_factory = __import__("aiosqlite").Row
        sql = ("SELECT c.*, l.data AS lead FROM companies c JOIN leads l ON l.company_id = c.id "
               "ORDER BY c.location, c.name")
        if limit:
            sql += f" LIMIT {int(limit)}"
        async with db.execute(sql) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def store_reviews(state, records: list[dict], findings: dict[str, dict], by: str) -> dict:
    """Add this run's findings to each lead's review. Earlier removals stay."""
    counts = {OK: 0, UNSURE: 0, FIXED: 0}
    for r in records:
        item = findings.get(r["id"])
        if item is None:
            continue
        lead = json.loads(r.get("lead") or "{}")
        old = lead.get("review") or {}
        remove = _merge_remove(old.get("remove") or {}, item["remove"])
        by_agents = str(old.get("by") or "").startswith("agenten")
        settled = set(old.get("settled") or [])          # doubts the agents already decided
        doubt = [n for n in item["notes"] if n not in settled]
        # An open doubt without a removal is "unsicher" (for the agents), whatever
        # was removed before; otherwise removals make it "korrigiert".
        status = UNSURE if doubt and not _has_removals(item["remove"]) else (
            FIXED if _has_removals(remove) else OK)
        notes = list(dict.fromkeys((old.get("notes") or []) + doubt)) if by_agents else doubt
        lead["review"] = {"status": status, "notes": notes, "remove": remove,
                          "by": "agenten+" + by if by_agents else by, "settled": sorted(settled),
                          "on": date.today().isoformat()}
        counts[status] += 1
        await L.save_lead(state, r["id"], lead)
    return counts


def open_cases(records: list[dict], findings: dict[str, dict]) -> list[dict]:
    """The rows for the agents: everything a rule or the live check doubted."""
    cases = []
    for r in records:
        item = findings.get(r["id"]) or {}
        lead = json.loads(r.get("lead") or "{}")
        settled = set((lead.get("review") or {}).get("settled") or [])
        notes = [n for n in item.get("notes") or [] if n not in settled]
        if not notes:
            continue          # nothing new, or only doubts the agents already decided
        row, _ = L.rows_for(r, _with_removals(lead, item))
        cases.append({
            "company_id": r["id"], "firma": row["Firma"], "website": row["Website"],
            "ort": row["Ort"], "email": row["Beste E-Mail"], "email_quelle": row["Quelle"],
            "ansprechpartner": row["Ansprechpartner"],
            "rolle": row["Rolle"],
            "telefon_website": [p.strip() for p in row["Telefon (Website)"].split(",") if p.strip()],
            "telefon_karte": row["Telefon (Karte)"], "hinweise": notes,
        })
    return cases


_VERDICTS = {"behalten", "entfernen"}


def _validate(decisions) -> list[dict]:
    if not isinstance(decisions, list):
        raise ValueError("Die Entscheidungen müssen eine JSON-Liste sein.")
    clean = []
    for i, d in enumerate(decisions, start=1):
        if not isinstance(d, dict) or not isinstance(d.get("company_id"), str):
            raise ValueError(f"Eintrag {i}: braucht ein Objekt mit company_id (Text).")
        for key in ("email", "ansprechpartner"):
            if d.get(key) is not None and (not isinstance(d[key], str) or d[key] not in _VERDICTS):
                raise ValueError(f"Eintrag {i}: {key} muss 'behalten' oder 'entfernen' sein.")
        phones = d.get("telefon") or {}
        if not isinstance(phones, dict) or any(not isinstance(v, str) or v not in _VERDICTS
                                               for v in phones.values()):
            raise ValueError(f"Eintrag {i}: telefon muss {{nummer: 'behalten'|'entfernen'}} sein.")
        clean.append(d)
    return clean


async def apply_decisions(state, path: Path) -> dict:
    """Decisions from the agents: [{"company_id", "email": "behalten|entfernen",
    "ansprechpartner": ..., "telefon": {nummer: ...}, "begruendung"}]. All entries are
    checked before anything is written; removals add to what earlier checks removed."""
    decisions = _validate(json.loads(Path(path).read_text(encoding="utf-8")))
    by_id: dict[str, dict] = {}
    for d in decisions:          # several entries for one company add up
        m = by_id.setdefault(d["company_id"], {"company_id": d["company_id"], "telefon": {}, "begruendung": ""})
        for key in ("email", "ansprechpartner"):
            if d.get(key) == "entfernen" or (d.get(key) and not m.get(key)):
                m[key] = d[key]
        m["telefon"].update({k: v for k, v in (d.get("telefon") or {}).items() if v == "entfernen" or k not in m["telefon"]})
        if d.get("begruendung"):
            m["begruendung"] = (m["begruendung"] + " " + str(d["begruendung"])).strip()
    counts = {"behalten": 0, "entfernt": 0}
    records = {r["id"]: r for r in await load_records(state)}
    for d in by_id.values():
        r = records.get(d["company_id"])
        if not r:
            continue
        lead = json.loads(r.get("lead") or "{}")
        row, _ = L.rows_for(r, lead)
        new = _empty()["remove"]
        if d.get("email") == "entfernen" and row["Beste E-Mail"]:
            new["emails"] = [row["Beste E-Mail"].lower()]
        if d.get("ansprechpartner") == "entfernen" and row["Ansprechpartner"]:
            new["contacts"] = [row["Ansprechpartner"]]
        new["phones"] = [p for p, verdict in (d.get("telefon") or {}).items() if verdict == "entfernen"]
        old = lead.get("review") or {}
        remove = _merge_remove(old.get("remove") or {}, new)
        notes = list(old.get("notes") or [])
        if d.get("begruendung"):
            notes.append(f"Agenten: {str(d['begruendung']).strip()}")
        settled = sorted(set(old.get("settled") or []) | set(old.get("notes") or []))
        status = FIXED if _has_removals(remove) else OK
        if new["emails"]:
            after, _ = L.rows_for(r, {**lead, "review": {"remove": remove}})
            if after["Beste E-Mail"]:
                # The next address moves up, and nobody has checked it yet.
                notes.append(f"Nachgerückt, noch ungeprüft: {after['Beste E-Mail']}")
                status = UNSURE
        lead["review"] = {"status": status, "notes": notes,
                          "remove": remove, "by": "agenten", "settled": settled,
                          "on": date.today().isoformat()}
        counts["entfernt" if _has_removals(new) else "behalten"] += 1
        await L.save_lead(state, r["id"], lead)
    return counts

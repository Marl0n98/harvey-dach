"""Fixes found by the recorded demo run."""

import logging
from types import SimpleNamespace

import pytest

from harvey import leads
from harvey.agents import scout as scout_mod
from harvey.agents.scout import SMTP_VERDICT_STATUS, Scout
from harvey.brain import Brain
from harvey.public_http import page_text


def test_mail_server_verdicts_match_the_lead_list_labels():
    assert set(SMTP_VERDICT_STATUS) == {leads.OK, leads.BAD}
    assert SMTP_VERDICT_STATUS[leads.OK] == "verified"


class FakeChecker:
    def __init__(self, verdict):
        self.verdict, self.asked = verdict, []

    async def check(self, domain, emails):
        self.asked.append((domain, emails))
        return {"results": {e: self.verdict for e in emails}}


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, status", [
    (leads.OK, "verified"), (leads.CATCH_ALL, "guess"), (leads.MS_UNSURE, "guess"),
    (leads.BAD, "invalid"), (leads.UNKNOWN, "guess")])
async def test_a_guess_is_checked_with_the_mail_server(monkeypatch, verdict, status):
    monkeypatch.setenv("HARVEY_SMTP_CHECK", "1")
    s = Scout.__new__(Scout)
    s._mailbox_checker = FakeChecker(verdict)
    assert await s._smtp_status("x.test", "eva@x.test", "guess") == status


@pytest.mark.asyncio
async def test_only_guesses_are_asked_and_the_switch_turns_it_off(monkeypatch):
    s = Scout.__new__(Scout)
    s._mailbox_checker = checker = FakeChecker(leads.OK)
    monkeypatch.setenv("HARVEY_SMTP_CHECK", "1")
    assert await s._smtp_status("x.test", "a@x.test", "risky") == "risky"
    monkeypatch.setenv("HARVEY_SMTP_CHECK", "0")
    assert await s._smtp_status("x.test", "a@x.test", "guess") == "guess"
    assert checker.asked == []


def test_utf8_under_a_latin1_header_reads_as_utf8():
    raw = "Fachpraxis für Physiotherapie".encode("utf-8")
    resp = SimpleNamespace(content=raw, encoding="ISO-8859-1", text=raw.decode("latin-1"))
    assert page_text(resp) == "Fachpraxis für Physiotherapie"
    real_latin1 = "für".encode("latin-1")
    resp = SimpleNamespace(content=real_latin1, encoding="ISO-8859-1", text="für")
    assert page_text(resp) == "für"
    assert page_text(SimpleNamespace(text="nur text")) == "nur text"


def test_merge_tags_and_untrained_skills_dont_warn(caplog):
    brain = Brain.__new__(Brain)
    brain.config = SimpleNamespace(language="de")
    filled = {k: "x" for k in ("persona_company", "persona_name", "persona_role", "persona_tone",
                               "product_benefits", "product_description", "product_name",
                               "product_pricing")}
    with caplog.at_level(logging.WARNING):
        brain.load_prompt("writer", **filled)
        brain.load_skill("no_such_trained_skill_xyz")
    assert "unfilled variables" not in caplog.text
    assert "no_such_trained_skill_xyz" in caplog.text      # real missing skills still warn
    caplog.clear()
    import harvey.brain as brain_mod
    brain_mod.TRAINED_SKILLS.add("no_such_trained_skill_xyz")
    try:
        with caplog.at_level(logging.WARNING):
            brain.load_skill("no_such_trained_skill_xyz")
        assert caplog.text == ""
    finally:
        brain_mod.TRAINED_SKILLS.discard("no_such_trained_skill_xyz")


def test_german_greeting_rule_prefers_frau_or_herr():
    from harvey.brain import GERMAN_RULES
    assert "Herr Dr. Nachname" in GERMAN_RULES and "operator's email rules" in GERMAN_RULES


def test_job_and_department_words_are_not_people():
    from harvey.collectors.people import is_person_name
    assert not is_person_name("Teamleitung Bilanzierung")
    assert not is_person_name("Offene Stellen")
    assert is_person_name("Anna Jung") and is_person_name("Peter Hartung")


def test_excel_contact_is_the_owner_of_the_best_address():
    lead = {"people": [{"name": "Martina Lindner", "title": "Inhaberin"}],
            "emails": [{"email": "petra@gmail.com", "source": "Website",
                        "person": "Petra Sommerfeld", "check": leads.OK}]}
    row, _ = leads.rows_for({"name": "Praxis"}, lead)
    assert row["Ansprechpartner"] == "Petra Sommerfeld" and row["Rolle"] == ""
    lead["emails"] = [{"email": "office@p.test", "source": "Website", "person": "", "check": leads.OK}]
    row, _ = leads.rows_for({"name": "Praxis"}, lead)
    assert row["Ansprechpartner"] == "Martina Lindner"


def test_german_setups_get_a_german_sender_notice_not_can_spam():
    from harvey.config import ComplianceConfig, compliance_notice
    cfg = SimpleNamespace(channels=SimpleNamespace(email=SimpleNamespace(enabled=True)),
                          compliance=ComplianceConfig(postal_address="", opt_out_line=""),
                          brain=SimpleNamespace(language="de"))
    text = compliance_notice(cfg)
    assert "CAN-SPAM" not in text and "Anschrift" in text and "compliance.postal_address" in text
    cfg.brain.language = "en"
    assert "CAN-SPAM" in compliance_notice(cfg)


def test_untrusted_text_never_becomes_an_excel_formula(tmp_path):
    from openpyxl import load_workbook
    rows = [{"Firma": "=HYPERLINK(\"http://evil\")", "Telefon": "+43 316 123"}]
    leads.to_xlsx(tmp_path / "x.xlsx", [("Firmen", ["Firma", "Telefon"], rows)])
    sheet = load_workbook(tmp_path / "x.xlsx")["Firmen"]
    assert sheet["A2"].data_type == "s" and sheet["A2"].value.startswith("=HYPERLINK")
    assert sheet["B2"].value == "+43 316 123"
    assert leads.to_csv(rows, ["Firma", "Telefon"]).splitlines()[1].startswith("\"'=HYPERLINK")


def test_umlaut_domains_and_enhanced_codes():
    assert leads.to_ascii_address("eva@müller-bau.de") == "eva@xn--mller-bau-q9a.de"
    assert not leads.user_unknown(550, "5.7.1 Recipient address rejected: Access denied")
    assert leads.user_unknown(550, "5.1.1 <x@y.de>: Recipient address rejected: User unknown")
    assert not leads.user_unknown(550, "Recipient address rejected: client host blocked")
    assert leads.user_unknown(550, "No such user here")


@pytest.mark.asyncio
async def test_a_dns_failure_is_not_a_missing_mail_server(monkeypatch):
    async def broken(domain):
        return "", "error"
    monkeypatch.setattr(leads, "mx_lookup", broken)
    out = await leads.MailboxChecker().check("x.test", ["a@x.test"])
    assert out["results"] == {"a@x.test": leads.UNKNOWN}


@pytest.mark.asyncio
async def test_checking_stops_when_port_25_is_blocked(monkeypatch):
    async def mx(domain):
        return f"mx.{domain}", "ok"
    monkeypatch.setattr(leads, "mx_lookup", mx)
    checker = leads.MailboxChecker()
    async def no_connection(mx, domain, emails, out):
        out["note"] = "keine Verbindung"
        out["results"] = {e: leads.UNKNOWN for e in emails}
    checker._smtp = no_connection
    for i in range(leads.PORT25_FAILS_TO_STOP):
        await checker.check(f"d{i}.test", ["a@x"])
    assert not checker.enabled and "Port 25" in checker.stopped


@pytest.mark.asyncio
async def test_long_candidate_lists_get_a_second_session(monkeypatch):
    async def mx(domain):
        return "mx.x.test", "ok"
    monkeypatch.setattr(leads, "mx_lookup", mx)
    checker, seen = leads.MailboxChecker(), []
    async def fine(mx, domain, emails, out):
        seen.append(len(emails))
        for e in emails:
            out["results"][e] = leads.OK
    checker._smtp = fine
    emails = [f"p{i}@x.test" for i in range(15)]
    out = await checker.check("x.test", emails)
    assert seen == [10, 5] and all(v == leads.OK for v in out["results"].values())


def test_a_question_mode_draft_that_pitches_is_caught():
    from harvey.agents.writer import pitch_problem
    cfg = SimpleNamespace(product=SimpleNamespace(
        name="NFY Brand", pricing="499 Euro", offer=SimpleNamespace(booking_url="https://cal.com/x")))
    assert pitch_problem("Wie finden neue Patienten zu Ihnen?", cfg) == ""
    assert "link" in pitch_problem("Schauen Sie mal: www.nfy.de", cfg)
    assert "product" in pitch_problem("Mit NFY Brand geht das schneller.", cfg)
    assert "price" in pitch_problem("Ab 499 Euro im Monat.", cfg)
    assert "asks" in pitch_problem("Hätten Sie Zeit für ein kurzes Gespräch?", cfg)


def test_dach_names_with_diacritics_are_people():
    from harvey.collectors.people import is_person_name
    for name in ("Ivana Kovačević", "Mehmet Yılmaz", "Łukasz Nowak", "Ömer Çelik"):
        assert is_person_name(name), name


def test_chamber_filter_no_longer_hits_lookalikes():
    from harvey.collectors.discover import is_junk
    assert is_junk("ihk-muenchen.de") and is_junk("amtsgericht-koeln.de") and is_junk("wko.at")
    assert not is_junk("bayrak-transport.de") and not is_junk("gerichtsdolmetscher-meier.de")


@pytest.mark.asyncio
async def test_fork_schema_lives_outside_upstream_migrations(tmp_path):
    import aiosqlite
    from harvey.state import MIGRATIONS, StateManager
    path = str(tmp_path / "old.db")
    s = StateManager(path)
    await s.init_db()
    async with aiosqlite.connect(path) as db:      # what an earlier Harvey DACH left behind
        await db.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
        await db.commit()
    await StateManager(path).init_db()
    async with aiosqlite.connect(path) as db:
        (version,) = await (await db.execute("PRAGMA user_version")).fetchone()
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(companies)")).fetchall()}
        leads_table = await (await db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='leads'")).fetchone()
    assert version == len(MIGRATIONS)
    assert {"email", "address"} <= cols and leads_table


@pytest.mark.parametrize("geography, language, held", [
    (["München", "Wien"], "en", True), (["Bayern"], "en", True), (["Österreich"], "en", True),
    (["Zürich, Schweiz"], "en", True), ([], "de", True), ([], "en", False),
    (["Austin, TX"], "de", False), (["London, United Kingdom"], "en", False)])
def test_consent_hold_holds_com_and_gmail_addresses_aimed_at_dach(geography, language, held):
    from harvey.agents.sender import consent_hold
    from harvey.config import ComplianceConfig
    cfg = SimpleNamespace(compliance=ComplianceConfig(consent_required_countries=["DE", "AT", "CH"]),
                          icp=SimpleNamespace(geography=geography),
                          brain=SimpleNamespace(language=language))
    for address in ("info@baeckerei-mueller.com", "eva.huber@gmail.com"):
        assert bool(consent_hold(cfg, address, "")) is held


def test_instantly_uploads_respect_the_consent_hold():
    from harvey.agents.sender import Sender
    from harvey.config import ComplianceConfig
    s = Sender.__new__(Sender)
    s.config = SimpleNamespace(compliance=ComplianceConfig(consent_required_countries=["DE"]),
                               icp=SimpleNamespace(geography=[]), brain=SimpleNamespace(language="de"),
                               channels=SimpleNamespace(email=SimpleNamespace()))
    p = SimpleNamespace(email="eva@praxis.de", first_name="Eva", last_name="Huber", company="P",
                        title="", personalization_notes="", country="")
    assert s._reviewed_leads([p]) == []

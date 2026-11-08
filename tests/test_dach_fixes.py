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


def test_a_personal_gmail_without_a_name_is_no_inbox_for_the_owner():
    lead = {"people": [{"name": "Martina Lindner", "title": "Inhaber"}],
            "emails": [{"email": "petra.sommerfeld@gmail.com", "source": "Website",
                        "person": "", "check": leads.OK}]}
    row, _ = leads.rows_for({"name": "Praxis Linde"}, lead)
    assert row["Ansprechpartner"] == "" and row["Beste E-Mail"] == "petra.sommerfeld@gmail.com"
    lead["emails"][0]["email"] = "m.lindner@praxis-linde.at"
    row, _ = leads.rows_for({"name": "Praxis Linde"}, lead)
    assert row["Ansprechpartner"] == "Martina Lindner"
    lead["emails"][0]["email"] = "office@praxis-linde.at"
    row, _ = leads.rows_for({"name": "Praxis Linde"}, lead)
    assert row["Ansprechpartner"] == "Martina Lindner"


def test_contacts_reuse_addresses_the_lead_list_confirmed():
    from harvey.agents.scout import _confirmed_for
    confirmed = [{"email": "graz@gze.at", "person": "", "check": leads.OK},
                 {"email": "c.huber@gze.at", "person": "", "check": leads.OK},
                 {"email": "anna@gze.at", "person": "Anna Berg", "check": leads.OK}]
    assert _confirmed_for(confirmed, "Anna Berg") == "anna@gze.at"
    assert _confirmed_for(confirmed, "Carmen Huber") == "c.huber@gze.at"
    assert _confirmed_for(confirmed, "Otto Brandl") == ""


def test_mojibake_from_the_site_itself_is_repaired():
    assert leads._text("Fachpraxis f&Atilde;&frac14;r Physiotherapie") == "Fachpraxis für Physiotherapie"
    assert leads._text("Café für Größe") == "Café für Größe"


def test_outbox_times_are_local_and_labelled():
    from harvey.cli import _local_time
    out = _local_time("2026-11-08T14:42:00")
    assert out[:4] == "2026" and len(out) > 16


def test_business_inboxes_beyond_the_role_list():
    people = [{"name": "Carmen Brandstetter"}]
    assert leads.is_role_address("therapie@physiozentrum-nord.at", people)
    assert leads.is_role_address("graz@praxis-am-park.at", people)
    assert not leads.is_role_address("carmen@praxis-am-park.at", people)
    assert not leads.is_role_address("c.brandstetter@praxis-am-park.at", people)
    assert not leads.is_role_address("petra.sommerfeld@gmail.com", people)
    lead = {"people": [{"name": "Carmen Brandstetter", "title": ""}],
            "emails": [{"email": "graz@praxis-am-park.at", "source": "Website", "person": "", "check": leads.OK}]}
    row, _ = leads.rows_for({"name": "Auster"}, lead)
    assert row["Ansprechpartner"] == ""     # graz@ could be anyone's: no person guessed
    lead["emails"][0]["email"] = "therapie@praxis-am-park.at"
    row, _ = leads.rows_for({"name": "Auster"}, lead)
    assert row["Ansprechpartner"] == "Carmen Brandstetter"


def test_name_shaped_addresses_never_get_someone_elses_name():
    lead = {"people": [{"name": "Anna Huber", "title": "Inhaberin"}],
            "emails": [{"email": "j.schmidt@firma.de", "source": "Website", "person": "", "check": leads.OK}]}
    row, _ = leads.rows_for({"name": "Firma", "domain": "firma.de"}, lead)
    assert row["Ansprechpartner"] == ""
    lead["emails"][0]["email"] = "carmen@firma.de"
    row, _ = leads.rows_for({"name": "Firma", "domain": "firma.de"}, lead)
    assert row["Ansprechpartner"] == ""


def test_the_business_own_freemail_address_is_kept():
    assert leads._names_the_business("praxis.huber@gmx.de", "Physio Huber", "physio-huber.at")
    assert leads._names_the_business("physio-huber@t-online.de", "Physio Huber", "physio-huber.at")
    assert not leads._names_the_business("tom.design@gmx.de", "Physio Huber", "physio-huber.at")


# ── attribution: data must never land on the wrong company or person ──

def test_two_people_with_one_surname_share_no_guessed_address():
    assert leads._person_behind("hmueller@x.de", [{"name": "Hans Müller"}, {"name": "Heike Müller"}]) == ""
    assert leads._person_behind("hmueller@x.de", [{"name": "Hans Müller"}, {"name": "Eva Berg"}]) == "Hans Müller"
    hans = {"first_name": "Hans", "last_name": "Müller"}
    heike = {"first_name": "Heike", "last_name": "Müller"}
    shared = leads.person_patterns(hans, "x.de") & leads.person_patterns(heike, "x.de")
    assert "hmueller@x.de" in shared and "mueller@x.de" in shared


def test_confirmed_lead_address_needs_one_clear_owner():
    from harvey.agents.scout import _confirmed_for
    confirmed = [{"email": "h.mueller@x.de", "person": "", "check": leads.OK}]
    assert _confirmed_for(confirmed, "Heike Müller", ["Hans Müller", "Heike Müller"]) == ""
    assert _confirmed_for(confirmed, "Heike Müller", ["Heike Müller", "Eva Berg"]) == "h.mueller@x.de"


def test_same_site_only_for_the_same_name():
    from harvey.public_http import same_site
    assert same_site("praxis-huber.de", "praxis-huber.de")
    assert same_site("praxis-huber.de", "praxis-huber.at")
    assert not same_site("fremd-praxis.de", "praxis-alt.de")
    assert not same_site("mvz-zentrale.de", "praxis-alt.de")


def test_batch_template_never_names_one_prospect():
    from harvey.agents.writer import batch_leak
    steps = [SimpleNamespace(subject="Frage", body="Wie läuft es bei Physio Lindenhof mit Terminen?")]
    ps = [SimpleNamespace(company="Physio Lindenhof", last_name="Huber", email="a@physio-lindenhof.at"),
          SimpleNamespace(company="Praxis Berg", last_name="Berg", email="b@berg.at")]
    assert batch_leak(steps, ps) == "physio lindenhof"
    steps = [SimpleNamespace(subject="Frage", body="Wie läuft es bei {{company}} mit Terminen?")]
    assert batch_leak(steps, ps) == ""


def test_impressum_skips_the_web_agency_and_the_chamber():
    from harvey.collectors.people import people_from_impressum
    html = ("<p>Physio Huber GmbH</p><p>Geschäftsführer: Anna Huber</p><p>Webdesign</p>"
            "<p>XY Media GmbH, Geschäftsführer: Tim Meier</p>"
            "<p>Zuständige Kammer: Ärztekammer, vertreten durch den Präsidenten Dr. Karl Groß</p>")
    assert [p["last_name"] for p in people_from_impressum(html)] == ["Huber"]


def test_a_second_business_on_a_known_website_is_not_merged():
    from harvey.collectors.discover import _same_business, is_junk
    assert _same_business("Physio Huber", "Physiotherapie Huber GmbH")
    assert not _same_business("Physio Huber", "Praxis Berg")
    assert all(is_junk(d) for d in ("jameda.de", "doctolib.de", "herold.at", "gelbeseiten.de"))


@pytest.mark.parametrize("html, owners", [
    # Right after a credit, nobody is taken: missing an owner is safe, a stranger isn't.
    ("<p>Fotos: Studio Licht</p><p>Geschäftsführer: Max Muster</p>", []),
    ("<p>Webdesign: XY Media, Geschäftsführer: Tim Meier</p><p>Inhaberin: Eva Berg</p>", []),
    ("<p>Webdesign &amp; Umsetzung:</p><p>Pixelwerk Mediendesign</p><p>Inhaber: Thomas Weber</p>", []),
    ("<p>Webdesign: Agentur Blau</p><p>Vertreten durch:</p><p>Tim Blau</p>", []),
    ("<p>Zuständige Kammer:</p><p>Zahnärztekammer Nordrhein</p><p>Vertreten durch: Ralf Hausweiler</p>", []),
    ("<p>Webdesign: XY</p><p>x</p><p>y</p><p>z</p><p>Impressum</p><p>Inhaberin: Eva Berg</p>", ["Berg"]),
    ("<p>© 2024 Praxis Huber, Inhaber: Paul Huber</p>", ["Huber"]),
])
def test_impressum_owner_survives_nearby_credits(html, owners):
    from harvey.collectors.people import people_from_impressum
    assert [p["last_name"] for p in people_from_impressum(html)] == owners


def test_subdomains_and_job_sites_count_as_the_same_business():
    from harvey.public_http import is_ats_host, same_site
    assert same_site("jobs.firma.de", "firma.de") and same_site("firma.de", "home.firma.de")
    assert is_ats_host("firma.jobs.personio.de") and not is_ats_host("fremd.de")


def test_batch_leak_ignores_surnames_that_are_words():
    from harvey.agents.writer import batch_leak
    steps = [SimpleNamespace(subject="Kurz gefragt", body="Ganz kurz: wie läuft es bei {{company}}?")]
    ps = [SimpleNamespace(company="Praxis Kurz", last_name="Kurz", email="a@praxis-kurz.at")]
    assert batch_leak(steps, ps) == ""


def test_a_longer_surname_is_someone_else():
    assert leads._person_behind("a.bergmann@firma.de", [{"name": "Anna Berg"}]) == ""
    assert leads._person_behind("k.wolfram@firma.de", [{"name": "Klaus Wolf"}]) == ""
    assert leads._person_behind("a.berg@firma.de", [{"name": "Anna Berg"}]) == "Anna Berg"


def test_platform_roots_and_person_inboxes():
    from harvey.public_http import same_site
    assert not same_site("jimdosite.com", "praxis-huber.jimdosite.com")
    assert not same_site("co.at", "firma.co.at")
    assert not leads.is_business_inbox("team-sabine@firma.de", [], "Firma", "firma.de")
    assert not leads.is_business_inbox("sekretariat.mayer@firma.de", [], "Firma", "firma.de")
    assert leads.is_business_inbox("praxis.huber@huber.de", [], "Praxis Huber", "huber.de")
    assert leads.is_business_inbox("therapie@physiozentrum-nord.at", [], "Physiozentrum Nord", "physiozentrum-nord.at")

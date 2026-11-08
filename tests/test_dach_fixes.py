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
    assert set(SMTP_VERDICT_STATUS) == {leads.OK, leads.CATCH_ALL, leads.MS_UNSURE, leads.BAD}
    assert SMTP_VERDICT_STATUS[leads.OK] == "verified"


class FakeChecker:
    def __init__(self, verdict):
        self.verdict, self.asked = verdict, []

    async def check(self, domain, emails):
        self.asked.append((domain, emails))
        return {"results": {e: self.verdict for e in emails}}


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, status", [
    (leads.OK, "verified"), (leads.CATCH_ALL, "risky"), (leads.BAD, "invalid"),
    (leads.UNKNOWN, "guess")])
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

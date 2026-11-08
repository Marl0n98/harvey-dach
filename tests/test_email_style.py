"""Your own email style (writer.email_rules) in question mode and in replies.

Question mode used to throw the operator's rules away for the first email,
and replies never saw them. Now the rules decide wording, length, greeting
and sign-off everywhere; question mode still offers nothing.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from harvey.agents.handler import Handler
from harvey.agents.writer import QUESTION_CORE, QUESTION_STYLE, Writer
from harvey.config import ComplianceConfig
from harvey.models.prospect import Prospect
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager

RULES = "Schreib wie ich: Hallo Frau Huber, kurze Frage. Schluss nur mit Lisa."


class RecordingBrain:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def load_skills_for_agent(self, name):
        return ""

    def load_prompt(self, name, **kw):
        return "prompt"

    async def think_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.reply


def config(rules_path="", first_touch="question"):
    return SimpleNamespace(
        channels=SimpleNamespace(email=SimpleNamespace(
            provider="gmail", require_approval=True, first_touch=first_touch)),
        product=SimpleNamespace(name="P", description="D", key_benefits=[], pricing="",
                                objection_responses={}),
        persona=SimpleNamespace(name="Lisa", company="Beispiel GmbH", role="R", tone="t"),
        writer=SimpleNamespace(humanizer=False, email_rules=rules_path),
        compliance=ComplianceConfig(),
    )


@pytest_asyncio.fixture
async def state(tmp_path):
    s = StateManager(str(tmp_path / "s.db"))
    await s.init_db()
    return s


async def a_prospect(state):
    pid = await state.add_prospect(Prospect(
        first_name="Eva", last_name="Huber", title="Inhaberin", company="Huber Physio",
        email="eva@huber.test", email_status="verified"))
    return await state.get_prospect(pid)


@pytest.mark.asyncio
async def test_question_mode_follows_your_style_and_still_offers_nothing(state):
    brain = RecordingBrain({"subject": "s", "body": "b"})
    w = Writer(brain, state, config())
    w.skills, w.email_rules = "", RULES
    await w._write_personal_email(await a_prospect(state))
    prompt = brain.prompts[0]
    assert RULES in prompt
    assert QUESTION_CORE in prompt            # no offer, no posing, one email
    assert QUESTION_STYLE not in prompt       # your wording replaces the built-in one
    assert "never from the examples" in prompt


@pytest.mark.asyncio
async def test_question_mode_without_style_bans_the_generic_opening(state):
    brain = RecordingBrain({"subject": "s", "body": "b"})
    w = Writer(brain, state, config())
    w.skills, w.email_rules = "", ""
    await w._write_personal_email(await a_prospect(state))
    prompt = brain.prompts[0]
    assert QUESTION_CORE in prompt and QUESTION_STYLE in prompt
    assert "never a general statement about their industry" in prompt


@pytest.mark.asyncio
async def test_question_sequence_uses_your_style_too(state):
    brain = RecordingBrain([{"step": 1, "subject": "s", "body": "b {{company}}", "delay_days": 0}])
    w = Writer(brain, state, config())
    w.skills, w.email_rules = "", RULES
    steps = await w._write_sequence([await a_prospect(state)])
    assert len(steps) == 1
    assert RULES in brain.prompts[0] and QUESTION_CORE in brain.prompts[0]


def handler_with(cfg):
    h = Handler.__new__(Handler)   # no mail provider needed to read the style
    h.config, h.email_rules = cfg, None
    return h


def test_replies_read_your_style_once(tmp_path):
    f = tmp_path / "stil.md"
    f.write_text(RULES, encoding="utf-8")
    h = handler_with(config(str(f)))
    assert h._email_rules() == RULES
    f.write_text("geändert", encoding="utf-8")
    assert h._email_rules() == RULES      # read on the first reply, not every time


def test_a_broken_style_file_costs_replies_only_the_style(tmp_path):
    h = handler_with(config(str(tmp_path / "fehlt.md")))
    assert h._email_rules() == ""


def test_no_style_means_no_style_block():
    assert handler_with(config(""))._email_rules() == ""


def test_the_shipped_example_is_usable_german_and_has_both_mails():
    text = (PROJECT_ROOT / "skills" / "email_rules.beispiel.md").read_text(encoding="utf-8")
    assert "## Erste Mail" in text and "## Antwort" in text and "## Nie" in text
    assert "—" not in text and " – " not in text


@pytest.mark.asyncio
async def test_preview_falls_back_to_named_people_in_the_lead_list(state):
    import json
    from harvey.leads import sample_prospects
    from harvey.models import Company
    cid = await state.add_company(Company(name="Huber Physio", domain="huber.test"))
    lead = {"description": "Physiotherapie in Graz", "people": [{"name": "Eva Huber", "title": "Inhaberin"}],
            "emails": [{"email": "eva@huber.test", "source": "Website", "person": "Eva Huber",
                        "check": "nicht geprüft"}], "booking": "", "site": "https://huber.test",
            "careers": True}
    async with state._connect() as db:
        await db.execute("INSERT INTO leads (company_id, data, checked_at) VALUES (?, ?, '')",
                         (cid, json.dumps(lead)))
        await db.commit()
    [p] = await sample_prospects(state, 3)
    assert (p.first_name, p.last_name, p.email, p.company_id) == ("Eva", "Huber", "eva@huber.test", cid)
    assert "Physiotherapie in Graz" in p.personalization_notes
    assert "No online booking" in p.personalization_notes and "jobs" in p.personalization_notes
    assert await state.get_prospects_by_status("new") == []   # a preview saves nothing

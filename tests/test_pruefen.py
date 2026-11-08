"""harvey pruefen: the lead list is checked so no value sits at the wrong company."""

import json

import pytest
import pytest_asyncio

from harvey import leads as L
from harvey import pruefen as P
from harvey.models import Company
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state(tmp_path):
    s = StateManager(str(tmp_path / "p.db"))
    await s.init_db()
    return s


async def add(state, name, domain, lead, phone=""):
    cid = await state.add_company(Company(name=name, domain=domain, website=f"https://{domain}", phone=phone))
    await L.save_lead(state, cid, lead)
    return cid


def email(addr, person="", source=L.SOURCE_SITE, check=L.OK):
    return {"email": addr, "person": person, "source": source, "check": check}


@pytest.mark.asyncio
async def test_rules_catch_foreign_domains_shared_values_and_wrong_contacts(state):
    a = await add(state, "Praxis Linde", "praxis-linde.at",
                  {"people": [{"name": "Eva Lindner", "title": "Inhaberin"}],
                   "emails": [email("office@fremd-praxis.at")], "site_phones": ["+43 316 111111"]})
    b = await add(state, "Praxis Berg", "praxis-berg.at",
                  {"people": [], "emails": [email("info@praxis-berg.at")], "site_phones": ["0316 111111"]})
    c = await add(state, "Praxis Ost", "praxis-ost.at",
                  {"people": [{"name": "Anna Huber", "title": "Inhaberin"}],
                   "emails": [email("info@praxis-ost.at")]})
    records = await P.load_records(state)
    found = P.rule_findings(records)
    assert found[a]["remove"]["emails"] == ["office@fremd-praxis.at"]
    # The same number at two companies is a doubt for the agents, not a removal.
    assert any("Telefon steht bei mehreren Firmen" in n for n in found[a]["notes"])
    assert found[a]["remove"]["phones"] == [] and found[b]["remove"]["phones"] == []
    assert found[c]["notes"] == [] and not any(found[c]["remove"].values())


@pytest.mark.asyncio
async def test_removed_values_stay_out_of_the_export_and_decisions_apply(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at",
                    {"people": [{"name": "Anna Huber", "title": "Inhaberin"}],
                     "emails": [email("info@praxis-ost.at")], "site_phones": ["0316 222222"]})
    decisions = tmp_path / "d.json"
    decisions.write_text(json.dumps([{"company_id": cid, "email": "behalten",
                                      "ansprechpartner": "entfernen",
                                      "telefon": {"0316 222222": "entfernen"},
                                      "begruendung": "Anna Huber ist laut Impressum die Webdesignerin"}]))
    c = await P.apply_decisions(state, decisions)
    assert c == {"behalten": 0, "entfernt": 1}
    [record] = await P.load_records(state)
    row, _ = L.rows_for(record, json.loads(record["lead"]))
    assert row["Beste E-Mail"] == "info@praxis-ost.at"
    assert row["Ansprechpartner"] == "" and row["Telefon (Website)"] == ""
    assert row["Zuordnung geprüft"] == "korrigiert" and "Webdesignerin" in row["Prüfhinweis"]


@pytest.mark.asyncio
async def test_open_cases_are_only_the_doubtful_rows(state):
    await add(state, "Praxis Ost", "praxis-ost.at", {"people": [], "emails": [email("info@praxis-ost.at")]})
    bad = await add(state, "Praxis Linde", "praxis-linde.at",
                    {"people": [], "emails": [email("office@fremd-praxis.at")]})
    records = await P.load_records(state)
    cases = P.open_cases(records, P.rule_findings(records))
    assert [c["company_id"] for c in cases] == [bad] and "fremd-praxis.at" in cases[0]["hinweise"][0]


def test_phone_numbers_compare_in_any_format_and_never_merge_different_ones():
    k = L.phone_key
    assert k("+43 316 111111", "AT") == k("0316 111111", "AT") == k("0043 316/111111", "AT")
    assert k("+49 89 12345", "DE") == k("089 12345", "DE")
    assert k("0151 23456789", "DE") != k("0171 23456789", "DE")
    assert k("+41 44 1234567") != k("+49 44 1234567")


class FakeScout:
    """Reads sites the way Scout does, from a dict of {domain: (pages, redirected_to)}."""
    def __init__(self, sites):
        self.sites, self._site_reads = sites, {}

    def _host(self, d):
        return d.lower().removeprefix("www.")

    async def _read_site(self, domain):
        pages, redirected = self.sites.get(domain, ([], ""))
        site = {"base": f"https://{domain}" if pages else "", "tried": set(),
                "pages": [(f"https://{domain}/p{i}", t) for i, t in enumerate(pages)]}
        if redirected:
            site["redirected"] = redirected
        return site

    async def _site_fetch(self, site, url):
        return None


@pytest.mark.asyncio
async def test_live_check_drops_everything_from_a_site_that_now_redirects(state):
    await add(state, "Zahnarzt Ost", "zahnarzt-ost.at",
              {"site": "https://zahnarzt-ost.at", "description": "Fremde Beschreibung",
               "people": [{"name": "Karl Fremd", "title": "Inhaber"}],
               "emails": [email("k.fremd@zahnarzt-ost.at", person="Karl Fremd")],
               "site_phones": ["0316 999999"]})
    records = await P.load_records(state)
    found = await P.live_findings(records, scout=FakeScout({"zahnarzt-ost.at": ([], "fremd.app")}))
    await P.store_reviews(state, records, found, "live")
    [record] = await P.load_records(state)
    row, _ = L.rows_for(record, json.loads(record["lead"]))
    assert row["Beste E-Mail"] == "" and row["Ansprechpartner"] == ""
    assert row["Telefon (Website)"] == "" and row["Was sie machen (Website)"] == ""
    assert "leitet auf fremd.app" in row["Prüfhinweis"]


@pytest.mark.asyncio
async def test_live_check_reads_the_team_page_and_never_deletes_on_a_miss(state):
    await add(state, "Praxis Linde", "praxis-linde.at",
              {"site": "https://praxis-linde.at",
               "people": [{"name": "Eva Lindner", "title": "Inhaberin"}],
               "emails": [email("e.lindner@praxis-linde.at", person="Eva Lindner"),
                          email("office@praxis-linde.at")],
               "site_phones": ["0316 123456"]})
    home = "<p>Willkommen</p><p>Tel 0316 123456</p><a href='mailto:office@praxis-linde.at'>Mail</a>"
    team = "<p>Eva Lindner, Inhaberin</p><p>e.lindner@praxis-linde.at</p>"
    records = await P.load_records(state)
    found = await P.live_findings(records, scout=FakeScout({"praxis-linde.at": ([home, team], "")}))
    item = next(iter(found.values()))
    assert item["notes"] == [] and not any(item["remove"].values())
    # Only the homepage readable this time: a doubt for the agents, nothing removed.
    found = await P.live_findings(records, scout=FakeScout({"praxis-linde.at": ([home], "")}))
    item = next(iter(found.values()))
    assert item["notes"] and not any(item["remove"].values())


@pytest.mark.asyncio
async def test_agent_removals_survive_a_new_rule_run(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at",
                    {"people": [{"name": "Anna Huber", "title": "Inhaberin"}],
                     "emails": [email("info@praxis-ost.at"), email("a.huber@praxis-ost.at", person="Anna Huber")]})
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "ansprechpartner": "entfernen", "begruendung": "Webdesignerin"}]))
    await P.apply_decisions(state, d)
    records = await P.load_records(state)
    await P.store_reviews(state, records, P.rule_findings(records), "regeln")
    [record] = await P.load_records(state)
    lead = json.loads(record["lead"])
    row, sheet = L.rows_for(record, lead)
    assert row["Ansprechpartner"] == "" and "a.huber@praxis-ost.at" not in {e["E-Mail"] for e in sheet}
    assert all(e["Person"] != "Anna Huber" for e in sheet)


@pytest.mark.asyncio
async def test_removing_the_best_address_lets_the_next_move_up_as_a_new_case(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at",
                    {"people": [], "emails": [email("info@praxis-ost.at"),
                                              email("office@praxis-ost.at", check=L.UNCHECKED)]})
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "email": "entfernen"}]))
    await P.apply_decisions(state, d)
    [record] = await P.load_records(state)
    lead = json.loads(record["lead"])
    row, _ = L.rows_for(record, lead)
    assert row["Beste E-Mail"] == "office@praxis-ost.at"
    assert lead["review"]["status"] == "unsicher" and "Nachgerückt" in row["Prüfhinweis"]


@pytest.mark.asyncio
async def test_malformed_decisions_write_nothing(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at", {"people": [], "emails": [email("info@praxis-ost.at")]})
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "email": "entfernen"}, {"company_id": cid, "telefon": ["x"]}]))
    with pytest.raises(ValueError):
        await P.apply_decisions(state, d)
    [record] = await P.load_records(state)
    assert "review" not in json.loads(record["lead"])


def test_a_port_in_the_url_is_still_the_same_site():
    from harvey.public_http import same_site
    assert same_site(P._host("https://abc.de:8443/kontakt"), "abc.de")


@pytest.mark.asyncio
async def test_all_foreign_addresses_go_at_once(state):
    cid = await add(state, "Praxis Linde", "praxis-linde.at",
                    {"people": [], "emails": [email(f"info@fremd{i}.at") for i in range(6)]})
    records = await P.load_records(state)
    found = P.rule_findings(records)
    await P.store_reviews(state, records, found, "regeln")
    [record] = await P.load_records(state)
    row, _ = L.rows_for(record, json.loads(record["lead"]))
    assert row["Beste E-Mail"] == ""


@pytest.mark.asyncio
async def test_several_decisions_for_one_company_add_up(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at",
                    {"people": [], "emails": [email("info@praxis-ost.at")], "site_phones": ["0316 222222"]})
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "telefon": {"0316 222222": "entfernen"}},
                             {"company_id": cid, "email": "entfernen"}]))
    await P.apply_decisions(state, d)
    [record] = await P.load_records(state)
    row, _ = L.rows_for(record, json.loads(record["lead"]))
    assert row["Beste E-Mail"] == "" and row["Telefon (Website)"] == ""


def test_country_and_phone_edge_cases():
    assert L.phone_key("+43 (0)316 999999", "AT") == L.phone_key("0316 999999", "AT")
    assert L.company_country({"location": "Wiener Straße 3, Berlin"}) == ""
    assert L.company_country({"location": "Bernau, Deutschland"}) == "DE"
    assert L.company_country({"domain": "praxis.at", "location": "Berlin"}) == "AT"


def test_removed_people_stay_removed_with_titles():
    from harvey.agents.scout import _same_person_in
    assert _same_person_in("Anna Huber", {"Dr. Anna Huber"})
    assert not _same_person_in("Anna Berg", {"Dr. Anna Huber"})


def test_small_list_fixes():
    assert L._clean_phone("tel: +43 316 1234567") == "+43 316 1234567"
    assert L.site_description('<meta name="description" content="xenia, a beautiful template for clinics">') == ""
    assert L._provider_label({"mail_host": "mx.praxis.at"}, "a@aon.at") == "Freemail (aon.at)"


@pytest.mark.asyncio
async def test_a_map_address_on_another_domain_is_a_doubt_not_a_removal(state):
    await add(state, "Praxis Linde", "praxis-linde.at",
              {"people": [], "emails": [email("therapie@praxis-linde-alt.at", source=L.SOURCE_MAP),
                                        email("office@schwester.at")]})
    records = await P.load_records(state)
    item = next(iter(P.rule_findings(records).values()))
    assert item["remove"]["emails"] == ["office@schwester.at"]          # from the site: removed
    assert any("Adresse aus der Karte" in n for n in item["notes"])     # from the map: a doubt


@pytest.mark.asyncio
async def test_an_association_site_is_no_company_site(state):
    await add(state, "Physio Wöhr", "physio.at",
              {"site": "https://physio.at", "description": "Berufsverband", "people": [],
               "emails": [email("therapie@physio.at")]})
    records = await P.load_records(state)
    found = P.rule_findings(records)
    await P.store_reviews(state, records, found, "regeln")
    from harvey.leads import sample_prospects
    # The association's own address isn't the company's: no preview goes there at all.
    assert await sample_prospects(state, 1) == []


def test_theme_blurbs_and_per_address_mail_servers():
    assert L.site_description('<meta name="description" content="Xenia – Medical WordPress Template">') == ""
    _, sheet = L.rows_for({"name": "P", "domain": "p.at"},
                          {"mail_host": "p-at.mail.protection.outlook.com", "provider": "microsoft",
                           "emails": [email("a.b@aon.at"), email("office@p.at")]})
    servers = {e["E-Mail"]: e["Mailserver"] for e in sheet}
    assert servers["a.b@aon.at"] == "Freemail (aon.at)" and "Microsoft" in servers["office@p.at"]


@pytest.mark.asyncio
async def test_a_moved_up_address_is_a_case_on_the_next_run(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at",
                    {"people": [], "emails": [email("info@praxis-ost.at"),
                                              email("office@praxis-ost.at", check=L.UNCHECKED)]})
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "email": "entfernen"}]))
    await P.apply_decisions(state, d)
    records = await P.load_records(state)
    cases = P.open_cases(records, P.rule_findings(records))
    assert [c["email"] for c in cases] == ["office@praxis-ost.at"]


@pytest.mark.asyncio
async def test_settled_doubts_are_no_cases_and_association_addresses_go(state, tmp_path):
    cid = await add(state, "Praxis Ost", "praxis-ost.at",
                    {"people": [], "emails": [email("info@praxis-ost.at"),
                                              email("office@praxis-ost.at", check=L.UNCHECKED)]})
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "email": "entfernen"}]))
    await P.apply_decisions(state, d)
    d.write_text(json.dumps([{"company_id": cid, "email": "behalten"}]))
    await P.apply_decisions(state, d)
    records = await P.load_records(state)
    assert P.open_cases(records, P.rule_findings(records)) == []
    await add(state, "Physio Wöhr", "physio.at", {"site": "https://physio.at", "people": [],
                                                   "emails": [email("office@physio.at")]})
    records = await P.load_records(state)
    found = P.rule_findings(records)
    assert any("office@physio.at" in found[r["id"]]["remove"]["emails"] for r in records)


@pytest.mark.asyncio
async def test_a_website_of_another_business_leaves_only_the_map_data(state, tmp_path):
    cid = await add(state, "Praxis Nord", "schwester.at",
                    {"site": "https://schwester.at", "description": "Schwesterpraxis",
                     "people": [{"name": "Karl Fremd", "title": "Inhaber"}],
                     "emails": [email("therapie@praxis-nord.at", source=L.SOURCE_MAP),
                                email("buero@schwester.at"), email("k.fremd@schwester.at", person="Karl Fremd")],
                     "site_phones": ["0316 777777"]}, phone="0316 111111")
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "website": "entfernen", "begruendung": "gehört der Schwesterpraxis"}]))
    await P.apply_decisions(state, d)
    [record] = await P.load_records(state)
    lead = json.loads(record["lead"])
    row, sheet = L.rows_for(record, lead)
    assert row["Beste E-Mail"] == "therapie@praxis-nord.at" and row["Weitere E-Mails"] == ""
    assert row["Ansprechpartner"] == "" and row["Telefon (Website)"] == "" and row["Telefon (Karte)"] == "0316 111111"
    assert row["Was sie machen (Website)"] == "" and {e["E-Mail"] for e in sheet} == {"therapie@praxis-nord.at"}
    assert row["Website"] == "" and all(e["Website"] == "" for e in sheet)

    from harvey.agents.scout import Scout
    s = Scout.__new__(Scout)
    s.state = state
    assert (await s._lead_removed(cid))["site"] is True


@pytest.mark.asyncio
async def test_map_phone_survives_when_the_site_shows_the_same_number(state, tmp_path):
    cid = await add(state, "Praxis Nord", "schwester.at",
                    {"site": "https://schwester.at", "people": [],
                     "emails": [email("buero@schwester.at")], "site_phones": ["+43 316 111111"]},
                    phone="0316 111111")
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "website": "entfernen"}]))
    await P.apply_decisions(state, d)
    [record] = await P.load_records(state)
    row, _ = L.rows_for(record, json.loads(record["lead"]))
    assert row["Telefon (Karte)"] == "0316 111111" and row["Telefon (Website)"] == ""
    d.write_text(json.dumps([{"company_id": cid, "telefon": {"+43 316 111111": "entfernen"}}]))
    await P.apply_decisions(state, d)
    [record] = await P.load_records(state)
    row, _ = L.rows_for(record, json.loads(record["lead"]))
    assert row["Telefon (Karte)"] == ""        # a decision about the map's own number removes it


@pytest.mark.asyncio
async def test_writer_skips_contacts_a_check_removed(state, tmp_path):
    from types import SimpleNamespace
    from harvey.agents.writer import Writer
    from harvey.models.prospect import Prospect
    cid = await state.add_company(Company(name="Praxis Nord", domain="schwester.at",
                                          website="https://schwester.at", email="therapie@praxis-nord.at"))
    await L.save_lead(state, cid, {"site": "https://schwester.at", "people": [],
                                   "emails": [email("therapie@praxis-nord.at", source=L.SOURCE_MAP),
                                              email("buero@schwester.at")]})
    for addr in ("buero@schwester.at", "therapie@praxis-nord.at"):
        await state.add_prospect(Prospect(company="Praxis Nord", company_id=cid, email=addr,
                                          email_status="verified", source="company_inbox", title="General inbox"))
    d = tmp_path / "d.json"
    d.write_text(json.dumps([{"company_id": cid, "website": "entfernen"}]))
    await P.apply_decisions(state, d)

    seen = []

    class Brain:
        def load_skills_for_agent(self, n): return ""
        async def think_json(self, *a, **k): return None

    w = Writer(Brain(), state, SimpleNamespace(
        channels=SimpleNamespace(email=SimpleNamespace(provider="gmail", require_approval=True, first_touch="question")),
        writer=SimpleNamespace(email_rules="")))

    async def capture(batch):
        seen.extend(p.email for p in batch)
        return []
    w._write_sequence = capture
    w._group_prospects = lambda ps: {"x": ps}
    await w.run()
    assert seen == ["therapie@praxis-nord.at"]

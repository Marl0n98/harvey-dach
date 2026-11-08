"""German language, DACH names and the consent hold."""
from types import SimpleNamespace

import httpx
import pytest

from harvey.agents.handler import _opt_out_reason
from harvey.agents.sender import consent_hold
from harvey.collectors.discover import is_junk
from harvey.collectors.people import find_people, people_from_impressum, split_person_name
from harvey.config import ComplianceConfig


def cfg(geo=("Berlin, Deutschland",), **kw):
    return SimpleNamespace(compliance=ComplianceConfig(**kw), icp=SimpleNamespace(geography=list(geo)))


def test_umlaut_names():
    assert split_person_name("Jürgen Müller") == ("Jürgen", "Müller")
    assert split_person_name("Dr. Anna Bär") == ("Anna", "Bär")


def test_impressum_names():
    html = "<p>Muster GmbH</p><p>Geschäftsführer: Jürgen Müller und Dr. Anna Bär</p><p>HRB 12345</p>"
    assert [(p["first_name"], p["last_name"]) for p in people_from_impressum(html)] == [
        ("Jürgen", "Müller"), ("Anna", "Bär")]


def test_impressum_name_on_next_line_ranks_as_owner():
    html = "<h2>Vertreten durch:</h2><p>Lena Vogt</p>"
    people = find_people([("https://x.de/impressum", html)], "x.de")
    assert people[0]["last_name"] == "Vogt" and people[0]["owner"]


def test_impressum_ignores_company_as_name():
    assert people_from_impressum("<p>Vertreten durch: Muster Verwaltungs GmbH</p>") == []


def test_german_opt_outs():
    for text in ("Bitte abmelden", "Kein Interesse.", "Schreiben Sie mir nicht mehr",
                 "Nehmen Sie mich aus Ihrem Verteiler"):
        assert _opt_out_reason(text, text), text


def test_opt_out_line_german():
    line = 'Kein Interesse? Antworten Sie einfach "abmelden", dann schreibe ich nicht mehr.'
    assert ComplianceConfig(opt_out_line=line).opt_out_line == line


def test_consent_holds_dach_tlds():
    for address in ("a@x.de", "a@x.at", "a@x.ch"):
        assert consent_hold(cfg(geo=["Texas"]), address)


def test_consent_list_releases_address_and_domain():
    assert not consent_hold(cfg(consent_list=["a@x.de"]), "a@x.de")
    assert not consent_hold(cfg(consent_list=["x.de"]), "b@x.de")
    assert consent_hold(cfg(consent_list=["a@x.de"]), "b@x.de")


def test_known_country_beats_tld():
    assert consent_hold(cfg(geo=["Texas"]), "a@x.com", "DE")
    assert not consent_hold(cfg(geo=["Texas"]), "a@x.de", "US")


def test_unknown_country_with_dach_icp_holds():
    assert consent_hold(cfg(), "a@x.com")
    assert not consent_hold(cfg(geo=["Texas"]), "a@x.com")


def test_check_can_be_switched_off():
    assert not consent_hold(cfg(consent_required_countries=[]), "a@x.de")


def test_public_bodies_filtered():
    for domain in ("rakwien.at", "ag.ch", "bmj.gv.at"):
        assert is_junk(domain), domain


def test_impressum_strips_degrees_and_professions():
    html = "<p>Inhaber: Dipl.-Ing. Rechtsanwalt Dr. Tim Weiß</p><p>Medieninhaber: Mag. Eva Gruber</p>"
    assert [(p["first_name"], p["last_name"]) for p in people_from_impressum(html)] == [
        ("Tim", "Weiß"), ("Eva", "Gruber")]


def test_german_job_titles():
    from harvey.collectors.profile import _GERMAN_ROLE
    assert _GERMAN_ROLE.search("Steuerfachangestellte (m/w/d)")
    assert _GERMAN_ROLE.search("Maler (w/m/d) in Vollzeit")
    assert _GERMAN_ROLE.search("Ausbildung zum Dachdecker")
    assert not _GERMAN_ROLE.search("Unsere Leistungen")


def test_german_booking_tools():
    import re
    from harvey.collectors.profile import BOOKING_HINTS
    for html in ("https://www.doctolib.de/praxis", "Termin online buchen", "etermin.net/x"):
        assert any(re.search(p, html, re.I) for p in BOOKING_HINTS), html


@pytest.mark.asyncio
async def test_observed_signals_reach_the_writer():
    from harvey.agents.writer import Writer

    class State:
        async def get_observations(self, **kw):
            return [{"signal_code": "BLOG_STALE", "value_num": 400, "value_text": "newest content 2025-09-01"},
                    {"signal_code": "HIRING_ROLE", "value_num": None, "value_text": "Steuerfachangestellte (m/w/d)"},
                    {"signal_code": "RUNNING_META_ADS", "value_num": 0, "value_text": ""}]

    facts = await Writer(None, State(), None)._observed_facts("c1")
    assert len(facts) == 2 and "400 days" in facts[0] and "m/w/d" in facts[1]


@pytest.mark.asyncio
async def test_a_young_site_is_no_stale_signal():
    from harvey.agents.writer import Writer

    class State:
        async def get_observations(self, **kw):
            return [{"signal_code": "BLOG_STALE", "value_num": 30, "value_text": "x"}]

    assert await Writer(None, State(), None)._observed_facts("c1") == []


@pytest.mark.parametrize("answers, ok", [
    (["64:ff9b::74ca:4735", "116.202.71.53"], True),
    (["64:ff9b::a9fe:a9fe"], False),
    (["64:ff9b::1", "10.0.0.1"], False),
    (["116.202.71.53", "127.0.0.1"], False),
])
@pytest.mark.asyncio
async def test_nat64_answers_never_get_connected_to(answers, ok, monkeypatch):
    import asyncio
    import socket
    import httpx
    from harvey import public_http

    async def resolve(self, host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (a, port)) for a in answers]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    seen = []

    async def connect(self, request):
        seen.append(request.url.host)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", connect)
    transport = public_http.PublicHTTPTransport()
    request = httpx.Request("GET", "https://example.test/")
    if ok:
        await transport.handle_async_request(request)
        assert seen == ["116.202.71.53"]
    else:
        with pytest.raises(public_http.UnsafeURLError):
            await transport.handle_async_request(request)
        assert seen == []


def test_deutschland_expands_to_every_state():
    from harvey.collectors.discover import GERMAN_STATES, OSM_AREAS, expand_places

    places = expand_places(["Deutschland", "Schweiz", "Wien, Österreich"])
    assert len(places) == len(GERMAN_STATES) + 2
    assert all(p.lower() in OSM_AREAS for p in places if p.endswith("Deutschland"))
    assert "Schweiz" in places and "Wien, Österreich" in places


@pytest.mark.asyncio
async def test_osm_country_search_uses_the_boundary_and_no_cap():
    from harvey.collectors.discover import DiscoveryQuery, OpenStreetMap

    class Client:
        async def post(self, url, content, headers, timeout):
            self.body = content.decode()
            return httpx.Response(200, json={"elements": []}, request=httpx.Request("POST", url))

    client = Client()
    await OpenStreetMap().fetch(client, {}, DiscoveryQuery(term="Zahnarzt", location="Österreich", limit=0))
    assert 'area["ISO3166-1"="AT"]' in client.body
    assert "(area.a)" in client.body
    assert client.body.endswith("out center;")


def test_impressum_and_team_words_are_not_people():
    for text in ("Betreiber der Website", "Gemeinde Musterdorf", "Zahnarztpraxis Klaus",
                 "Frau Uhrlich", "Zahnmedizinisches Institut"):
        assert split_person_name(text) is None, text
    assert split_person_name("Doris Jung") == ("Doris", "Jung")


def test_question_first_keeps_one_email_and_a_valid_setting():
    from harvey.agents.writer import Writer
    from harvey.config import EmailChannelConfig

    assert EmailChannelConfig(first_touch="Question").first_touch == "question"
    with pytest.raises(ValueError):
        EmailChannelConfig(first_touch="spam")
    writer = Writer.__new__(Writer)
    writer.config = SimpleNamespace(channels=SimpleNamespace(email=SimpleNamespace(first_touch="question")))
    steps = writer._parse_sequence([{"subject": f"s{i}", "body": f"b{i}", "delay_days": i} for i in range(3)])
    assert [s.step for s in steps] == [1]

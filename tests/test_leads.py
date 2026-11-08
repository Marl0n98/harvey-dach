"""The DACH lead list: what is read from a site, how addresses are guessed and checked."""
import httpx
import pytest

from harvey import leads as L
from harvey.integrations.email_finder import generate_patterns


def test_umlauts_are_spelled_out_in_guessed_addresses():
    assert generate_patterns("Jürgen", "Müller", "praxis.test")[0] == "juergen.mueller@praxis.test"
    assert generate_patterns("Zoë", "Groß", "praxis.test")[0] == "zoe.gross@praxis.test"


def test_description_prefers_meta_and_falls_back_to_title():
    html = '<title>Praxis</title><meta name="description" content="Zahnarztpraxis in Graz mit Prophylaxe und Implantaten">'
    assert L.site_description(html).startswith("Zahnarztpraxis in Graz")
    assert L.site_description("<title>Physio Huber &amp; Team</title>") == "Physio Huber & Team"


def test_phones_from_tel_links_and_printed_numbers():
    html = '<a href="tel:+43316123456">Anrufen</a><p>Telefon: 0316 / 12 34 56</p><p>Fax 0316 99</p><p>Tel.: +43 664 1234567</p>'
    phones = L.phones_on_page(html)
    assert phones[0] == "+43316123456"
    assert "+43 664 1234567" in phones
    assert len(phones) == 2   # the printed copy of the tel: link is not counted twice


def test_a_published_address_that_spells_the_name_wins():
    person = {"first_name": "Anna", "last_name": "Weber"}
    assert L.candidates_for(person, "praxis.test", ["info@praxis.test", "a.weber@praxis.test"]) == [
        ("a.weber@praxis.test", L.SOURCE_SITE)]


def test_a_colleagues_address_gives_the_pattern():
    person = {"first_name": "Anna", "last_name": "Weber"}
    out = L.candidates_for(person, "praxis.test", ["m.huber@praxis.test"])
    assert out[0] == ("a.weber@praxis.test", L.SOURCE_PATTERN)
    assert all(src == L.SOURCE_GUESS for _, src in out[1:])


def test_double_surnames_also_try_the_first_part():
    person = {"first_name": "Eva", "last_name": "Meier-Lang"}
    emails = [e for e, _ in L.candidates_for(person, "kanzlei.test", [])]
    assert "eva.meierlang@kanzlei.test" in emails and "eva.meier@kanzlei.test" in emails


def test_best_email_prefers_confirmed_and_never_picks_an_invalid_one():
    emails = [
        {"email": "info@x.test", "source": L.SOURCE_SITE, "person": "", "check": L.UNKNOWN},
        {"email": "anna@x.test", "source": L.SOURCE_GUESS, "person": "Anna Weber", "check": L.OK},
        {"email": "a.weber@x.test", "source": L.SOURCE_GUESS, "person": "Anna Weber", "check": L.BAD},
    ]
    assert L.best_email(emails)["email"] == "anna@x.test"
    assert L.best_email([emails[2]]) is None


class FakeSMTP:
    accepted: set = set()
    catch_all = False

    def __init__(self, **kw):
        pass

    async def connect(self):
        pass

    async def ehlo(self):
        pass

    async def mail(self, sender):
        assert sender == ""          # the null sender: nothing can come back
        return L.aiosmtplib.SMTPResponse(250, "ok")

    async def rcpt(self, email):
        if email in self.accepted or (self.catch_all and email.startswith("keinpostfach")):
            return L.aiosmtplib.SMTPResponse(250, "ok")
        # aiosmtplib raises on a refusal instead of returning it
        raise L.aiosmtplib.SMTPRecipientRefused(550, "5.1.1 user unknown", email)

    async def quit(self):
        pass


@pytest.mark.asyncio
async def test_mailbox_check_tells_confirmed_from_catch_all(monkeypatch):
    async def mx(domain):
        return "mx.praxis.test", "ok"
    monkeypatch.setattr(L, "mx_lookup", mx)
    monkeypatch.setattr(L.asyncio, "sleep", lambda s: _done())
    FakeSMTP.accepted = {"anna@praxis.test"}
    monkeypatch.setattr(L.aiosmtplib, "SMTP", FakeSMTP)
    out = await L.MailboxChecker().check("praxis.test", ["anna@praxis.test", "a.weber@praxis.test"])
    assert out["catch_all"] is False
    assert out["results"] == {"anna@praxis.test": L.OK, "a.weber@praxis.test": L.BAD}

    FakeSMTP.catch_all = True
    FakeSMTP.accepted = {"anna@praxis.test", "a.weber@praxis.test"}
    out = await L.MailboxChecker().check("praxis.test", ["anna@praxis.test", "a.weber@praxis.test"])
    assert set(out["results"].values()) == {L.CATCH_ALL}
    FakeSMTP.catch_all = False


async def _done():
    return None


@pytest.mark.asyncio
async def test_osm_keeps_email_address_and_country():
    from harvey.collectors.discover import DiscoveryQuery, OpenStreetMap

    element = {"type": "node", "id": 1, "tags": {
        "name": "Zahnarzt Dr. Huber", "amenity": "dentist", "website": "https://huber.test",
        "email": "Praxis@Huber.test", "addr:street": "Hauptplatz", "addr:housenumber": "3",
        "addr:postcode": "8010", "addr:city": "Graz", "phone": "+43 316 1"}}

    class Client:
        async def post(self, url, content, headers, timeout):
            return httpx.Response(200, json={"elements": [element]}, request=httpx.Request("POST", url))

    [biz] = await OpenStreetMap().fetch(Client(), {}, DiscoveryQuery(term="Zahnarzt", location="Österreich", limit=0))
    assert biz.email == "praxis@huber.test"
    assert biz.address == "Hauptplatz 3, 8010 Graz"
    assert biz.location == "Graz, Österreich"


def test_export_writes_semicolon_csv_and_both_sheets(tmp_path):
    from openpyxl import load_workbook

    company = {"name": "Physio Huber", "website": "https://huber.test", "location": "Graz, Österreich",
               "phone": "+43 316 1", "industry": "physiotherapist"}
    lead = {"people": [{"name": "Anna Huber", "title": "Inhaberin", "owner": True}],
            "emails": [{"email": "anna@huber.test", "source": L.SOURCE_GUESS, "person": "Anna Huber", "check": L.OK},
                       {"email": "info@huber.test", "source": L.SOURCE_SITE, "person": "", "check": L.OK}],
            "mail_host": "mx.huber.test", "provider": "other", "catch_all": False}
    row, emails = L.rows_for(company, lead)
    assert row["Beste E-Mail"] == "anna@huber.test" and row["Ansprechpartner"] == "Anna Huber"
    assert row["Catch-all"] == "nein" and len(emails) == 2
    assert L.to_csv([row], L.COMPANY_COLUMNS).splitlines()[0].startswith("Firma;Branche;")
    L.to_xlsx(tmp_path / "x.xlsx", [("Firmen", L.COMPANY_COLUMNS, [row]), ("E-Mails", L.EMAIL_COLUMNS, emails)])
    book = load_workbook(tmp_path / "x.xlsx")
    assert book.sheetnames == ["Firmen", "E-Mails"] and book["E-Mails"].max_row == 3

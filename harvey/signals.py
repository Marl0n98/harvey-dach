"""The signal vocabulary Harvey proposes, and the user confirms.

Harvey never decides on its own what makes a good prospect. It proposes a
catalog of signals it knows how to collect — each with a plain-language
description, what it costs, and why it might matter — and the user confirms,
edits, or rejects them in the dashboard before any collection runs.

Only ``confirmed`` signals get collected. That keeps spend intentional and
keeps the resulting cohorts explainable: a prospect list is a query over
signals a human chose, not a black box.

Design note: every signal is recorded as an OBSERVATION (a row), never a
column. A new signal needs no migration, history comes free, and confidence
and provenance travel with the fact.
"""

# A cost note starting with one of these means collecting it costs nothing
# (`harvey signals --confirm free`, the dashboard's cost chip). The English
# ones are what rows seeded before the German notes still say.
FREE_COST_PREFIXES = ("kostenlos", "enthalten", "free", "included")

# category: discovery | profile | people | verification
# value_type: num | text | bool
# cost_note: what collecting it actually costs, in plain language
SIGNAL_CATALOG: list[dict] = [
    # ── Discovery: who exists, and how visible are they ──
    {
        "code": "SERP_RANK",
        "label": "Position in der Google-Suche",
        "description": (
            "Wo die Firma organisch für eine Suche nach '<Leistung> <Stadt>' "
            "steht (die Suchanfrage wird mitgespeichert). Das nützlichste "
            "Signal für die Akquise: Platz 11 bis 30 ist der beste Bereich. "
            "Sichtbar genug, dass sie es versuchen, aber noch nicht vorne. Die "
            "Top 10 gewinnen schon (schwerer zu verkaufen), ab Platz 31 ist es "
            "günstiger, aber weniger dringend. Der Rang hängt davon ab, wo "
            "gesucht wird: Das hier ist der Blick aus der Stadtmitte."
        ),
        "category": "discovery",
        "value_type": "num",
        "collector": "discover",
        "cost_note": "ca. $0.006 pro Suche bei Tiefe 30 (kostenpflichtige SERP-API, Live-Endpunkt)",
        "confidence_floor": 0.0,
    },
    {
        "code": "IN_LOCAL_PACK",
        "label": "Im lokalen 3er-Block von Google",
        "description": (
            "Hat einen der drei Kartenplätze über den organischen Ergebnissen "
            "für eine Suche nach '<Leistung> <Stadt>' (Wert = Platz 1 bis 3, "
            "ohne Anzeigen). Diese Firmen gewinnen die lokale Suche schon. "
            "Schließ sie aus, um die anderen zu finden, oder biete ihnen etwas "
            "anderes an."
        ),
        "category": "discovery",
        "value_type": "num",
        "collector": "discover",
        "cost_note": "enthalten in der organischen Suche oben",
        "confidence_floor": 0.0,
    },
    {
        "code": "MAPS_RANK",
        "label": "Position in Google Maps",
        "description": (
            "Wo die Firma in Google Maps bei einer Suche nach '<Leistung> <Stadt>' "
            "aus der Stadtmitte erscheint (die Suchanfrage wird mitgespeichert). "
            "Außerhalb der Top 3 auf der Karte heißt: außerhalb des lokalen "
            "Blocks, den die meisten sehen. Findet auch Firmen ganz ohne Website."
        ),
        "category": "discovery",
        "value_type": "num",
        "collector": "discover",
        "cost_note": "ca. $0.002 pro Maps-Suche mit bis zu 100 Ergebnissen (kostenpflichtige SERP-API)",
        "confidence_floor": 0.0,
    },
    {
        "code": "FOUND_IN_SERP",
        "label": "Über die Suche gefunden",
        "description": "Die Firma ist in einer Suche im Zielmarkt aufgetaucht. So hat Harvey sie überhaupt gefunden.",
        "category": "discovery",
        "value_type": "text",
        "collector": "discover",
        "cost_note": "enthalten in der Suche oben",
        "confidence_floor": 0.0,
    },

    {
        "code": "NO_WEBSITE",
        "label": "Gar keine Website",
        "description": (
            "Die Firma hat einen Eintrag und eine Telefonnummer, aber keine Website. "
            "Wenn du Websites verkaufst, gibt es kein stärkeres Signal. Und du "
            "kannst direkt danach filtern, statt es zu erraten."
        ),
        "category": "discovery",
        "value_type": "bool",
        "collector": "discover",
        "cost_note": "enthalten in der Abfrage des Firmeneintrags",
        "confidence_floor": 0.0,
    },
    {
        "code": "UNCLAIMED_LISTING",
        "label": "Google-Eintrag nicht beansprucht",
        "description": (
            "Niemand hat ihr Google-Unternehmensprofil beansprucht. Sie kümmern "
            "sich also nicht darum, wie sie lokal auftauchen. Starkes Signal, "
            "wenn du lokales Marketing verkaufst."
        ),
        "category": "discovery",
        "value_type": "bool",
        "collector": "discover",
        "cost_note": "enthalten in der Abfrage des Firmeneintrags",
        "confidence_floor": 0.0,
    },
    {
        "code": "REVIEW_RATING",
        "label": "Bewertung",
        "description": (
            "Ihre durchschnittliche Sternebewertung. Hilft in beide Richtungen: "
            "Eine schlechte Bewertung ist ein Rufproblem, das du lösen kannst. "
            "Eine gute mit wenigen Bewertungen zeigt, dass sie gut, aber unsichtbar sind."
        ),
        "category": "discovery",
        "value_type": "num",
        "collector": "discover",
        "cost_note": "enthalten in der Abfrage des Firmeneintrags",
        "confidence_floor": 0.0,
    },
    {
        "code": "REVIEW_COUNT",
        "label": "Anzahl der Bewertungen",
        "description": (
            "Wie viele Bewertungen sie haben. Ein grober Hinweis auf ihre Größe "
            "und darauf, wie sehr sie sich um ihren Auftritt im Netz kümmern."
        ),
        "category": "discovery",
        "value_type": "num",
        "collector": "discover",
        "cost_note": "enthalten in der Abfrage des Firmeneintrags",
        "confidence_floor": 0.0,
    },

    # ── Profile: what they are (free — HTML Harvey already fetches) ──
    {
        "code": "INCUMBENT_AGENCY",
        "label": "Hat eine Marketing- oder Webagentur",
        "description": (
            "Eine Agentur, die im Footer der Website genannt wird ('Website von X'). "
            "Sehr wertvoll: Du weißt, wen du ablöst, und mit jedem Fund wächst eine "
            "Liste, die du als ganzen Kundenstamm angehen kannst. Wird nur erfasst, "
            "wenn die Nennung eindeutig ist. Eine falsche Agentur ist schlimmer als keine."
        ),
        "category": "profile",
        "value_type": "text",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.75,
    },
    {
        "code": "RUNNING_GOOGLE_ADS",
        "label": "Schaltet Google Ads",
        "description": "Google-Ads-Tag auf der Website. Sie geben HEUTE Geld für Kundengewinnung aus. Starkes Budgetsignal.",
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "RUNNING_META_ADS",
        "label": "Schaltet Meta- oder Facebook-Anzeigen",
        "description": "Meta-Pixel auf der Website. Sie geben gerade Geld für bezahlte Social-Media-Werbung aus.",
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "TECH_STACK",
        "label": "Website-Plattform und Tools",
        "description": "Worauf die Website läuft und welche Tools sie nutzt (WordPress, Shopify, HubSpot, Chat-Widgets, Buchungstools).",
        "category": "profile",
        "value_type": "text",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "NO_SCHEMA_MARKUP",
        "label": "Keine strukturierten Daten",
        "description": "Kein JSON-LD-Schema-Markup. Eine konkrete, prüfbare SEO-Lücke, die du in einer E-Mail nennen kannst.",
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "NO_ONLINE_BOOKING",
        "label": "Keine Online-Terminbuchung",
        "description": "Kein Buchungs- oder Termin-Widget gefunden. Interessenten müssen anrufen, Anfragen nach Feierabend gehen verloren.",
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "SITE_PAGE_COUNT",
        "label": "Größe der Website",
        "description": "Seitenzahl aus der sitemap.xml. Eine sehr dünne Website heißt wenig Investition, eine große veraltete heißt Vernachlässigung.",
        "category": "profile",
        "value_type": "num",
        "collector": "profile",
        "cost_note": "kostenlos (sitemap.xml)",
        "confidence_floor": 0.0,
    },
    {
        "code": "BLOG_STALE",
        "label": "Inhalte veraltet",
        "description": "Lange keine neuen Inhalte. Content-Marketing angefangen und liegen gelassen.",
        "category": "profile",
        "value_type": "num",
        "collector": "profile",
        "cost_note": "kostenlos (sitemap.xml)",
        "confidence_floor": 0.0,
    },
    {
        "code": "BLOCKS_AI_CRAWLERS",
        "label": "Sperrt KI-Crawler",
        "description": "Die robots.txt sperrt KI-Crawler. In Antworten von KI-Suchen tauchen sie dann nicht auf.",
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (robots.txt)",
        "confidence_floor": 0.0,
    },
    {
        "code": "YC_BACKED",
        "label": "Von Y Combinator finanziert",
        "description": ("Die Firma sagt auf ihrer eigenen Website, dass YC sie finanziert (ein "
                        "Batch wie W24, ein YC-Badge oder ein Link auf ihre YC-Seite). Passt zur "
                        "Suchvorlage YC-Startups und zum Schreibleitfaden für Startup-Gründer."),
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Startseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "ACCELERATOR",
        "label": "War in einem Accelerator",
        "description": ("Die Firma sagt auf ihrer eigenen Website, dass ein Accelerator sie unterstützt "
                        "hat: Techstars, 500 Global, Antler, Entrepreneur First, South Park Commons, Neo, "
                        "AI Grant, a16z speedrun, Pioneer oder Plug and Play, mit Jahrgang, wenn genannt. "
                        "YC läuft über YC_BACKED. Passt zur Suchvorlage Accelerator-Startups und zum "
                        "Schreibleitfaden für Startup-Gründer."),
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (Startseite und Über-uns-Seite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "FUNDING_ROUND",
        "label": "Finanzierungsrunde angekündigt",
        "description": ("Die Firma meldet auf ihrer Website selbst eine Finanzierung (\"we raised\", "
                        "\"Acme sammelt 12 Mio. $ in der Series A ein\"): Runde, Betrag und Datum, wenn "
                        "genannt. Runden, die älter als 18 Monate sind, werden nicht als Neuigkeit "
                        "angeschrieben. Passt zur Suchvorlage Frisch finanziert."),
        "category": "profile",
        "value_type": "bool",
        "collector": "profile",
        "cost_note": "kostenlos (Startseite, Über uns, Presseseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "HIRING_ROLE",
        "label": "Sucht für eine passende Stelle",
        "description": (
            "Eine offene Stelle, die zeigt, dass sie gerade in deinem Bereich einkaufen: "
            "eine deiner hiring_signals-Rollen (oder ICP-Titel) auf dem Greenhouse-, "
            "Lever-, Ashby- oder Workable-Board, auf das ihre Website verlinkt, mit "
            "Abteilung, Ort und Datum, oder eine Stelle auf ihrer Karriereseite."
        ),
        "category": "profile",
        "value_type": "text",
        "collector": "profile",
        "cost_note": "kostenlos (Karriereseite oder öffentlicher Feed der Jobbörse)",
        "confidence_floor": 0.0,
    },
    {
        "code": "OPEN_ROLES",
        "label": "Offene Stellen auf ihrer Jobbörse",
        "description": (
            "Wie viele Stellen auf dem Greenhouse-, Lever-, Ashby- oder Workable-Board "
            "offen sind, auf das ihre Website verlinkt. Ein wachsendes Team ist ein "
            "Budgetsignal, null Stellen ein leises."
        ),
        "category": "profile",
        "value_type": "num",
        "collector": "profile",
        "cost_note": "kostenlos (öffentlicher Feed der Jobbörse, eine Anfrage)",
        "confidence_floor": 0.0,
    },

    # ── People: who decides ──
    {
        "code": "CONTACT_FOUND",
        "label": "Ansprechpartner mit Namen gefunden",
        "description": "Eine Person mit Namen und Titel, gefunden auf der Team- oder Über-uns-Seite.",
        "category": "people",
        "value_type": "text",
        "collector": "people",
        "cost_note": "kostenlos (Teamseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "DECISION_MAKER_TITLE",
        "label": "Titel einer entscheidenden Person",
        "description": "Der Titel passt zu Inhabern oder Entscheidern (Inhaber, Geschäftsführer, Praxismanager, Marketingleiter).",
        "category": "people",
        "value_type": "text",
        "collector": "people",
        "cost_note": "kostenlos (Teamseite)",
        "confidence_floor": 0.0,
    },
    {
        "code": "REGISTRY_VERIFIED",
        "label": "In einem öffentlichen Register bestätigt",
        "description": (
            "Die Person wurde in einem amtlichen öffentlichen Register bestätigt "
            "(Kammer, Berufsregister). Filtert auch Fehler beim Auslesen heraus. "
            "Ein Name, der nirgends passt, war meistens keine Person."
        ),
        "category": "people",
        "value_type": "text",
        "collector": "people",
        "cost_note": "kostenlos (API des öffentlichen Registers)",
        "confidence_floor": 0.0,
    },
    {
        "code": "LIKELY_OWNER",
        "label": "Wahrscheinlich der Inhaber",
        "description": (
            "Die eigene Website der Firma sagt, dass diese Person sie besitzt oder "
            "gegründet hat: ein Titel wie Inhaber, Gründer oder Geschäftsführer, ein "
            "Satz wie 'inhabergeführt von' oder ein schema.org-Gründer. Frühere, "
            "pensionierte und verstorbene Inhaber zählen nicht. Der Beleg wird mitgespeichert."
        ),
        "category": "people",
        "value_type": "bool",
        "collector": "people",
        "cost_note": "kostenlos (Website der Firma)",
        "confidence_floor": 0.0,
    },

    # ── Verification ──
    {
        "code": "EMAIL_PATTERN",
        "label": "E-Mail-Schema der Firma",
        "description": "Das Adressformat der Domain (vorname.nachname@, vnachname@). Eine echte Adresse knackt die ganze Firma.",
        "category": "verification",
        "value_type": "text",
        "collector": "verify",
        "cost_note": "kostenlos oder ca. 1 Abfrage-Credit",
        "confidence_floor": 0.0,
    },
    {
        "code": "EMAIL_STATUS",
        "label": "Zustellbarkeit der E-Mail",
        "description": "Ob die Adresse geprüft ist, ein Catch-all und damit riskant, nur geraten oder ungültig.",
        "category": "verification",
        "value_type": "text",
        "collector": "verify",
        "cost_note": "1 Prüf-Credit (kostenlose Kontingente verfügbar)",
        "confidence_floor": 0.0,
    },
    {
        "code": "CONTACT_FORM_URL",
        "label": "Kontaktformular vorhanden",
        "description": (
            "Ein Kontaktformular auf der Website. Gut zu wissen, weil es die Firma "
            "ganz ohne E-Mail-Adresse erreicht. Und etwa die Hälfte aller Domains "
            "lässt sich per E-Mail nicht prüfen."
        ),
        "category": "verification",
        "value_type": "text",
        "collector": "profile",
        "cost_note": "kostenlos (HTML der Website)",
        "confidence_floor": 0.0,
    },
]


async def seed_signal_catalog(state) -> int:
    """Register the catalog as PROPOSED signals awaiting user confirmation.

    Idempotent, and never overrides a decision the user already made: an
    existing signal keeps its confirmed/rejected status.
    """
    for sig in SIGNAL_CATALOG:
        await state.upsert_signal_code(
            sig["code"],
            label=sig["label"],
            description=sig["description"],
            category=sig["category"],
            value_type=sig["value_type"],
            collector=sig["collector"],
            cost_note=sig["cost_note"],
            confidence_floor=sig.get("confidence_floor", 0.0),
        )
    return len(SIGNAL_CATALOG)

"""Approval or autopilot, picked up while Harvey runs.

The dashboard switch (or a hand edit) changes channels.email.require_approval
in the config file. Whatever part of Harvey notices first, the loop, the
sender, the writer or the handler, calls sync_sending_mode, so going back to
approval always brings what autopilot approved (and hasn't sent) back to the
Outbox, whoever noticed.
"""

import logging

from harvey.config import refresh_sending_mode

logger = logging.getLogger("harvey.sending_mode")


async def sync_sending_mode(config, state) -> bool:
    """Bring ``config`` in line with its file. True when the mode changed."""
    if not refresh_sending_mode(config):
        return False
    if config.channels.email.require_approval:
        back = await state.requeue_autopilot_approved()
        logger.warning("Sending mode is now APPROVAL: every email waits in the Outbox for you."
                       + (f" {back} queued by autopilot came back to the Outbox." if back else ""))
    else:
        logger.warning("Sending mode is now AUTOPILOT: Harvey sends on its own "
                       "(emails it holds still wait for you).")
    return True


SIGNATURE_HOLD_PREFIX = "email signature: "


def held_message(hold: dict, waiting: int) -> tuple[str, str]:
    """What Today and `harvey status` say while sending is held because
    replies couldn't all be read or handled: (title, detail)."""
    reason = str(hold.get("reason") or "nicht alle Antworten konnten gelesen werden").strip().rstrip(".")
    low = reason.lower()
    emails = "1 E-Mail wartet" if waiting == 1 else f"{waiting} E-Mails warten"
    if low.startswith(SIGNATURE_HOLD_PREFIX):
        return ("Versand pausiert: Dein E-Mail-Logo lässt sich nicht verwenden",
                f"{reason[len(SIGNATURE_HOLD_PREFIX):]}. Reparier die Datei oder leere "
                f"`channels.email.signature.logo`, dann geht reiner Text raus. {emails}.")
    if low.startswith("a reply from"):
        # The inbox was read; handling one reply failed. Its text and the
        # sender's address are no guide to a fix, so match nothing in it.
        title = "Versand pausiert: Harvey konnte eine Antwort nicht verarbeiten"
        fix = "Harvey versucht es in jeder Runde neu. Wenn das anhält, schau ins Log."
    else:
        title = "Versand pausiert: Harvey konnte dein Postfach nicht lesen"
        # Only the providers' own messages name a fix.
        if "harvey gmail auth" in low:
            fix = "Führ `harvey gmail auth` aus und melde dich neu an."
        elif "harvey microsoft auth" in low:
            fix = "Führ `harvey microsoft auth` aus und melde dich neu an."
        elif "refusing a partial read" in low:
            fix = ("Im Postfach kam letzte Woche mehr Mail an, als Harvey liest. Es kann also "
                   "nicht sicher sein, jede Antwort gesehen zu haben. Versende von einem eigenen "
                   "Postfach oder archivier alte Mails.")
        else:
            fix = "Harvey versucht es in jeder Runde neu. Wenn das anhält, schau ins Log."
    return (title,
            f"{reason}. {fix} {emails}: Antworten werden zuerst gelesen, damit eine "
            f"Abmeldung das Follow-up stoppt, bevor etwas rausgeht. Pausiert seit "
            f"{str(hold.get('since', '?'))[:16].replace('T', ' ')} UTC.")

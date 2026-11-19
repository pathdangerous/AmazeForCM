"""What the first-load step shows about the feed's scheduled loads (phase 7.8 follow-up, item 2).

One rule: the facts, the message and the next action all describe the feed's LATEST scheduled load, read in the same
call from the database, so they cannot disagree. Earlier loads are lines in the timeline. Deterministic: no model
call, no file read. Reads only; the caller owns the transaction.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from core.domain.load import Load
from core.domain.onboarding import InputFact, TimelineEntry
from core.repositories.certifications import LoadCertificationRepository
from core.repositories.loads import LoadRepository
from core.repositories.products import ProductRepository


def scheduled_loads(session: Session, feed_id: UUID) -> list[Load]:
    """The feed's scheduled loads, newest first (a go-live's test load is not a delivery)."""
    return [x for x in LoadRepository(session).list_for_feed(feed_id) if x.purpose == "scheduled"]


def latest_scheduled_load(session: Session, feed_id: UUID) -> Load | None:
    loads = scheduled_loads(session, feed_id)
    return loads[0] if loads else None


def first_load_facts(session: Session, feed_id: UUID) -> list[InputFact]:
    """The latest scheduled load's facts; a fact that does not exist yet is left out."""
    load = latest_scheduled_load(session, feed_id)
    if load is None:
        return []
    facts = [InputFact(label="Load", value=load.status.replace("_", " "))]
    certification = LoadCertificationRepository(session).for_load(load.id)
    if certification is not None:
        facts += [InputFact(label="Rows loaded", value=str(certification.rows)),
                  InputFact(label="Rows flagged", value=str(certification.flagged_rows)),
                  InputFact(label="Trust score", value=f"{certification.trust_score:.1f}"),
                  InputFact(label="Outcome", value=certification.outcome.replace("_", " "))]
    product = ProductRepository(session).for_feed(feed_id)
    if product is not None:
        facts.append(InputFact(label="Product", value=product.code))
    return facts


def latest_load_summary(session: Session, feed_id: UUID) -> str | None:
    """One sentence about the latest scheduled load, from the same rows as the facts."""
    load = latest_scheduled_load(session, feed_id)
    if load is None:
        return None
    certification = LoadCertificationRepository(session).for_load(load.id)
    if load.status == "held" and certification is not None:
        return (f"The latest load ended held: its trust score {certification.trust_score:.1f} is below the release "
                f"threshold {certification.threshold:.1f} ({certification.flagged_rows} of {certification.rows} rows "
                "were flagged by a rule), and its consumers were warned. The feed's product was not created.")
    if load.status == "released" and certification is not None:
        return (f"The latest load was released: trust score {certification.trust_score:.1f} "
                f"({certification.flagged_rows} of {certification.rows} rows flagged).")
    if load.status == "quarantined":
        return "The latest load was quarantined: the same file was loaded before, so it was not loaded again."
    if load.status == "failed_rolled_back":
        where = f" at the {load.failed_stage} stage" if load.failed_stage else ""
        why = f": {load.failure}" if load.failure else ""
        return f"The latest load failed{where} and was rolled back{why}. Nothing from it was kept."
    if load.status == "abandoned":
        return "The latest load was abandoned: the process that ran it stopped before it finished."
    return f"The latest load is {load.status.replace('_', ' ')}."


def load_timeline(session: Session, feed_id: UUID) -> list[TimelineEntry]:
    """One line per scheduled load of the feed (the latest too), in time order."""
    certifications = LoadCertificationRepository(session)
    entries = []
    for load in scheduled_loads(session, feed_id):
        detail: dict = {"status": load.status, "file_ref": load.file_ref, "arrival": load.arrival_outcome}
        certification = certifications.for_load(load.id)
        if certification is not None:
            detail |= {"rows": certification.rows, "flagged_rows": certification.flagged_rows,
                       "trust_score": f"{certification.trust_score:.1f}", "outcome": certification.outcome}
        entries.append(TimelineEntry(at=load.finished_at or load.received_at, kind="event", actor="system",
                                     step="first_load", title=f"load {load.status.replace('_', ' ')}",
                                     proposal_id=None, detail=detail))
    return sorted(entries, key=lambda entry: entry.at)

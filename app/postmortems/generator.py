"""Build a postmortem out of a stored incident.

The Postmortem page used to assemble the whole document in the browser. Two of
its four sections were string literals — Root Cause Analysis was always

    "The incident was caused by an issue that requires further investigation.
     Initial analysis suggests the root cause may be related to recent changes
     or increased load."

and Lessons Learned was always the same What Went Well / What Could Be Improved
block — as were both "action items", and all of it was rendered under an "AI
Generated" badge. `regenerateSection` slept 2000ms and changed nothing. There
was no postmortem route at all: `GET /api/postmortems -> 404`, and the only
"postmortem" path in the route dump was the Slack slash command, which replies
"Generating Postmortem…" and generates nothing.

This module produces the document instead, and splits it in two:

* The **facts** — timeline, durations, affected service, who acknowledged and
  resolved it — are read straight off the incident row. Nothing is invented and
  nothing is rounded up: a duration whose endpoint was never recorded comes back
  as ``None``, not ``0``, because "time to detect: 0m" is a claim and "—" is not.
* The **narrative** — the four prose sections and the action items — is written
  by the configured LLM from exactly those facts, with the stored investigation
  root cause handed to it when one exists.

If no LLM key is configured there is nothing to write the narrative, so
generation fails with a 503 naming the missing variable. Returning the old
template instead is what made a canned paragraph look like analysis.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.incidents.models import Incident

log = logging.getLogger("omnipotent.postmortems")

#: The prose sections of the document, in display order.
SECTION_TITLES: dict[str, str] = {
    "summary": "Executive Summary",
    "impact": "Impact",
    "root_cause": "Root Cause Analysis",
    "lessons": "Lessons Learned",
}
SECTION_IDS: tuple[str, ...] = tuple(SECTION_TITLES)

#: incident severity -> the SEV label the postmortem is filed under.
_SEVERITY_LABEL = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}

#: Timeline entry types the store writes -> the category the UI colours by.
_TIMELINE_CATEGORY = {
    "triggered": "detection",
    "acknowledged": "action",
    "status_change": "action",
    "investigation_started": "action",
    "investigation_completed": "action",
    "investigation_failed": "action",
    "resolved": "resolution",
}

#: The env var that holds each provider's key, for the 503 message.
_PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class PostmortemUnavailable(RuntimeError):
    """No model is configured, so the narrative cannot be written.

    Carries the configuration that is missing so the API can name it rather than
    reporting a generic failure.
    """

    def __init__(self, message: str, missing: list[str]) -> None:
        super().__init__(message)
        self.missing = missing


class PostmortemGenerationFailed(RuntimeError):
    """The model answered, but not with a postmortem we can use."""


def _minutes_between(start: datetime | None, end: datetime | None) -> int | None:
    """Whole minutes from `start` to `end`, or None if either is unrecorded.

    None is the honest answer for a duration one of whose endpoints was never
    written down. The page previously coerced these to 0 and printed "0m",
    which reads as "instant" rather than "unknown".
    """
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    if delta < 0:
        return None
    return int(delta // 60)


def _mitigated_at(incident: Incident) -> datetime | None:
    """When the incident was recorded as mitigated, or None.

    The store has no "mitigated" field; the closest recorded fact is the status
    moving to `monitoring`, which by convention means the fix is in and being
    watched. If that transition was never recorded there is nothing to measure,
    and reporting the resolution time under a second name would turn one
    measurement into two.
    """
    for entry in sorted(incident.timeline, key=lambda e: e.timestamp):
        if entry.type == "status_change" and entry.message.strip().endswith("monitoring"):
            return entry.timestamp
    return None


def incident_timeline(incident: Incident) -> list[dict[str, Any]]:
    """The incident's recorded timeline, in the shape the page renders.

    These are the rows the incident store actually wrote. The old page
    synthesised three events ("detected"/"acknowledged"/"resolved") from the
    incident's timestamps regardless of what happened.
    """
    return [
        {
            "timestamp": entry.timestamp.isoformat(),
            "event": entry.message,
            "actor": entry.user,
            "type": _TIMELINE_CATEGORY.get(entry.type, "action"),
            "raw_type": entry.type,
        }
        for entry in sorted(incident.timeline, key=lambda e: e.timestamp)
    ]


def incident_metrics(incident: Incident) -> dict[str, Any]:
    """The response metrics that can be derived from the incident record.

    `time_to_detect` is deliberately always None: the store records when the
    incident was *declared*, never when the underlying fault began, so there is
    no interval to measure. `time_to_mitigate` is only reported when the status
    was actually moved to `monitoring` (see `_mitigated_at`).
    """
    triggered = incident.triggered_at
    mitigated = _mitigated_at(incident)
    return {
        "time_to_detect": None,
        "time_to_acknowledge": _minutes_between(triggered, incident.acknowledged_at),
        "time_to_mitigate": _minutes_between(triggered, mitigated),
        "time_to_resolve": _minutes_between(triggered, incident.resolved_at),
        "affected_services": [incident.service] if incident.service else [],
        "customer_impact": incident.customer_impact,
        "impact": incident.impact,
    }


def incident_facts(incident: Incident) -> dict[str, Any]:
    """Everything known about the incident, as plain JSON.

    This is both what the API returns as the document's factual half and what
    the model is given to write from — one source, so the prose cannot describe
    an incident different from the one on screen.
    """
    investigation = incident.investigation
    findings: list[dict[str, Any]] = []
    if investigation is not None:
        for agent_type, agent in investigation.agents.items():
            findings.extend(
                {
                    "agent": agent_type,
                    "finding": f.finding,
                    "confidence": f.confidence,
                }
                for f in agent.findings
            )
    return {
        "incident_id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "service": incident.service,
        "severity": incident.severity.value,
        "severity_label": _SEVERITY_LABEL.get(incident.severity.value, "SEV4"),
        "status": incident.status.value,
        "triggered_at": incident.triggered_at.isoformat(),
        "acknowledged_at": (
            incident.acknowledged_at.isoformat() if incident.acknowledged_at else None
        ),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "acknowledged_by": incident.acknowledged_by,
        "resolved_by": incident.resolved_by,
        "assignee": incident.assignee,
        "tags": list(incident.tags),
        "metrics": incident_metrics(incident),
        "timeline": incident_timeline(incident),
        "investigation": (
            {
                "root_cause": investigation.root_cause,
                "confidence": investigation.overall_confidence,
                "recommended_actions": list(investigation.recommended_actions),
                "findings": findings,
            }
            if investigation is not None
            else None
        ),
    }


def _authors(incident: Incident) -> list[str]:
    """The people the record shows actually worked the incident.

    Empty when nobody was recorded. The old page listed "On-Call Engineer" and
    "Team Lead" — two job titles it made up — as the authors and reviewers of
    every postmortem.
    """
    names = [incident.acknowledged_by, incident.resolved_by, incident.assignee]
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


_SYSTEM = """You are an SRE writing an incident postmortem. You are given the \
complete, verified record of one incident as JSON: its timeline, timestamps, \
severity, affected service, and — when one ran — the investigation's root cause \
and per-agent findings.

Rules:
- Use only what the record contains. Do not invent causes, metrics, deploys, \
customers or people that are not in it.
- When the record does not establish something, say plainly that it is not \
established and name what evidence would settle it. An honest "unknown" is the \
correct answer; a plausible guess is not.
- When the record carries an investigation root cause, that is the root cause; \
explain it, do not replace it.
- Write markdown. No headings inside a section — the section already has a title.

Reply with a single JSON object and nothing else. Keys: %s. Each value is that \
section's markdown text.%s"""

_ACTION_ITEMS_CLAUSE = """
Also include "action_items": a list (possibly empty) of objects with keys \
"title", "owner", "priority" (P0|P1|P2|P3) and "type" (prevent|detect|mitigate). \
Every item must follow from the record. "owner" must be a person named in the \
record, or "" when nobody is."""


def _prompt(sections: tuple[str, ...], want_actions: bool) -> str:
    keys = ", ".join(f'"{s}" ({SECTION_TITLES[s]})' for s in sections)
    return _SYSTEM % (keys, _ACTION_ITEMS_CLAUSE if want_actions else "")


def _parse(text: str) -> dict[str, Any]:
    """Read the model's reply as a JSON object, tolerating a ```json fence."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        raise PostmortemGenerationFailed("the model did not return a JSON object")
    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PostmortemGenerationFailed(f"the model's reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PostmortemGenerationFailed("the model did not return a JSON object")
    return parsed


def _require_model() -> Any:
    """The chat model to write with, or PostmortemUnavailable naming what is missing."""
    from app import runtime
    from app.llm import get_llm

    provider = runtime.provider()
    if not runtime.provider_key(provider):
        env = _PROVIDER_ENV.get(provider, "the provider's API key")
        raise PostmortemUnavailable(
            f"Postmortem generation needs an LLM: no API key is configured for "
            f"provider '{provider}'. Set {env} (or switch COPILOT_PROVIDER to a "
            f"provider you have a key for). The incident record is still readable "
            f"at /api/incidents/{{incident_id}}.",
            [env],
        )
    try:
        return get_llm(fast=True)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as a 503
        raise PostmortemUnavailable(
            f"Postmortem generation needs an LLM, and building the '{provider}' "
            f"client failed: {exc}",
            [_PROVIDER_ENV.get(provider, provider)],
        ) from exc


async def _write_narrative(
    facts: dict[str, Any], sections: tuple[str, ...], want_actions: bool
) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = _require_model()
    messages = [
        SystemMessage(content=_prompt(sections, want_actions)),
        HumanMessage(content=json.dumps(facts, indent=2, default=str)),
    ]
    try:
        # `.invoke` is a blocking HTTP call; off the event loop it would stall
        # every other request for the length of a model round trip.
        response = await asyncio.to_thread(llm.invoke, messages)
    except Exception as exc:  # noqa: BLE001 — the provider call itself failed
        raise PostmortemGenerationFailed(f"the model call failed: {exc}") from exc
    parsed = _parse(str(getattr(response, "content", response)))
    missing = [s for s in sections if not str(parsed.get(s) or "").strip()]
    if missing:
        raise PostmortemGenerationFailed(
            f"the model omitted section(s): {', '.join(missing)}"
        )
    return parsed


def _action_items(raw: Any, incident: Incident) -> list[dict[str, Any]]:
    """Normalise the model's action items, dropping anything malformed.

    No due dates are attached: nobody has committed to one, and the old page
    stamped every item with now+7d / now+14d as if a date had been agreed.
    """
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        priority = str(entry.get("priority") or "P2").upper()
        kind = str(entry.get("type") or "prevent").lower()
        items.append({
            "id": f"{incident.id}-action-{i + 1}",
            "title": title,
            "owner": str(entry.get("owner") or "").strip(),
            "priority": priority if priority in {"P0", "P1", "P2", "P3"} else "P2",
            "status": "open",
            "due_date": None,
            "type": kind if kind in {"prevent", "detect", "mitigate"} else "prevent",
        })
    return items


async def generate_postmortem(
    incident: Incident, sections: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Generate the postmortem for `incident`.

    `sections` limits generation to a subset — that is what the page's
    per-section Regenerate button sends, so it re-runs one section instead of
    paying for the whole document. Action items are only produced for a full
    generation.
    """
    wanted = tuple(sections) if sections else SECTION_IDS
    unknown = [s for s in wanted if s not in SECTION_TITLES]
    if unknown:
        raise ValueError(
            f"unknown section(s): {', '.join(unknown)}; "
            f"supported: {', '.join(SECTION_IDS)}"
        )
    full = set(wanted) == set(SECTION_IDS)
    facts = incident_facts(incident)
    narrative = await _write_narrative(facts, wanted, want_actions=full)

    now = datetime.now(timezone.utc)
    document: dict[str, Any] = {
        "id": f"pm-{incident.id}",
        "incident_id": str(incident.id),
        "title": incident.title,
        "status": "draft",
        "severity": facts["severity_label"],
        "generated_at": now.isoformat(),
        "authors": _authors(incident),
        "reviewers": [],
        "sections": [
            {
                "id": section,
                "title": SECTION_TITLES[section],
                "content": str(narrative[section]).strip(),
                "source": "model",
            }
            for section in wanted
        ],
        "timeline": facts["timeline"],
        "metrics": facts["metrics"],
    }
    if full:
        document["action_items"] = _action_items(narrative.get("action_items"), incident)
    return document

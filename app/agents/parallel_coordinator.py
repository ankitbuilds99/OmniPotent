"""ParallelInvestigationCoordinator — runs 6 specialist agents concurrently.

Architecture 3 (Omnipotent) implementation:
- Spawns 6 agents in parallel: Telemetry, Code, Dependencies, Historical, Knowledge, Collaboration
- Collects findings concurrently with timeout
- Synthesizes into unified root cause hypothesis
- Returns confidence score with transparent factors
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import anthropic

from app import model_catalog
from app.agents.protocol import SharedContext
from app.config import get_settings

log = logging.getLogger("omnipotent.agents.parallel_coordinator")


class SpecialistType(str, Enum):
    """The 6 parallel specialists for investigation."""
    TELEMETRY = "telemetry"
    CODE = "code"
    DEPENDENCIES = "dependencies"
    HISTORICAL = "historical"
    KNOWLEDGE = "knowledge"
    COLLABORATION = "collaboration"


@dataclass
class Finding:
    """A single finding from a specialist agent."""
    finding_type: str  # anomaly, correlation, hypothesis, evidence
    title: str
    description: str
    confidence_score: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    severity: str | None = None


@dataclass
class SpecialistResult:
    """Result from a single specialist agent."""
    specialist_type: SpecialistType
    status: str  # completed, failed, timeout
    findings: list[Finding] = field(default_factory=list)
    confidence_score: float = 0.0
    reasoning: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class ConfidenceFactor:
    """A factor contributing to the overall confidence score."""
    name: str
    score: float
    weight: float
    explanation: str


@dataclass
class ParallelInvestigationResult:
    """Result from parallel investigation with all 4 specialists."""
    session_id: UUID
    status: str
    root_cause_hypothesis: str
    confidence_score: float
    confidence_factors: list[ConfidenceFactor]
    summary: str
    all_findings: list[Finding]
    suggested_actions: list[dict[str, Any]]
    specialist_results: dict[str, SpecialistResult]
    total_duration_ms: int
    total_cost_usd: Decimal
    alternative_hypotheses: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": str(self.session_id),
            "status": self.status,
            "root_cause_hypothesis": self.root_cause_hypothesis,
            "confidence_score": self.confidence_score,
            "confidence_factors": [
                {"name": f.name, "weight": f.weight, "score": f.score, "explanation": f.explanation}
                for f in self.confidence_factors
            ],
            "summary": self.summary,
            "all_findings": [asdict(f) for f in self.all_findings],
            "suggested_actions": self.suggested_actions,
            "specialist_results": {
                k: asdict(v) for k, v in self.specialist_results.items()
            },
            "total_duration_ms": self.total_duration_ms,
            "total_cost_usd": float(self.total_cost_usd),
            "alternative_hypotheses": self.alternative_hypotheses,
        }


# Specialist agent prompts
TELEMETRY_PROMPT = """You are a Telemetry Analysis Specialist for incident investigation.

Your job: Analyze metrics, logs, and traces to find anomalies and correlations.

## What to Look For:
1. **Metric Anomalies**: Spikes/drops in latency, error rates, throughput, CPU, memory
2. **Log Patterns**: Error messages, stack traces, warning frequencies
3. **Trace Analysis**: Slow spans, failed spans, unusual call patterns
4. **Correlations**: Metrics that changed around the incident time

## Incident Context:
{context}

## Output Format (JSON):
{{
    "findings": [
        {{
            "finding_type": "anomaly|correlation|evidence",
            "title": "Brief title",
            "description": "Detailed description with specific values",
            "confidence_score": 0.0-1.0,
            "evidence": [
                {{"type": "metric|log|trace", "source": "...", "value": "...", "timestamp": "..."}}
            ],
            "affected_services": ["service1"],
            "severity": "critical|high|medium|low"
        }}
    ],
    "summary": "1-2 sentence summary",
    "confidence_score": 0.0-1.0,
    "reasoning": "Why this confidence level"
}}

Be specific with timestamps, values, and sources. If data is limited, lower confidence accordingly."""


CODE_PROMPT = """You are a Code Change Specialist for incident investigation.

Your job: Identify code changes that may have caused the incident.

## What to Look For:
1. **Recent Deployments**: What deployed in the last 24h?
2. **Suspicious Commits**: Changes to critical paths, error handling, configs
3. **Deployment Timing**: Did symptoms start after a specific deploy?
4. **Rollback Candidates**: Which deploys could be rolled back?

## Incident Context:
{context}

## Output Format (JSON):
{{
    "findings": [
        {{
            "finding_type": "hypothesis|evidence|correlation",
            "title": "Brief title",
            "description": "Detailed description",
            "confidence_score": 0.0-1.0,
            "evidence": [
                {{"type": "commit|deployment|pr", "sha": "...", "author": "...", "message": "...", "timestamp": "..."}}
            ],
            "affected_services": ["service1"],
            "severity": "critical|high|medium|low"
        }}
    ],
    "summary": "1-2 sentence summary",
    "confidence_score": 0.0-1.0,
    "reasoning": "Why this confidence level"
}}

Focus on correlation between deployment times and incident onset."""


DEPENDENCIES_PROMPT = """You are a Dependencies Specialist for incident investigation.

Your job: Identify issues in service dependencies and infrastructure.

## What to Look For:
1. **Upstream Services**: Are dependencies unhealthy or slow?
2. **Database Issues**: Connection pools, slow queries, locks, replication lag
3. **API Failures**: Rate limits, auth issues, timeout patterns
4. **Infrastructure**: DNS, load balancers, network partitions, cloud issues

## Incident Context:
{context}

## Output Format (JSON):
{{
    "findings": [
        {{
            "finding_type": "anomaly|hypothesis|evidence",
            "title": "Brief title",
            "description": "Detailed description",
            "confidence_score": 0.0-1.0,
            "evidence": [
                {{"type": "service|database|api|infra", "name": "...", "status": "...", "details": "..."}}
            ],
            "affected_services": ["service1"],
            "severity": "critical|high|medium|low"
        }}
    ],
    "summary": "1-2 sentence summary",
    "confidence_score": 0.0-1.0,
    "reasoning": "Why this confidence level"
}}

Check all layers: services, databases, caches, queues, external APIs."""


HISTORICAL_PROMPT = """You are a Historical Analysis Specialist for incident investigation.

Your job: Find similar past incidents and relevant runbooks.

## What to Look For:
1. **Similar Incidents**: Past incidents with matching symptoms, services, or patterns
2. **Proven Remediations**: What fixed similar issues before?
3. **Runbooks**: Relevant playbooks for this type of incident
4. **Patterns**: Is this recurring? What's the MTTR history?

## Incident Context:
{context}

## Output Format (JSON):
{{
    "findings": [
        {{
            "finding_type": "correlation|hypothesis|evidence",
            "title": "Brief title",
            "description": "Detailed description",
            "confidence_score": 0.0-1.0,
            "evidence": [
                {{"type": "past_incident|runbook|pattern", "id": "...", "title": "...", "resolution": "...", "similarity_score": 0.0-1.0}}
            ],
            "affected_services": ["service1"],
            "severity": "critical|high|medium|low"
        }}
    ],
    "summary": "1-2 sentence summary",
    "confidence_score": 0.0-1.0,
    "reasoning": "Why this confidence level"
}}

Rank past incidents by similarity. Include what remediation worked."""


KNOWLEDGE_PROMPT = """You are a Knowledge Base Specialist for incident investigation.

Your job: Search documentation, wikis, architecture docs, and internal knowledge bases for relevant context.

## What to Look For:
1. **Architecture Docs**: System design, service dependencies, data flow diagrams
2. **Service Documentation**: API contracts, SLOs, known limitations
3. **Change Documentation**: Recent architecture changes, migrations, deprecations
4. **Operational Knowledge**: Known gotchas, anti-patterns, tribal knowledge

## Incident Context:
{context}

## Output Format (JSON):
{{
    "findings": [
        {{
            "finding_type": "context|evidence|hypothesis",
            "title": "Brief title",
            "description": "Detailed description with specific references",
            "confidence_score": 0.0-1.0,
            "evidence": [
                {{"type": "doc|wiki|architecture|confluence", "title": "...", "url": "...", "excerpt": "...", "relevance": "high|medium|low"}}
            ],
            "affected_services": ["service1"],
            "severity": "critical|high|medium|low"
        }}
    ],
    "summary": "1-2 sentence summary",
    "confidence_score": 0.0-1.0,
    "reasoning": "Why this confidence level"
}}

Include specific document references and excerpts. Highlight any documented known issues."""


COLLABORATION_PROMPT = """You are a Collaboration Specialist for incident investigation.

Your job: Identify who knows what, coordinate with SMEs, and gather human context.

## What to Look For:
1. **Service Owners**: Who owns the affected services? Who's the on-call?
2. **Recent Activity**: Who made recent changes to affected systems?
3. **SME Identification**: Who are the subject matter experts for this failure mode?
4. **Communication History**: Any relevant Slack threads, PR discussions, or meeting notes?

## Incident Context:
{context}

## Output Format (JSON):
{{
    "findings": [
        {{
            "finding_type": "context|recommendation|evidence",
            "title": "Brief title",
            "description": "Detailed description",
            "confidence_score": 0.0-1.0,
            "evidence": [
                {{"type": "person|channel|thread|discussion", "name": "...", "role": "...", "relevance": "...", "contact": "..."}}
            ],
            "affected_services": ["service1"],
            "severity": "critical|high|medium|low"
        }}
    ],
    "summary": "1-2 sentence summary",
    "recommended_contacts": [
        {{"name": "...", "role": "...", "reason": "Why they should be involved"}}
    ],
    "confidence_score": 0.0-1.0,
    "reasoning": "Why this confidence level"
}}

Prioritize people who can provide immediate context or take remediation action."""


SYNTHESIS_PROMPT = """You are the Investigation Coordinator synthesizing findings from 6 specialists.

## Specialist Findings:

### Telemetry Specialist (metrics, logs, traces):
{telemetry}

### Code Specialist (deployments, commits):
{code}

### Dependencies Specialist (services, databases, APIs):
{dependencies}

### Historical Specialist (past incidents, runbooks):
{historical}

### Knowledge Specialist (docs, architecture, wikis):
{knowledge}

### Collaboration Specialist (people, SMEs, communication):
{collaboration}

## Your Task:
1. Synthesize findings into a unified root cause hypothesis
2. Calculate overall confidence (0.0-1.0) based on evidence quality
3. Recommend specific actions

## Output Format (JSON):
{{
    "root_cause_hypothesis": "Clear statement of the most likely root cause",
    "confidence_score": 0.0-1.0,
    "confidence_reasoning": "Why this confidence based on evidence quality",
    "summary": "2-3 sentence executive summary",
    "key_evidence": [
        {{"source": "specialist_type", "finding": "...", "weight": "high|medium|low"}}
    ],
    "suggested_actions": [
        {{
            "action_type": "rollback|scale|restart|fix|investigate",
            "title": "Action title",
            "description": "Detailed steps",
            "target_service": "service_name",
            "priority": 1,
            "estimated_impact": "high|medium|low",
            "requires_approval": true
        }}
    ],
    "alternative_hypotheses": [
        {{"hypothesis": "...", "confidence": 0.0-1.0, "why_less_likely": "..."}}
    ],
    "gaps_in_investigation": ["What data was missing or unclear"]
}}

Be decisive. Pick the most likely hypothesis and defend it."""


class ParallelInvestigationCoordinator:
    """Orchestrates 4 specialist agents running in parallel.

    This is the core investigation engine for Architecture 3 (Omnipotent).
    It spawns 4 specialists concurrently, collects their findings,
    and synthesizes them into a unified root cause hypothesis.
    """

    # Model pricing per 1M tokens. IDs only — the dated snapshots this
    # coordinator sends and has sent historically; every rate comes from
    # app/model_catalog.py so this table cannot drift from the ledger's. A
    # snapshot the catalog cannot price would be omitted rather than priced by
    # hand here.
    MODEL_PRICING = {
        model_id: {"input": rates[0], "output": rates[1]}
        for model_id, rates in (
            (m, model_catalog.price_per_1m(m))
            for m in (
                "claude-sonnet-4-20250514",
                "claude-3-5-sonnet-20241022",
                "claude-3-haiku-20240307",
                "claude-3-opus-20240229",
            )
        )
        if rates is not None
    }

    # Specialist prompts
    SPECIALIST_PROMPTS = {
        SpecialistType.TELEMETRY: TELEMETRY_PROMPT,
        SpecialistType.CODE: CODE_PROMPT,
        SpecialistType.DEPENDENCIES: DEPENDENCIES_PROMPT,
        SpecialistType.HISTORICAL: HISTORICAL_PROMPT,
        SpecialistType.KNOWLEDGE: KNOWLEDGE_PROMPT,
        SpecialistType.COLLABORATION: COLLABORATION_PROMPT,
    }

    # Pinned to a dated snapshot deliberately: specialist prompts here expect a
    # stable model, and a floating alias would silently change their output. It is
    # validated against the catalog at import so a retired pin can't go unnoticed.
    DEFAULT_MODEL = "claude-sonnet-4-20250514"

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        model: str = "",
    ):
        """Initialize the coordinator.

        Args:
            client: Anthropic async client (created if not provided)
            model: Model for the specialist agents; falls back to COPILOT_MODEL,
                then to the pinned DEFAULT_MODEL.
        """
        self._client = client
        self._model = model or get_settings().copilot_model or self.DEFAULT_MODEL
        info = model_catalog.resolve(self._model)
        if info is None:
            log.warning(
                "model %r is not in the model catalog — check for a typo", self._model
            )
        elif info.status == "retired":
            log.warning(
                "model %r is retired and will start failing; update DEFAULT_MODEL "
                "or COPILOT_MODEL", self._model,
            )

    def _max_tokens(self) -> int:
        """Output-token ceiling for one specialist call.

        Read at call time from LLM_MAX_TOKENS. Previously this was hardcoded to
        4096 in two places, so raising the setting had no effect here and the two
        call sites could drift apart.
        """
        return get_settings().llm_max_tokens

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Get or create the Anthropic client."""
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def investigate(
        self,
        incident_id: UUID,
        organization_id: UUID,
        context: dict[str, Any],
        timeout_seconds: int = 120,
    ) -> ParallelInvestigationResult:
        """Run parallel investigation with all 4 specialists.

        Args:
            incident_id: ID of the incident being investigated
            organization_id: Tenant ID
            context: Evidence collected from data sources
            timeout_seconds: Max time for entire investigation

        Returns:
            Synthesized investigation result
        """
        session_id = uuid4()
        start_time = time.time()

        log.info(
            "Starting parallel investigation session=%s incident=%s",
            session_id, incident_id,
        )

        # Select model based on severity
        severity = context.get("severity", "medium")
        model = self._select_model(severity)

        # Run all 6 specialists in parallel
        specialist_tasks = [
            self._run_specialist(SpecialistType.TELEMETRY, context, model),
            self._run_specialist(SpecialistType.CODE, context, model),
            self._run_specialist(SpecialistType.DEPENDENCIES, context, model),
            self._run_specialist(SpecialistType.HISTORICAL, context, model),
            self._run_specialist(SpecialistType.KNOWLEDGE, context, model),
            self._run_specialist(SpecialistType.COLLABORATION, context, model),
        ]

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*specialist_tasks, return_exceptions=True),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning("Parallel investigation timed out after %ds", timeout_seconds)
            results = [
                SpecialistResult(
                    specialist_type=t,
                    status="timeout",
                    error=f"Investigation timed out after {timeout_seconds}s",
                )
                for t in SpecialistType
            ]

        # Process results (handle exceptions)
        specialist_results: dict[str, SpecialistResult] = {}
        for specialist_type, result in zip(SpecialistType, results):
            if isinstance(result, BaseException):
                specialist_results[specialist_type.value] = SpecialistResult(
                    specialist_type=specialist_type,
                    status="failed",
                    error=str(result),
                )
            else:
                specialist_results[specialist_type.value] = result

        # Synthesize findings from all specialists
        synthesis = await self._synthesize_findings(specialist_results, model)

        # Calculate confidence with transparent factors
        confidence_score, confidence_factors = self._calculate_confidence(
            specialist_results, synthesis
        )

        # Collect all findings
        all_findings: list[Finding] = []
        for result in specialist_results.values():
            all_findings.extend(result.findings)

        # Calculate totals
        total_duration_ms = int((time.time() - start_time) * 1000)
        total_input_tokens = sum(r.input_tokens for r in specialist_results.values())
        total_output_tokens = sum(r.output_tokens for r in specialist_results.values())
        total_cost = self._calculate_cost(model, total_input_tokens, total_output_tokens)

        log.info(
            "Parallel investigation complete session=%s duration=%dms confidence=%.2f",
            session_id, total_duration_ms, confidence_score,
        )

        return ParallelInvestigationResult(
            session_id=session_id,
            status="completed",
            root_cause_hypothesis=synthesis.get("root_cause_hypothesis", "Unknown"),
            confidence_score=confidence_score,
            confidence_factors=confidence_factors,
            summary=synthesis.get("summary", ""),
            all_findings=all_findings,
            suggested_actions=synthesis.get("suggested_actions", []),
            specialist_results=specialist_results,
            total_duration_ms=total_duration_ms,
            total_cost_usd=total_cost,
            alternative_hypotheses=synthesis.get("alternative_hypotheses", []),
        )

    async def _run_specialist(
        self,
        specialist_type: SpecialistType,
        context: dict[str, Any],
        model: str,
    ) -> SpecialistResult:
        """Run a single specialist agent.

        Args:
            specialist_type: Which specialist to run
            context: Evidence context
            model: Model to use

        Returns:
            Specialist result with findings
        """
        start_time = time.time()
        log.debug("Running specialist: %s", specialist_type.value)

        # Build context specific to this specialist
        specialist_context = self._build_specialist_context(specialist_type, context)
        prompt = self.SPECIALIST_PROMPTS[specialist_type].format(context=specialist_context)

        try:
            response = await self.client.messages.create(
                model=model,
                max_tokens=self._max_tokens(),
                system="You are an expert incident investigator. Respond with valid JSON only.",
                messages=[{"role": "user", "content": prompt}],
            )

            # Parse response
            content = getattr(response.content[0], "text", str(response.content[0]))
            parsed = self._parse_json_response(content)

            duration_ms = int((time.time() - start_time) * 1000)

            findings = [
                Finding(
                    finding_type=f.get("finding_type", "evidence"),
                    title=f.get("title", ""),
                    description=f.get("description", ""),
                    confidence_score=f.get("confidence_score", 0.5),
                    evidence=f.get("evidence", []),
                    affected_services=f.get("affected_services", []),
                    severity=f.get("severity"),
                )
                for f in parsed.get("findings", [])
            ]

            return SpecialistResult(
                specialist_type=specialist_type,
                status="completed",
                findings=findings,
                confidence_score=parsed.get("confidence_score", 0.5),
                reasoning=parsed.get("reasoning", ""),
                duration_ms=duration_ms,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log.exception("Specialist %s failed", specialist_type.value)
            return SpecialistResult(
                specialist_type=specialist_type,
                status="failed",
                error=str(e),
                duration_ms=duration_ms,
            )

    def _build_specialist_context(
        self,
        specialist_type: SpecialistType,
        full_context: dict[str, Any],
    ) -> str:
        """Build context string for a specific specialist."""
        import json

        incident_info = f"""
## Incident Details:
- Title: {full_context.get('title', 'Unknown')}
- Severity: {full_context.get('severity', 'Unknown')}
- Triggered At: {full_context.get('triggered_at', 'Unknown')}
- Affected Services: {', '.join(full_context.get('affected_services', []))}
- Description: {full_context.get('description', 'No description')}
"""

        if specialist_type == SpecialistType.TELEMETRY:
            data = full_context.get("telemetry", {})
            return incident_info + f"""
## Metrics (last 1 hour):
{json.dumps(data.get('metrics', {}), indent=2, default=str)}

## Recent Logs:
{data.get('logs', 'No logs available')}

## Traces:
{json.dumps(data.get('traces', []), indent=2, default=str)}
"""

        elif specialist_type == SpecialistType.CODE:
            data = full_context.get("code", {})
            return incident_info + f"""
## Recent Deployments (last 24h):
{json.dumps(data.get('deployments', []), indent=2, default=str)}

## Recent Commits:
{json.dumps(data.get('commits', []), indent=2, default=str)}

## Open PRs:
{json.dumps(data.get('pull_requests', []), indent=2, default=str)}
"""

        elif specialist_type == SpecialistType.DEPENDENCIES:
            data = full_context.get("dependencies", {})
            return incident_info + f"""
## Service Health:
{json.dumps(data.get('services', {}), indent=2, default=str)}

## Database Status:
{json.dumps(data.get('databases', {}), indent=2, default=str)}

## External APIs:
{json.dumps(data.get('apis', {}), indent=2, default=str)}
"""

        elif specialist_type == SpecialistType.HISTORICAL:
            data = full_context.get("historical", {})
            return incident_info + f"""
## Similar Past Incidents:
{json.dumps(data.get('past_incidents', []), indent=2, default=str)}

## Relevant Runbooks:
{json.dumps(data.get('runbooks', []), indent=2, default=str)}

## Service Incident History:
{json.dumps(data.get('service_history', {}), indent=2, default=str)}
"""

        elif specialist_type == SpecialistType.KNOWLEDGE:
            data = full_context.get("knowledge", {})
            return incident_info + f"""
## Architecture Documentation:
{json.dumps(data.get('architecture_docs', []), indent=2, default=str)}

## Service Documentation:
{json.dumps(data.get('service_docs', []), indent=2, default=str)}

## Wiki Pages:
{json.dumps(data.get('wiki_pages', []), indent=2, default=str)}

## Known Issues:
{json.dumps(data.get('known_issues', []), indent=2, default=str)}
"""

        elif specialist_type == SpecialistType.COLLABORATION:
            data = full_context.get("collaboration", {})
            return incident_info + f"""
## Service Owners:
{json.dumps(data.get('service_owners', []), indent=2, default=str)}

## On-Call Schedule:
{json.dumps(data.get('oncall', {}), indent=2, default=str)}

## Recent Contributors:
{json.dumps(data.get('recent_contributors', []), indent=2, default=str)}

## Relevant Discussions:
{json.dumps(data.get('discussions', []), indent=2, default=str)}
"""

        return incident_info

    async def _synthesize_findings(
        self,
        specialist_results: dict[str, SpecialistResult],
        model: str,
    ) -> dict[str, Any]:
        """Synthesize findings from all specialists into unified hypothesis."""

        prompt = SYNTHESIS_PROMPT.format(
            telemetry=self._format_specialist_result(
                specialist_results.get("telemetry")
            ),
            code=self._format_specialist_result(
                specialist_results.get("code")
            ),
            dependencies=self._format_specialist_result(
                specialist_results.get("dependencies")
            ),
            historical=self._format_specialist_result(
                specialist_results.get("historical")
            ),
            knowledge=self._format_specialist_result(
                specialist_results.get("knowledge")
            ),
            collaboration=self._format_specialist_result(
                specialist_results.get("collaboration")
            ),
        )

        try:
            response = await self.client.messages.create(
                model=model,
                max_tokens=self._max_tokens(),
                system="You are an expert incident coordinator. Synthesize findings decisively. Respond with valid JSON.",
                messages=[{"role": "user", "content": prompt}],
            )

            content = getattr(response.content[0], "text", str(response.content[0]))
            return self._parse_json_response(content)

        except Exception as e:
            log.exception("Synthesis failed")
            return {
                "root_cause_hypothesis": f"Synthesis failed: {e}",
                "confidence_score": 0.0,
                "summary": "Unable to synthesize findings",
                "suggested_actions": [],
            }

    def _format_specialist_result(self, result: SpecialistResult | None) -> str:
        """Format specialist result for synthesis prompt."""
        if not result:
            return "No results (specialist did not run)"

        if result.status == "failed":
            return f"Specialist failed: {result.error}"

        if result.status == "timeout":
            return "Specialist timed out"

        if not result.findings:
            return "No findings"

        lines = []
        for f in result.findings:
            lines.append(f"- [{f.confidence_score:.0%}] {f.title}: {f.description}")

        lines.append(f"\nOverall confidence: {result.confidence_score:.0%}")
        lines.append(f"Reasoning: {result.reasoning}")

        return "\n".join(lines)

    def _calculate_confidence(
        self,
        specialist_results: dict[str, SpecialistResult],
        synthesis: dict[str, Any],
    ) -> tuple[float, list[ConfidenceFactor]]:
        """Calculate overall confidence with transparent factors.

        This is the 0.8 threshold gate from Architecture 3.
        """
        factors = []

        # Factor 1: Synthesis confidence (from LLM)
        synthesis_conf = synthesis.get("confidence_score", 0.5)
        factors.append(ConfidenceFactor(
            name="AI Synthesis Confidence",
            score=synthesis_conf,
            weight=0.35,
            explanation=f"LLM synthesis confidence: {synthesis_conf:.0%}",
        ))

        # Factor 2: Specialist agreement (how many found corroborating evidence)
        successful_specialists = [
            r for r in specialist_results.values()
            if r.status == "completed" and r.confidence_score > 0.5
        ]
        agreement_score = len(successful_specialists) / 6.0
        factors.append(ConfidenceFactor(
            name="Specialist Agreement",
            score=agreement_score,
            weight=0.25,
            explanation=f"{len(successful_specialists)}/6 specialists found supporting evidence",
        ))

        # Factor 3: Evidence quality (number of high-confidence findings)
        all_findings = []
        for r in specialist_results.values():
            all_findings.extend(r.findings)
        high_conf_findings = [f for f in all_findings if f.confidence_score >= 0.7]
        evidence_quality = min(len(high_conf_findings) / 5, 1.0)  # Cap at 5 findings
        factors.append(ConfidenceFactor(
            name="Evidence Quality",
            score=evidence_quality,
            weight=0.25,
            explanation=f"{len(high_conf_findings)} high-confidence findings",
        ))

        # Factor 4: Coverage (did all specialists run successfully?)
        coverage = sum(1 for r in specialist_results.values() if r.status == "completed") / 6.0
        factors.append(ConfidenceFactor(
            name="Investigation Coverage",
            score=coverage,
            weight=0.15,
            explanation=f"{int(coverage * 6)}/6 specialists completed successfully",
        ))

        # Calculate weighted average
        total_confidence = sum(f.score * f.weight for f in factors)

        return total_confidence, factors

    def _calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Decimal:
        """Calculate LLM cost.

        Rates come from app/model_catalog.py, which also resolves `-latest`
        aliases and dated snapshots the local table doesn't spell out. A model
        the catalog cannot price is charged at the most expensive rate we know —
        the same guess-high-not-free rule as app/model_router's `_cost_for` — and
        warned about, instead of the old fallback's invented $3/$15.
        """
        rates = model_catalog.price_per_1m(model)
        if rates is None:
            dearest = max(
                self.MODEL_PRICING.values(), key=lambda p: p["input"] + p["output"]
            )
            log.warning(
                "no verified price for model %r; charging it at the most "
                "expensive known rate. Add the model to app/model_catalog.py.",
                model,
            )
            rates = (dearest["input"], dearest["output"])

        input_cost = Decimal(str(input_tokens)) / Decimal("1000000") * Decimal(str(rates[0]))
        output_cost = Decimal(str(output_tokens)) / Decimal("1000000") * Decimal(str(rates[1]))

        return input_cost + output_cost

    def _select_model(self, severity: str) -> str:
        """Select model based on incident severity."""
        if severity in ("critical", "sev1", "SEV1"):
            return "claude-sonnet-4-20250514"
        elif severity in ("high", "sev2", "SEV2"):
            return "claude-sonnet-4-20250514"
        else:
            # Use Haiku for lower severity to save cost
            return "claude-3-haiku-20240307"

    def _parse_json_response(self, content: str) -> dict[str, Any]:
        """Parse JSON from LLM response."""
        import json

        # Try to extract JSON from markdown code blocks
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            content = content[start:end].strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"findings": [], "confidence_score": 0.0}


# Integration with existing SharedContext
async def run_parallel_investigation(
    incident_id: UUID,
    organization_id: UUID,
    context: SharedContext | dict[str, Any],
    timeout_seconds: int = 120,
) -> ParallelInvestigationResult:
    """Run parallel investigation - entry point for integration.

    Args:
        incident_id: Incident being investigated
        organization_id: Tenant ID
        context: Either SharedContext or dict with evidence
        timeout_seconds: Timeout for entire investigation

    Returns:
        ParallelInvestigationResult
    """
    # Convert SharedContext to dict if needed
    if isinstance(context, SharedContext):
        context_dict = {
            "title": context.incident_summary,
            "severity": context.severity,
            "triggered_at": datetime.utcnow().isoformat(),
            "affected_services": context.affected_services,
            "description": context.incident_summary,
            "telemetry": {"metrics": {}, "logs": "", "traces": []},
            "code": {"deployments": [], "commits": []},
            "dependencies": {"services": {}, "databases": {}},
            "historical": {"past_incidents": [], "runbooks": []},
            "knowledge": {"architecture_docs": [], "service_docs": [], "wiki_pages": []},
            "collaboration": {"service_owners": [], "oncall": {}, "recent_contributors": []},
        }
    else:
        context_dict = context

    coordinator = ParallelInvestigationCoordinator()
    return await coordinator.investigate(
        incident_id=incident_id,
        organization_id=organization_id,
        context=context_dict,
        timeout_seconds=timeout_seconds,
    )

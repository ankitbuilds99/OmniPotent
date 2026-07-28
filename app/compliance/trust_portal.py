"""Customer Trust Portal for self-serve compliance documentation.

Provides:
- Self-serve document downloads (with NDA tracking)
- Pre-filled security questionnaires (SIG, CAIQ)
- Live security status
- Vendor risk inheritance
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.compliance.trust_portal")


class DocumentCategory(Enum):
    """Categories of trust documents."""

    COMPLIANCE_REPORT = "compliance_report"
    SECURITY_WHITEPAPER = "security_whitepaper"
    PENETRATION_TEST = "penetration_test"
    POLICY = "policy"
    AGREEMENT = "agreement"
    QUESTIONNAIRE = "questionnaire"
    AUDIT_REPORT = "audit_report"


class AccessLevel(Enum):
    """Access levels for documents."""

    PUBLIC = "public"
    NDA_REQUIRED = "nda_required"
    CUSTOMER_ONLY = "customer_only"
    ENTERPRISE_ONLY = "enterprise_only"


class QuestionnaireType(Enum):
    """Types of security questionnaires."""

    SIG_LITE = "sig_lite"
    SIG_CORE = "sig_core"
    CAIQ = "caiq"
    VSA = "vsa"
    CUSTOM = "custom"


@dataclass
class TrustDocument:
    """A document in the trust portal."""

    document_id: str
    title: str
    description: str
    category: DocumentCategory
    access_level: AccessLevel
    file_path: str
    version: str
    published_at: datetime
    expires_at: datetime | None = None
    file_size_bytes: int = 0
    file_hash: str = ""
    download_count: int = 0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "access_level": self.access_level.value,
            "version": self.version,
            "published_at": self.published_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "file_size_bytes": self.file_size_bytes,
            "download_count": self.download_count,
            "tags": self.tags,
        }


@dataclass
class DocumentAccess:
    """Record of document access."""

    access_id: str
    document_id: str
    customer_email: str
    customer_org: str
    accessed_at: datetime
    ip_address: str
    nda_signed: bool = False
    nda_signed_at: datetime | None = None
    download_token: str = ""
    token_expires_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_id": self.access_id,
            "document_id": self.document_id,
            "customer_org": self.customer_org,
            "accessed_at": self.accessed_at.isoformat(),
            "nda_signed": self.nda_signed,
        }


@dataclass
class QuestionnaireAnswer:
    """Pre-filled answer for a security questionnaire."""

    question_id: str
    question_text: str
    answer_text: str
    evidence_refs: list[str] = field(default_factory=list)
    last_updated: datetime | None = None
    verified: bool = False


@dataclass
class SecurityStatus:
    """Live security status for the trust portal."""

    last_pentest_date: datetime | None
    pentest_findings: str
    bug_bounty_active: bool
    bug_bounty_platform: str | None
    uptime_30d: float
    last_security_incident: datetime | None
    security_incident_details: str | None
    soc2_certified: bool
    soc2_report_date: datetime | None
    iso27001_certified: bool
    hipaa_compliant: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_pentest_date": self.last_pentest_date.isoformat() if self.last_pentest_date else None,
            "pentest_findings": self.pentest_findings,
            "bug_bounty_active": self.bug_bounty_active,
            "bug_bounty_platform": self.bug_bounty_platform,
            "uptime_30d": round(self.uptime_30d, 2),
            "last_security_incident": self.last_security_incident.isoformat() if self.last_security_incident else None,
            "security_incident_details": self.security_incident_details,
            "soc2_certified": self.soc2_certified,
            "soc2_report_date": self.soc2_report_date.isoformat() if self.soc2_report_date else None,
            "iso27001_certified": self.iso27001_certified,
            "hipaa_compliant": self.hipaa_compliant,
        }


@dataclass
class SubProcessor:
    """A sub-processor (third-party vendor)."""

    name: str
    description: str
    data_processed: list[str]
    location: str
    soc2_certified: bool
    gdpr_compliant: bool
    data_residency: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "data_processed": self.data_processed,
            "location": self.location,
            "soc2_certified": self.soc2_certified,
            "gdpr_compliant": self.gdpr_compliant,
            "data_residency": self.data_residency,
        }


class TrustPortal:
    """Customer-facing trust and compliance portal."""

    def __init__(self, portal_url: str = "https://trust.devopscopilot.com") -> None:
        self.portal_url = portal_url
        self._documents: dict[str, TrustDocument] = {}
        self._access_records: list[DocumentAccess] = []
        self._questionnaire_answers: dict[str, dict[str, QuestionnaireAnswer]] = {}
        self._sub_processors: list[SubProcessor] = []
        self._access_counter = 0
        self._setup_default_documents()
        self._setup_default_answers()
        self._setup_sub_processors()

    def _setup_default_documents(self) -> None:
        """Set up default trust documents."""
        now = datetime.now(timezone.utc)

        documents = [
            TrustDocument(
                document_id="soc2_2026",
                title="SOC2 Type II Report (2026)",
                description="Annual SOC2 Type II audit report covering security, availability, and confidentiality",
                category=DocumentCategory.AUDIT_REPORT,
                access_level=AccessLevel.NDA_REQUIRED,
                file_path="/docs/soc2_type2_2026.pdf",
                version="2026.1",
                published_at=now - timedelta(days=30),
                expires_at=now + timedelta(days=335),
                file_size_bytes=2_500_000,
                tags=["soc2", "audit", "compliance"],
            ),
            TrustDocument(
                document_id="pentest_2026q2",
                title="Penetration Test Summary (Q2 2026)",
                description="Summary of quarterly penetration test findings and remediation status",
                category=DocumentCategory.PENETRATION_TEST,
                access_level=AccessLevel.NDA_REQUIRED,
                file_path="/docs/pentest_q2_2026.pdf",
                version="2026.Q2",
                published_at=now - timedelta(days=45),
                file_size_bytes=500_000,
                tags=["pentest", "security"],
            ),
            TrustDocument(
                document_id="security_whitepaper",
                title="Security Whitepaper",
                description="Comprehensive overview of our security architecture and practices",
                category=DocumentCategory.SECURITY_WHITEPAPER,
                access_level=AccessLevel.PUBLIC,
                file_path="/docs/security_whitepaper.pdf",
                version="3.0",
                published_at=now - timedelta(days=60),
                file_size_bytes=1_200_000,
                tags=["security", "architecture"],
            ),
            TrustDocument(
                document_id="dpa",
                title="Data Processing Agreement (DPA)",
                description="Standard data processing agreement for GDPR compliance",
                category=DocumentCategory.AGREEMENT,
                access_level=AccessLevel.PUBLIC,
                file_path="/docs/dpa.pdf",
                version="2.1",
                published_at=now - timedelta(days=90),
                file_size_bytes=300_000,
                tags=["gdpr", "dpa", "legal"],
            ),
            TrustDocument(
                document_id="subprocessors",
                title="Sub-processor List",
                description="Current list of third-party sub-processors",
                category=DocumentCategory.POLICY,
                access_level=AccessLevel.PUBLIC,
                file_path="/docs/subprocessors.pdf",
                version="2026.07",
                published_at=now - timedelta(days=7),
                file_size_bytes=100_000,
                tags=["gdpr", "subprocessors", "vendors"],
            ),
            TrustDocument(
                document_id="bcp",
                title="Business Continuity Plan",
                description="Business continuity and disaster recovery plan overview",
                category=DocumentCategory.POLICY,
                access_level=AccessLevel.NDA_REQUIRED,
                file_path="/docs/bcp.pdf",
                version="4.0",
                published_at=now - timedelta(days=120),
                file_size_bytes=800_000,
                tags=["bcp", "disaster-recovery"],
            ),
            TrustDocument(
                document_id="irp",
                title="Incident Response Plan",
                description="Security incident response procedures",
                category=DocumentCategory.POLICY,
                access_level=AccessLevel.NDA_REQUIRED,
                file_path="/docs/irp.pdf",
                version="3.2",
                published_at=now - timedelta(days=90),
                file_size_bytes=600_000,
                tags=["incident-response", "security"],
            ),
        ]

        for doc in documents:
            self._documents[doc.document_id] = doc

    def _setup_default_answers(self) -> None:
        """Set up pre-filled questionnaire answers."""
        now = datetime.now(timezone.utc)

        sig_answers = {
            "A.1.1": QuestionnaireAnswer(
                question_id="A.1.1",
                question_text="Does the organization have a documented information security policy?",
                answer_text="Yes. Our Information Security Policy is reviewed annually and approved by executive leadership. It covers access control, data classification, incident response, and acceptable use.",
                evidence_refs=["security_whitepaper", "irp"],
                last_updated=now,
                verified=True,
            ),
            "A.2.1": QuestionnaireAnswer(
                question_id="A.2.1",
                question_text="Does the organization perform background checks on employees?",
                answer_text="Yes. All employees undergo background verification including criminal history, employment verification, and education verification prior to their start date.",
                evidence_refs=["hr_policy"],
                last_updated=now,
                verified=True,
            ),
            "B.1.1": QuestionnaireAnswer(
                question_id="B.1.1",
                question_text="Does the organization encrypt data at rest?",
                answer_text="Yes. All data at rest is encrypted using AES-256-GCM. Encryption keys are managed through AWS KMS with automatic rotation every 90 days.",
                evidence_refs=["soc2_2026", "security_whitepaper"],
                last_updated=now,
                verified=True,
            ),
            "B.1.2": QuestionnaireAnswer(
                question_id="B.1.2",
                question_text="Does the organization encrypt data in transit?",
                answer_text="Yes. All data in transit is encrypted using TLS 1.2 or higher. We enforce HSTS and use modern cipher suites. Internal service-to-service communication uses mTLS.",
                evidence_refs=["security_whitepaper"],
                last_updated=now,
                verified=True,
            ),
            "C.1.1": QuestionnaireAnswer(
                question_id="C.1.1",
                question_text="Does the organization perform vulnerability assessments?",
                answer_text="Yes. We perform weekly automated vulnerability scans and quarterly penetration tests by third-party security firms. Critical vulnerabilities are remediated within 24 hours.",
                evidence_refs=["pentest_2026q2"],
                last_updated=now,
                verified=True,
            ),
            "D.1.1": QuestionnaireAnswer(
                question_id="D.1.1",
                question_text="Does the organization have a SOC 2 Type II report?",
                answer_text="Yes. We maintain SOC 2 Type II certification covering Security, Availability, and Confidentiality. Our most recent report covers the period from January 2026 to June 2026.",
                evidence_refs=["soc2_2026"],
                last_updated=now,
                verified=True,
            ),
        }

        caiq_answers = {
            "AAC-01": QuestionnaireAnswer(
                question_id="AAC-01",
                question_text="Are audit logging and monitoring processes defined?",
                answer_text="Yes. We maintain comprehensive audit logs for all security-relevant events including authentication, authorization, data access, and administrative actions. Logs are retained for 365 days.",
                evidence_refs=["soc2_2026"],
                last_updated=now,
                verified=True,
            ),
            "AIS-01": QuestionnaireAnswer(
                question_id="AIS-01",
                question_text="Are application security policies established?",
                answer_text="Yes. We follow secure development practices including code review, SAST/DAST scanning, and dependency vulnerability scanning as part of our CI/CD pipeline.",
                evidence_refs=["security_whitepaper"],
                last_updated=now,
                verified=True,
            ),
        }

        self._questionnaire_answers["sig_lite"] = sig_answers
        self._questionnaire_answers["caiq"] = caiq_answers

    def _setup_sub_processors(self) -> None:
        """Set up sub-processor list."""
        self._sub_processors = [
            SubProcessor(
                name="Amazon Web Services (AWS)",
                description="Cloud infrastructure hosting",
                data_processed=["all customer data", "logs", "backups"],
                location="United States",
                soc2_certified=True,
                gdpr_compliant=True,
                data_residency="us-east-1, eu-west-1",
            ),
            SubProcessor(
                name="Anthropic",
                description="AI/LLM processing",
                data_processed=["investigation queries", "log excerpts"],
                location="United States",
                soc2_certified=True,
                gdpr_compliant=True,
                data_residency="US",
            ),
            SubProcessor(
                name="Datadog",
                description="Monitoring and observability",
                data_processed=["application metrics", "logs"],
                location="United States",
                soc2_certified=True,
                gdpr_compliant=True,
                data_residency="US, EU",
            ),
            SubProcessor(
                name="Stripe",
                description="Payment processing",
                data_processed=["billing information"],
                location="United States",
                soc2_certified=True,
                gdpr_compliant=True,
                data_residency="US",
            ),
        ]

    def list_documents(
        self,
        access_level: AccessLevel | None = None,
        category: DocumentCategory | None = None,
    ) -> list[TrustDocument]:
        """List available documents."""
        docs = list(self._documents.values())

        if access_level:
            docs = [d for d in docs if d.access_level == access_level]

        if category:
            docs = [d for d in docs if d.category == category]

        return sorted(docs, key=lambda d: d.published_at, reverse=True)

    def get_document(self, document_id: str) -> TrustDocument | None:
        """Get a document by ID."""
        return self._documents.get(document_id)

    def request_access(
        self,
        document_id: str,
        customer_email: str,
        customer_org: str,
        ip_address: str,
        nda_accepted: bool = False,
    ) -> DocumentAccess | None:
        """Request access to a document."""
        doc = self._documents.get(document_id)
        if not doc:
            return None

        if doc.access_level == AccessLevel.NDA_REQUIRED and not nda_accepted:
            log.info(
                "document access denied - NDA required",
                extra={"document_id": document_id, "customer_email": customer_email},
            )
            return None

        self._access_counter += 1
        now = datetime.now(timezone.utc)

        access = DocumentAccess(
            access_id=f"access_{self._access_counter}",
            document_id=document_id,
            customer_email=customer_email,
            customer_org=customer_org,
            accessed_at=now,
            ip_address=ip_address,
            nda_signed=nda_accepted,
            nda_signed_at=now if nda_accepted else None,
            download_token=secrets.token_urlsafe(32),
            token_expires_at=now + timedelta(hours=24),
        )

        self._access_records.append(access)
        doc.download_count += 1

        log.info(
            "document access granted",
            extra={
                "document_id": document_id,
                "customer_org": customer_org,
                "nda_signed": nda_accepted,
            },
        )

        return access

    def validate_download_token(self, token: str) -> DocumentAccess | None:
        """Validate a download token."""
        now = datetime.now(timezone.utc)
        for access in self._access_records:
            if access.download_token == token:
                if access.token_expires_at and access.token_expires_at > now:
                    return access
        return None

    def get_questionnaire(
        self,
        questionnaire_type: QuestionnaireType,
    ) -> dict[str, QuestionnaireAnswer]:
        """Get pre-filled questionnaire answers."""
        type_map = {
            QuestionnaireType.SIG_LITE: "sig_lite",
            QuestionnaireType.SIG_CORE: "sig_lite",
            QuestionnaireType.CAIQ: "caiq",
        }
        key = type_map.get(questionnaire_type, "sig_lite")
        return self._questionnaire_answers.get(key, {})

    def export_questionnaire(
        self,
        questionnaire_type: QuestionnaireType,
        format: str = "json",
    ) -> dict[str, Any]:
        """Export a questionnaire with answers."""
        answers = self.get_questionnaire(questionnaire_type)

        return {
            "questionnaire_type": questionnaire_type.value,
            "export_date": datetime.now(timezone.utc).isoformat(),
            "total_questions": len(answers),
            "answered_questions": len([a for a in answers.values() if a.answer_text]),
            "answers": {
                qid: {
                    "question": a.question_text,
                    "answer": a.answer_text,
                    "evidence": a.evidence_refs,
                    "verified": a.verified,
                }
                for qid, a in answers.items()
            },
        }

    def get_security_status(self) -> SecurityStatus:
        """Get current security status."""
        now = datetime.now(timezone.utc)

        return SecurityStatus(
            last_pentest_date=now - timedelta(days=45),
            pentest_findings="No critical findings",
            bug_bounty_active=True,
            bug_bounty_platform="HackerOne",
            uptime_30d=99.97,
            last_security_incident=None,
            security_incident_details=None,
            soc2_certified=True,
            soc2_report_date=now - timedelta(days=30),
            iso27001_certified=True,
            hipaa_compliant=True,
        )

    def get_sub_processors(self) -> list[SubProcessor]:
        """Get list of sub-processors."""
        return self._sub_processors

    def get_portal_data(self) -> dict[str, Any]:
        """Get complete trust portal data."""
        return {
            "portal_url": self.portal_url,
            "documents": [d.to_dict() for d in self.list_documents()],
            "security_status": self.get_security_status().to_dict(),
            "sub_processors": [sp.to_dict() for sp in self._sub_processors],
            "questionnaire_types": [qt.value for qt in QuestionnaireType],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def get_access_log(
        self,
        document_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get document access log."""
        records = self._access_records

        if document_id:
            records = [r for r in records if r.document_id == document_id]

        records = sorted(records, key=lambda r: r.accessed_at, reverse=True)[:limit]

        return [r.to_dict() for r in records]


_portal: TrustPortal | None = None


def get_trust_portal() -> TrustPortal:
    """Get the default trust portal."""
    global _portal
    if _portal is None:
        _portal = TrustPortal()
    return _portal

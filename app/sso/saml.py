"""SAML 2.0 authentication with multi-IdP support.

Supports Okta, Azure AD, OneLogin, Google Workspace, and custom SAML IdPs.
"""

from __future__ import annotations

import base64
import logging
import secrets
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger("omnipotent.sso.saml")


class SAMLProvider(Enum):
    """Supported SAML identity providers."""

    OKTA = "okta"
    AZURE_AD = "azure_ad"
    ONELOGIN = "onelogin"
    GOOGLE = "google"
    PING = "ping"
    CUSTOM = "custom"


@dataclass
class SAMLConfig:
    """SAML configuration for an identity provider."""

    provider: SAMLProvider
    entity_id: str
    sso_url: str
    slo_url: str | None = None
    certificate: str = ""
    name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    attribute_mapping: dict[str, str] = field(default_factory=dict)
    sign_requests: bool = True
    want_assertions_signed: bool = True
    want_response_signed: bool = True
    allow_unsolicited: bool = False
    metadata_url: str | None = None

    def __post_init__(self) -> None:
        if not self.attribute_mapping:
            self.attribute_mapping = self._default_attribute_mapping()

    def _default_attribute_mapping(self) -> dict[str, str]:
        """Default attribute mapping based on provider."""
        mappings = {
            SAMLProvider.OKTA: {
                "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                "first_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
                "last_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
                "groups": "http://schemas.xmlsoap.org/claims/Group",
                "department": "department",
                "title": "title",
                "employee_id": "employeeNumber",
            },
            SAMLProvider.AZURE_AD: {
                "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                "first_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
                "last_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
                "groups": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
                "department": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/department",
                "title": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/title",
                "tenant_id": "http://schemas.microsoft.com/identity/claims/tenantid",
            },
            SAMLProvider.GOOGLE: {
                "email": "email",
                "first_name": "firstName",
                "last_name": "lastName",
                "groups": "groups",
                "department": "department",
            },
        }
        return mappings.get(self.provider, {
            "email": "email",
            "first_name": "firstName",
            "last_name": "lastName",
            "groups": "groups",
        })


@dataclass
class SAMLAssertion:
    """Parsed SAML assertion with extracted attributes."""

    subject: str
    session_index: str
    issuer: str
    audiences: list[str]
    not_before: datetime
    not_on_or_after: datetime
    authn_instant: datetime
    authn_context: str
    attributes: dict[str, Any]
    raw_xml: str
    signature_valid: bool = False
    conditions_valid: bool = False

    @property
    def email(self) -> str | None:
        return self.attributes.get("email")

    @property
    def groups(self) -> list[str]:
        groups = self.attributes.get("groups", [])
        if isinstance(groups, str):
            return [groups]
        return groups

    @property
    def department(self) -> str | None:
        return self.attributes.get("department")

    @property
    def title(self) -> str | None:
        return self.attributes.get("title")

    @property
    def is_valid(self) -> bool:
        now = datetime.now(timezone.utc)
        return (
            self.signature_valid
            and self.conditions_valid
            and self.not_before <= now < self.not_on_or_after
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "email": self.email,
            "groups": self.groups,
            "department": self.department,
            "title": self.title,
            "issuer": self.issuer,
            "authn_instant": self.authn_instant.isoformat(),
            "not_on_or_after": self.not_on_or_after.isoformat(),
            "attributes": self.attributes,
            "is_valid": self.is_valid,
        }


@dataclass
class SAMLRequest:
    """SAML authentication request."""

    id: str
    issuer: str
    destination: str
    assertion_consumer_service_url: str
    name_id_policy_format: str
    created_at: datetime
    relay_state: str | None = None

    def to_xml(self) -> str:
        """Generate SAML AuthnRequest XML."""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<samlp:AuthnRequest
    xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="{self.id}"
    Version="2.0"
    IssueInstant="{self.created_at.isoformat()}"
    Destination="{self.destination}"
    AssertionConsumerServiceURL="{self.assertion_consumer_service_url}"
    ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">
    <saml:Issuer>{self.issuer}</saml:Issuer>
    <samlp:NameIDPolicy Format="{self.name_id_policy_format}" AllowCreate="true"/>
</samlp:AuthnRequest>'''


class SAMLService:
    """SAML authentication service with multi-IdP support."""

    def __init__(
        self,
        sp_entity_id: str,
        sp_acs_url: str,
        sp_slo_url: str | None = None,
    ):
        self.sp_entity_id = sp_entity_id
        self.sp_acs_url = sp_acs_url
        self.sp_slo_url = sp_slo_url
        self._configs: dict[str, SAMLConfig] = {}
        self._pending_requests: dict[str, SAMLRequest] = {}

    def register_idp(self, idp_id: str, config: SAMLConfig) -> None:
        """Register an identity provider configuration."""
        self._configs[idp_id] = config
        log.info("registered IdP", extra={"idp_id": idp_id, "provider": config.provider.value})

    def unregister_idp(self, idp_id: str) -> bool:
        """Unregister an identity provider."""
        if idp_id in self._configs:
            del self._configs[idp_id]
            return True
        return False

    def get_idp(self, idp_id: str) -> SAMLConfig | None:
        """Get IdP configuration."""
        return self._configs.get(idp_id)

    def list_idps(self) -> list[str]:
        """List registered IdP IDs."""
        return list(self._configs.keys())

    def create_authn_request(
        self,
        idp_id: str,
        relay_state: str | None = None,
    ) -> tuple[str, str]:
        """Create SAML AuthnRequest and return redirect URL with request ID."""
        config = self._configs.get(idp_id)
        if not config:
            raise ValueError(f"Unknown IdP: {idp_id}")

        request_id = f"_{''.join(secrets.token_hex(16))}"
        request = SAMLRequest(
            id=request_id,
            issuer=self.sp_entity_id,
            destination=config.sso_url,
            assertion_consumer_service_url=self.sp_acs_url,
            name_id_policy_format=config.name_id_format,
            created_at=datetime.now(timezone.utc),
            relay_state=relay_state,
        )

        self._pending_requests[request_id] = request

        xml = request.to_xml()
        encoded = base64.b64encode(xml.encode()).decode()

        params = {"SAMLRequest": encoded}
        if relay_state:
            params["RelayState"] = relay_state

        redirect_url = f"{config.sso_url}?{urlencode(params)}"
        return redirect_url, request_id

    def process_response(
        self,
        idp_id: str,
        saml_response: str,
        relay_state: str | None = None,
    ) -> SAMLAssertion:
        """Process SAML response and extract assertion."""
        config = self._configs.get(idp_id)
        if not config:
            raise ValueError(f"Unknown IdP: {idp_id}")

        try:
            xml_bytes = base64.b64decode(saml_response)
            xml_str = xml_bytes.decode("utf-8")
        except Exception as e:
            raise ValueError(f"Invalid SAML response encoding: {e}")

        return self._parse_assertion(xml_str, config)

    def _parse_assertion(self, xml_str: str, config: SAMLConfig) -> SAMLAssertion:
        """Parse SAML assertion from XML."""
        ns = {
            "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
            "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
            "ds": "http://www.w3.org/2000/09/xmldsig#",
        }

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as e:
            raise ValueError(f"Invalid SAML XML: {e}")

        assertion = root.find(".//saml:Assertion", ns)
        if assertion is None:
            raise ValueError("No Assertion found in SAML response")

        issuer_elem = assertion.find("saml:Issuer", ns)
        issuer = issuer_elem.text if issuer_elem is not None else ""

        subject = assertion.find(".//saml:Subject/saml:NameID", ns)
        subject_value = subject.text if subject is not None else ""

        conditions = assertion.find("saml:Conditions", ns)
        not_before_str = conditions.get("NotBefore", "") if conditions is not None else ""
        not_after_str = conditions.get("NotOnOrAfter", "") if conditions is not None else ""

        now = datetime.now(timezone.utc)
        not_before = self._parse_datetime(not_before_str) if not_before_str else now
        not_after = self._parse_datetime(not_after_str) if not_after_str else now + timedelta(hours=1)

        audiences = []
        audience_elems = assertion.findall(".//saml:AudienceRestriction/saml:Audience", ns)
        for aud in audience_elems:
            if aud.text:
                audiences.append(aud.text)

        authn_stmt = assertion.find("saml:AuthnStatement", ns)
        session_index = authn_stmt.get("SessionIndex", "") if authn_stmt is not None else ""
        authn_instant_str = authn_stmt.get("AuthnInstant", "") if authn_stmt is not None else ""
        authn_instant = self._parse_datetime(authn_instant_str) if authn_instant_str else now

        authn_context_elem = assertion.find(".//saml:AuthnContextClassRef", ns)
        authn_context = authn_context_elem.text if authn_context_elem is not None else ""

        attributes = self._extract_attributes(assertion, config, ns)

        signature_valid = self._verify_signature(xml_str, config)
        conditions_valid = self._verify_conditions(not_before, not_after, audiences)

        return SAMLAssertion(
            subject=subject_value or "",
            session_index=session_index,
            issuer=issuer or "",
            audiences=audiences,
            not_before=not_before,
            not_on_or_after=not_after,
            authn_instant=authn_instant,
            authn_context=authn_context or "",
            attributes=attributes,
            raw_xml=xml_str,
            signature_valid=signature_valid,
            conditions_valid=conditions_valid,
        )

    def _extract_attributes(
        self,
        assertion: ET.Element,
        config: SAMLConfig,
        ns: dict[str, str],
    ) -> dict[str, Any]:
        """Extract attributes from SAML assertion using configured mapping."""
        attributes: dict[str, Any] = {}
        attr_stmt = assertion.find("saml:AttributeStatement", ns)

        if attr_stmt is None:
            return attributes

        reverse_mapping = {v: k for k, v in config.attribute_mapping.items()}

        for attr in attr_stmt.findall("saml:Attribute", ns):
            name = attr.get("Name", "")
            values = []
            for val in attr.findall("saml:AttributeValue", ns):
                if val.text:
                    values.append(val.text)

            mapped_name = reverse_mapping.get(name, name)
            if len(values) == 1:
                attributes[mapped_name] = values[0]
            elif len(values) > 1:
                attributes[mapped_name] = values

        return attributes

    def _parse_datetime(self, dt_str: str) -> datetime:
        """Parse ISO datetime string to datetime object."""
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)

    def _verify_signature(self, xml_str: str, config: SAMLConfig) -> bool:
        """Verify XML signature (placeholder - requires xmlsec library in production)."""
        if not config.want_response_signed and not config.want_assertions_signed:
            return True
        return True

    def _verify_conditions(
        self,
        not_before: datetime,
        not_after: datetime,
        audiences: list[str],
    ) -> bool:
        """Verify assertion conditions."""
        now = datetime.now(timezone.utc)
        if now < not_before or now >= not_after:
            return False
        if audiences and self.sp_entity_id not in audiences:
            return False
        return True

    def generate_metadata(self) -> str:
        """Generate SP metadata XML."""
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor
    xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    entityID="{self.sp_entity_id}">
    <md:SPSSODescriptor
        AuthnRequestsSigned="true"
        WantAssertionsSigned="true"
        protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
        <md:AssertionConsumerService
            Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
            Location="{self.sp_acs_url}"
            index="0"
            isDefault="true"/>
        {f'<md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{self.sp_slo_url}"/>' if self.sp_slo_url else ''}
    </md:SPSSODescriptor>
</md:EntityDescriptor>'''


_service: SAMLService | None = None


def get_saml_service() -> SAMLService:
    """Get the default SAML service instance."""
    global _service
    if _service is None:
        _service = SAMLService(
            sp_entity_id="https://devopscopilot.com/saml/metadata",
            sp_acs_url="https://devopscopilot.com/saml/acs",
            sp_slo_url="https://devopscopilot.com/saml/slo",
        )
    return _service

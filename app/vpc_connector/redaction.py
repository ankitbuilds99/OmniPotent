"""Redaction Engine - Scrubs sensitive data before transmission.

Redacts:
- PII: emails, SSNs, phone numbers, credit cards, names
- Secrets: API keys, tokens, passwords, private keys, JWTs
- Infrastructure: internal IPs, hostnames, connection strings
- Custom patterns: organization-specific patterns
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("omnipotent.vpc_connector.redaction")


@dataclass
class RedactionRule:
    """A rule for redacting sensitive data."""
    name: str
    pattern: str
    replacement: str = "[REDACTED]"
    enabled: bool = True
    flags: int = re.IGNORECASE | re.DOTALL


class RedactionEngine:
    """Redacts sensitive data before it leaves the VPC.

    This is the core security component that ensures no sensitive
    data is transmitted to the cloud control plane.
    """

    # Built-in patterns for common sensitive data
    DEFAULT_RULES = [
        # PII
        RedactionRule(
            "email",
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            "[EMAIL]"
        ),
        RedactionRule(
            "ssn",
            r'\b\d{3}-\d{2}-\d{4}\b',
            "[SSN]"
        ),
        RedactionRule(
            "phone_us",
            r'\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
            "[PHONE]"
        ),
        RedactionRule(
            "credit_card",
            r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
            "[CARD]"
        ),
        RedactionRule(
            "credit_card_amex",
            r'\b3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}\b',
            "[CARD]"
        ),

        # Secrets - API Keys
        RedactionRule(
            "api_key_generic",
            r'(?:api[_-]?key|apikey|api_secret)["\'\s:=]+["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            "[API_KEY]"
        ),
        RedactionRule(
            "bearer_token",
            r'\bBearer\s+[a-zA-Z0-9_\-\.]+',
            "[BEARER_TOKEN]"
        ),
        RedactionRule(
            "basic_auth",
            r'\bBasic\s+[a-zA-Z0-9+/=]+',
            "[BASIC_AUTH]"
        ),

        # Secrets - Cloud Provider Keys
        RedactionRule(
            "aws_access_key",
            r'\b(AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b',
            "[AWS_KEY]"
        ),
        RedactionRule(
            "aws_secret_key",
            r'(?:aws_secret|secret_key|secret_access_key)["\'\s:=]+["\']?([a-zA-Z0-9/+=]{40})["\']?',
            "[AWS_SECRET]"
        ),
        RedactionRule(
            "gcp_service_account",
            r'"type"\s*:\s*"service_account"',
            "[GCP_SERVICE_ACCOUNT]"
        ),
        RedactionRule(
            "azure_connection_string",
            r'DefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[^;]+',
            "[AZURE_CONN_STRING]"
        ),

        # Secrets - Passwords
        RedactionRule(
            "password",
            r'(?:password|passwd|pwd|secret)["\'\s:=]+["\']?([^\s"\']{8,})["\']?',
            "[PASSWORD]"
        ),

        # Secrets - Tokens & Keys
        RedactionRule(
            "private_key",
            r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
            "[PRIVATE_KEY]"
        ),
        RedactionRule(
            "jwt",
            r'\beyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*\b',
            "[JWT]"
        ),
        RedactionRule(
            "github_token",
            r'\b(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}\b',
            "[GITHUB_TOKEN]"
        ),
        RedactionRule(
            "slack_token",
            r'\bxox[baprs]-[0-9a-zA-Z-]+\b',
            "[SLACK_TOKEN]"
        ),
        RedactionRule(
            "stripe_key",
            r'\b(sk|pk)_(test|live)_[a-zA-Z0-9]{24,}\b',
            "[STRIPE_KEY]"
        ),

        # Infrastructure
        RedactionRule(
            "internal_ip_10",
            r'\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b',
            "[INTERNAL_IP]"
        ),
        RedactionRule(
            "internal_ip_172",
            r'\b172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}\b',
            "[INTERNAL_IP]"
        ),
        RedactionRule(
            "internal_ip_192",
            r'\b192\.168\.\d{1,3}\.\d{1,3}\b',
            "[INTERNAL_IP]"
        ),
        RedactionRule(
            "hostname_internal",
            r'\b[a-z][a-z0-9-]*\.(?:internal|local|corp|private|lan)\b',
            "[HOSTNAME]"
        ),

        # Database connection strings
        RedactionRule(
            "postgres_uri",
            r'postgres(?:ql)?://[^\s"\']+',
            "[POSTGRES_URI]"
        ),
        RedactionRule(
            "mysql_uri",
            r'mysql://[^\s"\']+',
            "[MYSQL_URI]"
        ),
        RedactionRule(
            "mongodb_uri",
            r'mongodb(?:\+srv)?://[^\s"\']+',
            "[MONGODB_URI]"
        ),
        RedactionRule(
            "redis_uri",
            r'redis(?:s)?://[^\s"\']+',
            "[REDIS_URI]"
        ),
    ]

    def __init__(
        self,
        custom_rules: list[RedactionRule] | None = None,
        disable_defaults: bool = False,
    ):
        """Initialize the redaction engine.

        Args:
            custom_rules: Additional custom redaction rules
            disable_defaults: If True, only use custom rules
        """
        if disable_defaults:
            self.rules = custom_rules or []
        else:
            self.rules = self.DEFAULT_RULES + (custom_rules or [])

        # Compile patterns for performance
        self._compiled: dict[str, re.Pattern] = {}
        for rule in self.rules:
            if rule.enabled:
                try:
                    self._compiled[rule.name] = re.compile(rule.pattern, rule.flags)
                except re.error as e:
                    log.warning("Invalid regex pattern for rule %s: %s", rule.name, e)

        log.info("RedactionEngine initialized with %d rules", len(self._compiled))

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Redact sensitive data from text.

        Args:
            text: The text to redact

        Returns:
            Tuple of (redacted_text, list of applied rule names)
        """
        if not text:
            return text, []

        applied_rules = []
        result = text

        for rule in self.rules:
            if not rule.enabled:
                continue

            compiled = self._compiled.get(rule.name)
            if compiled and compiled.search(result):
                result = compiled.sub(rule.replacement, result)
                applied_rules.append(rule.name)

        return result, applied_rules

    def redact_dict(
        self,
        data: dict[str, Any],
        path: str = "",
    ) -> tuple[dict[str, Any], list[str]]:
        """Recursively redact a dictionary.

        Args:
            data: Dictionary to redact
            path: Current path (for logging)

        Returns:
            Tuple of (redacted_dict, list of applied rule names)
        """
        all_rules: list[str] = []
        result: dict[str, Any] = {}

        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key

            if isinstance(value, str):
                redacted_str, rules = self.redact(value)
                result[key] = redacted_str
                all_rules.extend(rules)

            elif isinstance(value, dict):
                redacted_dict, rules = self.redact_dict(value, current_path)
                result[key] = redacted_dict
                all_rules.extend(rules)

            elif isinstance(value, list):
                redacted_list: list[Any] = []
                for i, item in enumerate(value):
                    if isinstance(item, str):
                        item_redacted, rules = self.redact(item)
                        redacted_list.append(item_redacted)
                        all_rules.extend(rules)
                    elif isinstance(item, dict):
                        item_dict, rules = self.redact_dict(item, f"{current_path}[{i}]")
                        redacted_list.append(item_dict)
                        all_rules.extend(rules)
                    else:
                        redacted_list.append(item)
                result[key] = redacted_list

            else:
                result[key] = value

        return result, list(set(all_rules))

    def add_rule(self, rule: RedactionRule) -> None:
        """Add a custom redaction rule.

        Args:
            rule: The rule to add
        """
        self.rules.append(rule)
        if rule.enabled:
            try:
                self._compiled[rule.name] = re.compile(rule.pattern, rule.flags)
            except re.error as e:
                log.warning("Invalid regex pattern for rule %s: %s", rule.name, e)

    def disable_rule(self, rule_name: str) -> bool:
        """Disable a redaction rule by name.

        Args:
            rule_name: Name of the rule to disable

        Returns:
            True if rule was found and disabled
        """
        for rule in self.rules:
            if rule.name == rule_name:
                rule.enabled = False
                self._compiled.pop(rule_name, None)
                return True
        return False

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about the redaction engine.

        Returns:
            Dictionary with stats
        """
        return {
            "total_rules": len(self.rules),
            "active_rules": len(self._compiled),
            "rule_names": list(self._compiled.keys()),
        }


def create_redaction_engine(
    org_config: dict[str, Any] | None = None,
) -> RedactionEngine:
    """Factory function to create a redaction engine with org config.

    Args:
        org_config: Organization-specific configuration

    Returns:
        Configured RedactionEngine
    """
    custom_rules = []

    if org_config:
        # Load custom patterns from org config
        for pattern_config in org_config.get("custom_patterns", []):
            custom_rules.append(RedactionRule(
                name=pattern_config["name"],
                pattern=pattern_config["pattern"],
                replacement=pattern_config.get("replacement", "[REDACTED]"),
                enabled=pattern_config.get("enabled", True),
            ))

    return RedactionEngine(custom_rules=custom_rules)

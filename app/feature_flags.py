"""Feature flags system for progressive rollout.

Supports:
- LaunchDarkly integration (production)
- Local JSON file configuration (development)
- In-memory overrides (testing)
- Percentage-based rollouts
- User/org targeting
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("omnipotent.features")


class FlagStatus(Enum):
    """Feature flag status."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    PERCENTAGE = "percentage"
    TARGETED = "targeted"


@dataclass
class FeatureFlag:
    """Feature flag definition."""

    key: str
    name: str
    description: str = ""
    status: FlagStatus = FlagStatus.DISABLED
    percentage: int = 0
    targets: list[str] = field(default_factory=list)
    default_value: Any = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def evaluate(self, context: "EvaluationContext") -> Any:
        """Evaluate the flag for a given context.

        Args:
            context: User/request context.

        Returns:
            Flag value (bool, string, number, or JSON).
        """
        if self.status == FlagStatus.DISABLED:
            return self.default_value

        if self.status == FlagStatus.ENABLED:
            return True

        if self.status == FlagStatus.TARGETED:
            user_id = context.user_id or ""
            org_id = context.org_id or ""
            if user_id in self.targets or org_id in self.targets:
                return True
            return self.default_value

        if self.status == FlagStatus.PERCENTAGE:
            bucket = self._get_bucket(context)
            return bucket < self.percentage

        return self.default_value

    def _get_bucket(self, context: "EvaluationContext") -> int:
        """Get consistent bucket (0-99) for percentage rollout."""
        key = f"{self.key}:{context.user_id or context.org_id or 'anonymous'}"
        hash_value = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
        return int(hash_value[:8], 16) % 100


@dataclass
class EvaluationContext:
    """Context for flag evaluation."""

    user_id: str | None = None
    org_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: Any) -> "EvaluationContext":
        """Create context from FastAPI request."""
        from app import tenant_context

        cfg = tenant_context.get_tenant()
        actor = tenant_context.get_actor()

        return cls(
            user_id=actor,
            org_id=cfg.org_id if cfg else None,
            attributes={
                "plan": cfg.plan if cfg else "free",
                "role": cfg.role if cfg else "viewer",
            },
        )


class FeatureFlagProvider:
    """Abstract feature flag provider."""

    def get_flag(self, key: str) -> FeatureFlag | None:
        """Get a flag by key."""
        raise NotImplementedError

    def evaluate(self, key: str, context: EvaluationContext, default: Any = False) -> Any:
        """Evaluate a flag."""
        flag = self.get_flag(key)
        if flag is None:
            return default
        return flag.evaluate(context)

    def all_flags(self) -> dict[str, FeatureFlag]:
        """Get all flags."""
        raise NotImplementedError


class InMemoryProvider(FeatureFlagProvider):
    """In-memory feature flag provider for testing."""

    def __init__(self) -> None:
        self._flags: dict[str, FeatureFlag] = {}

    def get_flag(self, key: str) -> FeatureFlag | None:
        return self._flags.get(key)

    def all_flags(self) -> dict[str, FeatureFlag]:
        return self._flags.copy()

    def set_flag(self, flag: FeatureFlag) -> None:
        """Set a flag."""
        self._flags[flag.key] = flag

    def enable(self, key: str) -> None:
        """Enable a flag."""
        if key in self._flags:
            self._flags[key].status = FlagStatus.ENABLED

    def disable(self, key: str) -> None:
        """Disable a flag."""
        if key in self._flags:
            self._flags[key].status = FlagStatus.DISABLED

    def clear(self) -> None:
        """Clear all flags."""
        self._flags.clear()


class FileProvider(FeatureFlagProvider):
    """File-based feature flag provider."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._flags: dict[str, FeatureFlag] = {}
        self._load()

    def _load(self) -> None:
        """Load flags from file."""
        if not self._path.exists():
            log.warning("Feature flags file not found: %s", self._path)
            return

        try:
            with open(self._path) as f:
                data = json.load(f)

            for key, config in data.get("flags", {}).items():
                self._flags[key] = FeatureFlag(
                    key=key,
                    name=config.get("name", key),
                    description=config.get("description", ""),
                    status=FlagStatus(config.get("status", "disabled")),
                    percentage=config.get("percentage", 0),
                    targets=config.get("targets", []),
                    default_value=config.get("default", False),
                    metadata=config.get("metadata", {}),
                )

            log.info("Loaded %d feature flags from %s", len(self._flags), self._path)
        except Exception as e:
            log.error("Failed to load feature flags: %s", e)

    def get_flag(self, key: str) -> FeatureFlag | None:
        return self._flags.get(key)

    def all_flags(self) -> dict[str, FeatureFlag]:
        return self._flags.copy()

    def reload(self) -> None:
        """Reload flags from file."""
        self._flags.clear()
        self._load()


class LaunchDarklyProvider(FeatureFlagProvider):
    """LaunchDarkly feature flag provider."""

    def __init__(self, sdk_key: str) -> None:
        self._sdk_key = sdk_key
        self._client: Any = None
        self._init()

    def _init(self) -> None:
        """Initialize LaunchDarkly client."""
        try:
            import ldclient
            from ldclient.config import Config

            ldclient.set_config(Config(self._sdk_key))
            self._client = ldclient.get()

            if self._client.is_initialized():
                log.info("LaunchDarkly client initialized")
            else:
                log.error("LaunchDarkly client failed to initialize")
        except ImportError:
            log.warning("LaunchDarkly SDK not installed, using stub")
            self._client = None
        except Exception as e:
            log.error("LaunchDarkly initialization failed: %s", e)
            self._client = None

    def get_flag(self, key: str) -> FeatureFlag | None:
        return FeatureFlag(key=key, name=key)

    def all_flags(self) -> dict[str, FeatureFlag]:
        return {}

    def evaluate(self, key: str, context: EvaluationContext, default: Any = False) -> Any:
        """Evaluate flag via LaunchDarkly."""
        if self._client is None:
            return default

        try:
            from ldclient import Context

            ld_context = Context.builder(context.user_id or "anonymous")
            if context.org_id:
                ld_context.set("org_id", context.org_id)
            for k, v in context.attributes.items():
                ld_context.set(k, v)

            return self._client.variation(key, ld_context.build(), default)
        except Exception as e:
            log.error("LaunchDarkly evaluation failed: %s", e)
            return default


_provider: FeatureFlagProvider | None = None


DEFAULT_FLAGS: list[FeatureFlag] = [
    FeatureFlag(
        key="multi_agent_mode",
        name="Multi-Agent Mode",
        description="Enable multi-agent collaboration for investigations",
        status=FlagStatus.DISABLED,
    ),
    FeatureFlag(
        key="vector_search",
        name="Vector Search",
        description="Enable vector similarity search for incidents",
        status=FlagStatus.DISABLED,
    ),
    FeatureFlag(
        key="auto_remediation",
        name="Auto Remediation",
        description="Enable autonomous remediation actions",
        status=FlagStatus.DISABLED,
    ),
    FeatureFlag(
        key="streaming_v2",
        name="Streaming V2",
        description="Use new streaming protocol",
        status=FlagStatus.PERCENTAGE,
        percentage=10,
    ),
    FeatureFlag(
        key="new_dashboard",
        name="New Dashboard",
        description="Enable new React dashboard UI",
        status=FlagStatus.TARGETED,
        targets=["beta-testers"],
    ),
    FeatureFlag(
        key="cost_alerts",
        name="Cost Alerts",
        description="Enable cost anomaly alerting",
        status=FlagStatus.ENABLED,
    ),
    FeatureFlag(
        key="runbook_automation",
        name="Runbook Automation",
        description="Enable automated runbook execution",
        status=FlagStatus.DISABLED,
    ),
    FeatureFlag(
        key="api_v2",
        name="API V2",
        description="Enable API v2 endpoints",
        status=FlagStatus.DISABLED,
    ),
]


def get_provider() -> FeatureFlagProvider:
    """Get the configured feature flag provider."""
    global _provider

    if _provider is not None:
        return _provider

    ld_key = os.environ.get("LAUNCHDARKLY_SDK_KEY", "")
    flags_file = os.environ.get("COPILOT_FLAGS_FILE", "")

    if ld_key:
        log.info("Using LaunchDarkly provider")
        _provider = LaunchDarklyProvider(ld_key)
    elif flags_file:
        log.info("Using file-based flags: %s", flags_file)
        _provider = FileProvider(flags_file)
    else:
        log.info("Using in-memory flags with defaults")
        _provider = InMemoryProvider()
        for flag in DEFAULT_FLAGS:
            _provider.set_flag(flag)

    return _provider


def is_enabled(
    key: str,
    context: EvaluationContext | None = None,
    default: bool = False,
) -> bool:
    """Check if a feature flag is enabled.

    Args:
        key: Flag key.
        context: Evaluation context (user/org).
        default: Default value if flag not found.

    Returns:
        True if enabled, False otherwise.
    """
    provider = get_provider()
    ctx = context or EvaluationContext()
    return bool(provider.evaluate(key, ctx, default))


def get_value(
    key: str,
    context: EvaluationContext | None = None,
    default: Any = None,
) -> Any:
    """Get a feature flag value.

    Args:
        key: Flag key.
        context: Evaluation context.
        default: Default value.

    Returns:
        Flag value.
    """
    provider = get_provider()
    ctx = context or EvaluationContext()
    return provider.evaluate(key, ctx, default)


def feature_flag(
    key: str,
    default: Any = False,
) -> Callable:
    """Decorator for feature-flagged functions.

    Usage:
        @feature_flag("new_algorithm")
        def process_data():
            # Only runs if flag is enabled
            ...

    Args:
        key: Flag key.
        default: Default behavior if flag disabled.

    Returns:
        Decorator function.
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if is_enabled(key):
                return func(*args, **kwargs)
            return default

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


def reset_provider() -> None:
    """Reset the provider (for testing)."""
    global _provider
    _provider = None

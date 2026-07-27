"""Tests for feature flags system."""

from __future__ import annotations

import json
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from app.feature_flags import (
    FeatureFlag,
    FlagStatus,
    EvaluationContext,
    InMemoryProvider,
    FileProvider,
    is_enabled,
    get_value,
    feature_flag,
    reset_provider,
)


class TestFeatureFlag:
    """Tests for FeatureFlag class."""

    def test_evaluate_disabled(self):
        """Disabled flag should return default value."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.DISABLED,
            default_value=False,
        )
        context = EvaluationContext()

        assert flag.evaluate(context) is False

    def test_evaluate_enabled(self):
        """Enabled flag should return True."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.ENABLED,
        )
        context = EvaluationContext()

        assert flag.evaluate(context) is True

    def test_evaluate_targeted_user_match(self):
        """Targeted flag should return True for matching user."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.TARGETED,
            targets=["user123", "org456"],
        )
        context = EvaluationContext(user_id="user123")

        assert flag.evaluate(context) is True

    def test_evaluate_targeted_org_match(self):
        """Targeted flag should return True for matching org."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.TARGETED,
            targets=["user123", "org456"],
        )
        context = EvaluationContext(org_id="org456")

        assert flag.evaluate(context) is True

    def test_evaluate_targeted_no_match(self):
        """Targeted flag should return default for non-matching."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.TARGETED,
            targets=["user123"],
            default_value=False,
        )
        context = EvaluationContext(user_id="other_user")

        assert flag.evaluate(context) is False

    def test_evaluate_percentage_consistent(self):
        """Percentage flag should be consistent for same user."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.PERCENTAGE,
            percentage=50,
        )
        context = EvaluationContext(user_id="user123")

        results = [flag.evaluate(context) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_evaluate_percentage_different_users(self):
        """Percentage flag should vary across users."""
        flag = FeatureFlag(
            key="test",
            name="Test",
            status=FlagStatus.PERCENTAGE,
            percentage=50,
        )

        results = []
        for i in range(100):
            context = EvaluationContext(user_id=f"user{i}")
            results.append(flag.evaluate(context))

        true_count = sum(results)
        assert 30 < true_count < 70


class TestInMemoryProvider:
    """Tests for InMemoryProvider."""

    def test_set_and_get_flag(self):
        """Should store and retrieve flags."""
        provider = InMemoryProvider()
        flag = FeatureFlag(key="test", name="Test")

        provider.set_flag(flag)
        result = provider.get_flag("test")

        assert result is not None
        assert result.key == "test"

    def test_enable_flag(self):
        """Should enable a flag."""
        provider = InMemoryProvider()
        flag = FeatureFlag(key="test", name="Test", status=FlagStatus.DISABLED)
        provider.set_flag(flag)

        provider.enable("test")

        assert provider.get_flag("test").status == FlagStatus.ENABLED

    def test_disable_flag(self):
        """Should disable a flag."""
        provider = InMemoryProvider()
        flag = FeatureFlag(key="test", name="Test", status=FlagStatus.ENABLED)
        provider.set_flag(flag)

        provider.disable("test")

        assert provider.get_flag("test").status == FlagStatus.DISABLED

    def test_clear_flags(self):
        """Should clear all flags."""
        provider = InMemoryProvider()
        provider.set_flag(FeatureFlag(key="test1", name="Test1"))
        provider.set_flag(FeatureFlag(key="test2", name="Test2"))

        provider.clear()

        assert provider.get_flag("test1") is None
        assert provider.get_flag("test2") is None

    def test_all_flags(self):
        """Should return all flags."""
        provider = InMemoryProvider()
        provider.set_flag(FeatureFlag(key="test1", name="Test1"))
        provider.set_flag(FeatureFlag(key="test2", name="Test2"))

        flags = provider.all_flags()

        assert len(flags) == 2
        assert "test1" in flags
        assert "test2" in flags


class TestFileProvider:
    """Tests for FileProvider."""

    def test_load_from_file(self):
        """Should load flags from JSON file."""
        config = {
            "flags": {
                "feature1": {
                    "name": "Feature 1",
                    "status": "enabled",
                },
                "feature2": {
                    "name": "Feature 2",
                    "status": "percentage",
                    "percentage": 25,
                },
            }
        }

        with NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            f.flush()

            provider = FileProvider(f.name)

            flag1 = provider.get_flag("feature1")
            assert flag1 is not None
            assert flag1.status == FlagStatus.ENABLED

            flag2 = provider.get_flag("feature2")
            assert flag2 is not None
            assert flag2.status == FlagStatus.PERCENTAGE
            assert flag2.percentage == 25

    def test_missing_file(self):
        """Should handle missing file gracefully."""
        provider = FileProvider("/nonexistent/path.json")

        assert provider.get_flag("any") is None

    def test_reload(self):
        """Should reload flags from file."""
        config = {"flags": {"test": {"name": "Test", "status": "disabled"}}}

        with NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            f.flush()

            provider = FileProvider(f.name)
            assert provider.get_flag("test").status == FlagStatus.DISABLED

            config["flags"]["test"]["status"] = "enabled"
            with open(f.name, "w") as f2:
                json.dump(config, f2)

            provider.reload()
            assert provider.get_flag("test").status == FlagStatus.ENABLED


class TestGlobalFunctions:
    """Tests for global helper functions."""

    def setup_method(self):
        """Reset provider before each test."""
        reset_provider()

    def test_is_enabled_default(self):
        """Should return default when flag not found."""
        with patch.dict("os.environ", {}, clear=True):
            reset_provider()
            result = is_enabled("nonexistent", default=True)
            assert result is True

    def test_get_value(self):
        """Should get flag value."""
        with patch.dict("os.environ", {}, clear=True):
            reset_provider()
            result = get_value("cost_alerts", default=False)
            assert result is True

    def test_feature_flag_decorator_enabled(self):
        """Decorator should run function when flag enabled."""
        with patch("app.feature_flags.is_enabled", return_value=True):
            @feature_flag("test_flag")
            def my_function():
                return "executed"

            result = my_function()
            assert result == "executed"

    def test_feature_flag_decorator_disabled(self):
        """Decorator should return default when flag disabled."""
        with patch("app.feature_flags.is_enabled", return_value=False):
            @feature_flag("test_flag", default="skipped")
            def my_function():
                return "executed"

            result = my_function()
            assert result == "skipped"


class TestEvaluationContext:
    """Tests for EvaluationContext."""

    def test_from_request(self):
        """Should create context from request."""
        mock_cfg = type("Config", (), {"org_id": "org123", "plan": "team", "role": "admin"})()

        with patch("app.tenant_context.get_tenant", return_value=mock_cfg):
            with patch("app.tenant_context.get_actor", return_value="user456"):
                context = EvaluationContext.from_request(None)

                assert context.org_id == "org123"
                assert context.user_id == "user456"
                assert context.attributes["plan"] == "team"

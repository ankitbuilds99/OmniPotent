"""Task conditions must be evaluated without eval().

`_evaluate_condition` used to splice context values into the condition string
and hand it to eval() — with attribute access available, a model- or
request-supplied condition could walk `().__class__.__bases__[0].__subclasses__()`
to arbitrary code. The AST whitelist admits only literals, comparisons and
boolean operators.
"""

from app.coordinator.dag_executor import DAGExecutor


def _executor() -> DAGExecutor:
    return DAGExecutor(handlers={})


class TestConditionsStillWork:
    def test_equality_against_a_context_string(self):
        assert _executor()._evaluate_condition("${status} == 'ok'", {"status": "ok"})
        assert not _executor()._evaluate_condition("${status} == 'ok'", {"status": "bad"})

    def test_numeric_comparison(self):
        assert _executor()._evaluate_condition("${count} > 3", {"count": 5})
        assert not _executor()._evaluate_condition("${count} > 3", {"count": 2})

    def test_boolean_operators_and_not(self):
        ctx = {"a": 1, "b": "x"}
        assert _executor()._evaluate_condition("${a} == 1 and ${b} == 'x'", ctx)
        assert _executor()._evaluate_condition("${a} == 2 or ${b} == 'x'", ctx)
        assert _executor()._evaluate_condition("not ${a} == 2", ctx)

    def test_membership(self):
        ctx = {"service": "checkout", "critical": ["checkout", "payments"]}
        assert _executor()._evaluate_condition("${service} in ${critical}", ctx)

    def test_chained_comparison(self):
        assert _executor()._evaluate_condition("1 < ${n} < 10", {"n": 5})
        assert not _executor()._evaluate_condition("1 < ${n} < 10", {"n": 20})

    def test_unknown_placeholder_fails_closed(self):
        # ${missing} never substitutes, ast.parse fails on the literal ${...}.
        assert not _executor()._evaluate_condition("${missing} == 1", {})


class TestInjectionIsRejected:
    """Every one of these evaluated under the old eval() path."""

    def test_attribute_walk_to_subclasses_is_rejected(self):
        condition = "().__class__.__bases__[0].__subclasses__() != []"
        assert not _executor()._evaluate_condition(condition, {})

    def test_calls_are_rejected(self):
        assert not _executor()._evaluate_condition("len('abc') == 3", {})

    def test_names_are_rejected(self):
        # A bare name must not resolve to anything, truthy or otherwise.
        # (`or` still short-circuits, so the name must be in a forced position.)
        assert not _executor()._evaluate_condition("open == 1", {})
        assert not _executor()._evaluate_condition("False == True or open == open", {})

    def test_subscripts_are_rejected(self):
        assert not _executor()._evaluate_condition("${xs}[0] == 1", {"xs": [1]})

    def test_context_value_cannot_smuggle_code(self):
        # repr() of a hostile string stays a quoted literal.
        hostile = "' or ().__class__ or '"
        assert not _executor()._evaluate_condition("${v} == 'safe'", {"v": hostile})

    def test_arithmetic_is_rejected(self):
        # BinOp is not on the whitelist (no condition needs it, and it removes
        # the 10**10**10 CPU-burn vector).
        assert not _executor()._evaluate_condition("2 + 2 == 4", {})

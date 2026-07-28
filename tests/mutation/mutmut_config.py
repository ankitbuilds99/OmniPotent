"""Mutation testing configuration for mutmut.

Mutation testing validates test quality by introducing
small code changes (mutants) and verifying tests catch them.
"""


def pre_mutation(context):
    """Skip mutations in test files and generated code."""
    skip_patterns = [
        "tests/",
        "migrations/",
        "__pycache__",
        ".pyc",
        "conftest.py",
    ]

    for pattern in skip_patterns:
        if pattern in context.filename:
            context.skip = True
            return


def pre_mutation_ast(context):
    """Skip mutations that would be caught by type checking."""
    # Don't mutate type annotations
    if context.current_ast_node.__class__.__name__ == "AnnAssign":
        context.skip = True


# Mutation testing targets (high-value code paths)
MUTATION_TARGETS = [
    "app/graph/nodes.py",  # Core agent logic
    "app/policy.py",  # Security policy
    "app/guardrails.py",  # Injection defense
    "app/resilience_core.py",  # Failure handling
    "app/ratelimit.py",  # Rate limiting
    "app/session.py",  # Session management
]

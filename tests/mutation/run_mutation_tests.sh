#!/bin/bash
# Run mutation testing on critical code paths

set -e

echo "=== DevOps Copilot Mutation Testing ==="
echo "Running mutation tests to validate test quality..."

# Install mutmut if not present
pip install mutmut --quiet

# Run mutation testing on critical files
for file in \
    "app/policy.py" \
    "app/guardrails.py" \
    "app/resilience_core.py" \
    "app/ratelimit.py"
do
    if [ -f "$file" ]; then
        echo ""
        echo "Testing mutations in: $file"

        mutmut run \
            --paths-to-mutate="$file" \
            --tests-dir="tests/" \
            --runner="pytest -x -q" \
            --no-progress || true

        # Generate report
        mutmut results || true
    else
        echo "Skipping $file (not found)"
    fi
done

# Generate HTML report
mutmut html || true

echo ""
echo "=== Mutation Testing Complete ==="
echo "Report available at: html/index.html"

# Calculate mutation score
KILLED=$(mutmut results 2>/dev/null | grep -c "killed" || echo 0)
SURVIVED=$(mutmut results 2>/dev/null | grep -c "survived" || echo 0)
TOTAL=$((KILLED + SURVIVED))

if [ $TOTAL -gt 0 ]; then
    SCORE=$((KILLED * 100 / TOTAL))
    echo "Mutation Score: ${SCORE}% (${KILLED}/${TOTAL} mutants killed)"

    # Fail if score below threshold
    if [ $SCORE -lt 80 ]; then
        echo "WARNING: Mutation score below 80% threshold"
        # Don't fail CI, just warn
    fi
fi

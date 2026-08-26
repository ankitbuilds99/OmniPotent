#!/usr/bin/env bash
# Stop the uvicorn that a "Start application" step backgrounded.
#
# Every job in .github/workflows/load-tests.yml starts the app with
# `uv run uvicorn ... &` and used to just leave it running. That reads as
# harmless -- the runner is discarded seconds later anyway -- but
# astral-sh/setup-uv registers a post-job step that runs `uv cache prune --ci`,
# and the prune blocks on the cache lock the orphaned `uv run` still holds. It
# waits five minutes and then fails the job with exit code 2.
#
# So the job failed *after* every real step had already passed. In the run that
# prompted this script the smoke test reported 150/150 checks green, 0 failed
# requests, and uploaded its artifact -- and the check still went red, with the
# only error text pointing at "Post Install uv". Nothing in that message names
# the background server, which is what made it worth a comment this long.
#
# Killing the app before the post-job step runs is the entire fix.

set -uo pipefail

# `uv run` execs uvicorn as a child, so $! is uv's pid and killing that alone
# can leave uvicorn behind still holding the lock. The runner's own cleanup
# confirmed both survive: "Terminate orphan process: pid (2611) (uv)" and
# "pid (2616) (uvicorn)". Take both.
if [ -n "${APP_PID:-}" ]; then
    kill "$APP_PID" 2>/dev/null || true
fi
pkill -f 'uvicorn app.api.main:app' 2>/dev/null || true

# Wait for them to actually go. Returning while the lock is still held would
# reintroduce exactly the race this script exists to close.
for _ in $(seq 1 20); do
    pgrep -f 'uvicorn app.api.main:app' >/dev/null 2>&1 || break
    sleep 0.5
done

if pgrep -f 'uvicorn app.api.main:app' >/dev/null 2>&1; then
    echo "::warning::uvicorn ignored SIGTERM after 10s; sending SIGKILL"
    pkill -9 -f 'uvicorn app.api.main:app' 2>/dev/null || true
    sleep 1
fi

echo "Application stopped; uv cache lock released."

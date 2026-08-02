#!/usr/bin/env python3
"""THROWAWAY DIAGNOSTIC — delete before this branch merges.

Asks each candidate model for one trivial completion and reports either that
it worked or the exact free-tier quota that refused it. `error` in the adapter
is honest but opaque, and the run this diagnoses had already falsified one
guess (a longer pause made `error` MORE common, so it was never the
per-minute rate limit).

One call per model. That spends a little of the quota it is measuring, which
is the cheapest way to learn a number the API only reveals by refusing.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
CANDIDATES = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
]


def probe(model: str, key: str) -> str:
    body = json.dumps({"contents": [{"parts": [{"text": "Reply with the single word: ok"}]}]})
    request = urllib.request.Request(  # noqa: S310 - fixed https host, not user input
        f"{BASE}/models/{model}:generateContent",
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        usage = payload.get("usageMetadata", {})
        return f"HTTP 200 — call succeeded (tokens: {usage.get('totalTokenCount', '?')})"
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(raw).get("error", {})
        except json.JSONDecodeError:
            return f"HTTP {exc.code} — unparseable body: {raw[:200]}"
        violations = [
            violation
            for detail in error.get("details", [])
            if str(detail.get("@type", "")).endswith("QuotaFailure")
            for violation in detail.get("violations", [])
        ]
        if violations:
            named = "; ".join(
                f"{violation.get('quotaId')}={violation.get('quotaValue')}"
                for violation in violations
            )
            return f"HTTP {exc.code} — {named}"
        return f"HTTP {exc.code} — {error.get('status')}: {str(error.get('message'))[:160]}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"transport failure: {exc}"


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 1
    for model in CANDIDATES:
        print(f"{model:32s} {probe(model, key)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

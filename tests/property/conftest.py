"""Hypothesis configuration for the property test tier.

The property tests here exercise operations whose wall-clock timing varies
run to run and machine to machine: Ed25519 signing, RFC 8785 canonicalization,
SHA-256 hashing, and local filesystem I/O. Hypothesis's default per-example
deadline (200 ms) turns that variance into nondeterministic ``DeadlineExceeded``
failures when a single generated example happens to run slow under load — a
false negative that has nothing to do with the property being tested.

These tests assert correctness (round-trips hold, hashes are stable, invalid
inputs are rejected), never latency. Disabling the deadline for this tier keeps
"green means green" honest: a property-tier failure now always means a real
counterexample, not a slow machine. Per-example count is unchanged, so coverage
is unaffected.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

settings.register_profile(
    "mrr_property",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("mrr_property")

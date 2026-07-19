"""Standalone evaluation tooling for Meridian Research Runtime.

Not an ``mrr.*`` namespace root (task-packets/E4-T07.yaml derived_decisions):
this package is never built into the ``mrr`` distribution wheel, never added
to ``[tool.hatch.build.targets.wheel] dev-mode-dirs``/``sources``, and never
registered as an import-linter ``root_package``. It is plain, on-disk eval
tooling that imports ``mrr.*`` from the outside, exactly like a benchmark
harness for any other library would — never the other way around.
"""

from __future__ import annotations

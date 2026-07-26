"""Provider-neutral structured-generation layer (task-packets/E4-T02.yaml),
plus the package's first concrete ``ModelAdapter`` (task-packets/
E4-T08.yaml).

``mrr.adapters.llm.structured_generation`` is the first module under this
root — a higher-level function that USES an injected ``mrr.domain.
model_adapter.ModelAdapter`` (the E4-T01 port) to obtain a schema-valid
proposal from a model, with a bounded repair loop fed by Pydantic v2
validation errors. It is not itself a ``ModelAdapter``: its return type is a
parsed proposal plus an ordered audit trail, not a single
``ModelInvocationOutcome``.

This root is registered in the same import-linter "framework- and
provider-free" contract (MRR-NFR-004, MRR-NFR-010, pyproject.toml) as the
other core packages and ``mrr.adapters.object_store``. This might look like
tension with ``mrr.adapters.llm.gemini`` (below) making a real HTTP call —
it is not: the contract forbids specific PROVIDER SDKs and web/workflow
FRAMEWORKS (``openai, anthropic, boto3, botocore, fastapi, starlette,
temporalio``, pyproject.toml), never the standard library, and
``mrr.adapters.llm.gemini``/``mrr.adapters.llm.transport`` import nothing
but ``urllib``/``json``/``os`` plus this repository's own ``mrr.domain``/
``mrr.crypto`` (task-packets/E4-T08.yaml: "Kein SDK, keine neue
Abhängigkeit"; enforced independently by
tests/unit/architecture/test_llm_adapter_boundary.py's own
``_FORBIDDEN_NETWORK_MODULE_PREFIXES`` check, which the AST scan already
covers because it walks every ``.py`` file under this root, this package's
two new modules included). A future concrete vendor-SDK adapter (e.g. one
built on the ``google-generativeai``/``anthropic``/``openai`` Python SDKs
rather than a raw HTTP call) would still need its own namespace root and its
own contract treatment, precisely because it would need to import one of the
modules this contract forbids — that is a different situation from
``mrr.adapters.llm.gemini``'s stdlib-only HTTP call, which costs this
contract nothing.

``mrr.adapters.llm.transport`` is a narrow, provider-agnostic HTTP transport
abstraction (a ``Protocol`` plus one ``urllib``-based implementation with
redirects disabled and a set timeout) that any concrete provider adapter
under this package can take as a constructor-injected dependency.
``mrr.adapters.llm.gemini`` is the first such adapter: ``GeminiModelAdapter``
implements the UNCHANGED ``mrr.domain.model_adapter.ModelAdapter`` Protocol
against Google Gemini's ``generateContent`` REST endpoint, reads its API key
from the environment only, and maps a call's outcome onto all five
``TerminalStatus`` values honestly (never a generic ``"error"`` catch-all).
This package builds the edge only — task-packets/E4-T08.yaml explicitly
wires it into no orchestration; a run performing an actual model call is a
separate, later task.
"""

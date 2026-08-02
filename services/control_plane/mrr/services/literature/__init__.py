"""The literature channel (task-packets/N1-T05.yaml): turn anchored sources
and their model-proposed relations into a corpus that
``mrr.services.node_runtime.synthesis_executor`` accepts — and therefore into
a QUESTION that ``.github/workflows/research-run.yml`` will answer.

Step 3 of the owner's ordering (1. standard -> 2. model-assisted
classification -> 3. literature channel -> 4. coupling). It is the first
consumer of step 2: a ``CorpusEntry`` is not a bare source but a CLASSIFIED
one — ``evidence_relation``, ``claim_relevant_finding`` and ``claim_type`` are
all required — so a channel that fills a corpus needs something that reads,
and that something is what N1-T04 built.

Like its siblings ``mrr.services.classification`` and
``mrr.services.validation``, this package opens no database connection,
constructs no repository, reaches no model and touches no network. Everything
here is a deterministic join over files that already exist: the batch
manifest, the anchored content snapshot, and the proposal artefact.

--- The two axes, and why they are never mixed ------------------------------

``CorpusEntry`` carries two independent judgements and confusing them produces
a wrong verdict with no diagnostic at all:

* ``verification_status`` is the SOURCE-ANCHORING axis. MRR-MTH-015:
  "every EvidenceMatrix row MUST anchor a resolvable source with a
  verification status and a source family". It says the excerpt was fetched
  and hashed. It says NOTHING about the standing of the relation.
* ``evidence_relation`` is what the source says about the claim. Here it is a
  model proposal, carried with its rationale and its measured error rate.

So an entry may be ``verified`` on the strength of its anchored excerpt while
its relation is a proposal. That is what the field means — deliberately NOT
the move ``synthesis_executor.py:483`` makes, where ``verified`` is stamped on
a merely schema-valid MODEL RESPONSE. There the word describes the model;
here it describes the fetch.

The cost of getting this wrong is quiet: ``_classify_analysis`` gates
``min_included_sources`` on ``verification_status == "verified"``
(``synthesis_executor.py:688``) but builds the supporting and contradicting
sets from EVERY row (``:709-714``). A corpus with swapped axes runs to
completion and reports a verdict nobody can question.

--- What the model decides here, and what that costs ------------------------

Owner decision, 2026-08-02, taken with the objection in front of him: the
model proposal drives directly, with no human classification gate between the
channel and the run. The cost is recorded rather than softened. At Cohen's
kappa 0.3084 and accuracy 0.5439 against a majority floor of 0.4211, roughly
every second proposed relation is wrong, and those relations are exactly what
the eligibility thresholds count. Any claim this corpus produces inherits that
error rate.

This package therefore makes the corpus SAY SO: every entry's ``extraction``
dict carries ``classification_provenance`` naming the model, the prompt hash,
the proposal artefact and the measured accuracy beside its floor. Nobody
reading an entry can mistake a proposal for a finding.
"""

from __future__ import annotations

from mrr.services.literature.corpus_builder import (
    CorpusBuildError,
    CorpusBuildRefusedError,
    LiteratureCorpusBuilder,
)

__all__ = [
    "CorpusBuildError",
    "CorpusBuildRefusedError",
    "LiteratureCorpusBuilder",
]

"""Mirrors schemas/node-manifest.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.2, "NodeManifest": a node's available actions and restrictions).
"""

from __future__ import annotations

from typing import Annotated, Literal

from mrr.contracts.common import ApprovalMode, AutonomyLevel, BaseObject, MRRModel, Signature, Urn
from pydantic import AwareDatetime, Field, StringConstraints

#: Mirrors `capabilities[].network_profile`.
NetworkProfile = Literal["none", "allowlist", "unrestricted_forbidden"]

#: Mirrors the top-level `transport_modes` item enum.
TransportMode = Literal["online", "offline_bundle"]

#: Mirrors `capabilities[].name`'s pattern: one or more dot-separated
#: lowercase/digit/hyphen segments, e.g. "literature.retrieve".
_CapabilityName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")]


class CapabilityDefinition(MRRModel):
    """Mirrors a `capabilities[]` entry. All seven properties are required
    and no others are allowed (`additionalProperties: false`).

    `input_schema` and `output_schema` are plain schema-identifier strings
    in the JSON Schema (`{"type": "string"}`, no `$ref` to `$defs.urn`) —
    docs/spec/02_DOMAIN_MODEL.md section 2.2's example value
    (``urn:mrr:schema:literature-query:1``) does not itself satisfy the
    strict ULID-suffixed `$defs.urn` pattern, so narrowing these two fields
    to the `Urn` type would reject schema-valid values the JSON Schema
    accepts. They stay plain `str`, matching the schema exactly.
    """

    name: _CapabilityName
    version: str
    input_schema: str
    output_schema: str
    max_autonomy: AutonomyLevel
    approval: ApprovalMode
    network_profile: NetworkProfile


class NodeManifest(BaseObject):
    """Mirrors schemas/node-manifest.schema.json.

    `accepted_classifications` and `data_residency` are the only two
    properties absent from the schema's top-level `required` list.
    `accepted_classifications` is deliberately typed `list[str]`, not
    `list[Classification]`: the schema itself declares its items as plain
    `{"type": "string"}` with no enum restriction (unlike, say,
    research-score.schema.json's `data_classes`), so narrowing it here
    would add a constraint the schema does not impose.
    """

    kind: Literal["NodeManifest"]
    node_id: Urn
    capabilities: list[CapabilityDefinition] = Field(min_length=1)
    restrictions: list[str]
    accepted_classifications: list[str] = Field(default_factory=list)
    data_residency: str | None = None
    transport_modes: list[TransportMode]
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    public_keys: list[str] = Field(min_length=1)
    signature: Signature

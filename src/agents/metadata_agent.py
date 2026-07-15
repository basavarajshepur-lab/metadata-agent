"""Metadata Intelligence Agent.

Agentic loop (max 6 turns):
  1. search_field_glossary   — cross-dataset PII/sensitivity consistency
  2. get_dataset_history     — understand the data landscape
  3. get_regulation_updates  — fetch live regulatory guidance (BIS/ICO/FCA/EBA)
  4. generate_dataset_metadata — produce the structured output

Prompt caching is applied to the system prompt to reduce cost on repeated runs.
"""

import json
from typing import Any

from .base import BaseAgent
from .loader import load_agent
from ..config import AgentConfig
from ..extractors.base import DatasetProfile
from ..guardrails import apply_all
from ..evals import run as run_evals
from ..schema import (
    ComplianceInfo,
    DataDomain,
    DataType,
    DatasetMetadata,
    FieldConstraints,
    FieldMetadata,
    PIIType,
    QualityScore,
    RegulatoryFramework,
    SensitivityLevel,
)

try:
    from ..memory.memory_store import (
        get_run_history,
        glossary_size,
        search_glossary,
        store_run,
    )
    _MEMORY_OK = True
except Exception:
    _MEMORY_OK = False

try:
    from ..regulations.fetcher import get_regulation_context
    _REGULATIONS_OK = True
except Exception:
    _REGULATIONS_OK = False

_MAX_TURNS = 6

# ── Load system prompt and config from agents/metadata_agent.md ─────────────

_MD_CONFIG, _SYSTEM_PROMPT = load_agent("metadata_agent")

# Hard-coded fallback if the markdown file is missing
if not _SYSTEM_PROMPT:
    _SYSTEM_PROMPT = (
        "You are a senior data architect and metadata specialist at a global bank. "
        "Generate comprehensive banking-grade metadata with precise PII classification, "
        "sensitivity levels, BCBS 239 lineage, and GDPR compliance flags."
    )

# ── Tool schemas ────────────────────────────────────────────────────────────

_MEMORY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_field_glossary",
        "description": (
            "Search the enterprise field glossary for fields matching the given names. "
            "Returns prior PII classification, sensitivity level, data type, and descriptions. "
            "Use to enforce cross-dataset consistency."
        ),
        "input_schema": {
            "type": "object",
            "required": ["field_names"],
            "properties": {
                "field_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "All field names from the current dataset profile",
                }
            },
        },
    },
    {
        "name": "get_dataset_history",
        "description": "Retrieve summaries of previously catalogued datasets to understand the data landscape.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10}
            },
        },
    },
]

_REGULATION_TOOL: dict[str, Any] = {
    "name": "get_regulation_updates",
    "description": (
        "Fetch the latest regulatory guidance from official sources (BIS BCBS, ICO, FCA, EBA). "
        "Call this when processing risk data, personal data, or any dataset that needs BCBS 239 "
        "or GDPR compliance flags to ensure the most current rules are applied."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "frameworks": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["BCBS_239", "UK_GDPR", "FCA", "EBA"],
                },
                "description": "Frameworks to check for recent updates",
            }
        },
    },
}

_METADATA_TOOL: dict[str, Any] = {
    "name": "generate_dataset_metadata",
    "description": "Generate comprehensive, banking-grade metadata for a dataset",
    "input_schema": {
        "type": "object",
        "required": [
            "description", "business_context", "data_domain",
            "classification", "data_classification_rationale",
            "fields", "compliance", "usage_guidance",
        ],
        "properties": {
            "description": {"type": "string"},
            "business_context": {"type": "string"},
            "data_domain": {"type": "string", "enum": [d.value for d in DataDomain]},
            "sub_domain": {"type": "string"},
            "classification": {"type": "string", "enum": [s.value for s in SensitivityLevel]},
            "data_classification_rationale": {"type": "string"},
            "source_system": {"type": "string"},
            "usage_guidance": {"type": "string"},
            "known_limitations": {"type": "string"},
            "related_datasets": {"type": "array", "items": {"type": "string"}},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "name", "display_name", "description", "business_context",
                        "data_type", "is_pii", "sensitivity_level", "usage_guidance",
                    ],
                    "properties": {
                        "name": {"type": "string"},
                        "display_name": {"type": "string"},
                        "description": {"type": "string"},
                        "business_context": {"type": "string"},
                        "data_type": {"type": "string", "enum": [d.value for d in DataType]},
                        "format": {"type": "string"},
                        "is_pii": {"type": "boolean", "description": "true or false only."},
                        "pii_type": {"type": "string", "enum": [p.value for p in PIIType]},
                        "sensitivity_level": {"type": "string", "enum": [s.value for s in SensitivityLevel]},
                        "is_key_field": {"type": "boolean", "description": "true or false only."},
                        "usage_guidance": {"type": "string"},
                        "example_usage": {"type": "string"},
                        "business_rules": {"type": "string"},
                        "data_lineage": {"type": "string"},
                        "quality_notes": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "constraints": {
                            "type": "object",
                            "properties": {
                                "nullable": {"type": "boolean"},
                                "unique": {"type": "boolean"},
                                "min_value": {"type": "number"},
                                "max_value": {"type": "number"},
                                "min_length": {"type": "integer"},
                                "max_length": {"type": "integer"},
                                "pattern": {"type": "string"},
                                "allowed_values": {"type": "array", "items": {"type": "string"}},
                                "foreign_key_ref": {"type": "string"},
                                "precision": {"type": "integer"},
                                "scale": {"type": "integer"},
                            },
                        },
                    },
                },
            },
            "compliance": {
                "type": "object",
                "required": ["gdpr_applicable", "uk_gdpr_applicable"],
                "properties": {
                    "gdpr_applicable": {"type": "boolean", "description": "true or false only — reasoning goes in lawful_basis or a field's usage_guidance, not here."},
                    "uk_gdpr_applicable": {"type": "boolean", "description": "true or false only."},
                    "regulatory_frameworks": {
                        "type": "array",
                        "items": {"type": "string", "enum": [r.value for r in RegulatoryFramework]},
                    },
                    "data_residency_requirements": {"type": "string"},
                    "retention_period": {"type": "string"},
                    "cross_border_transfer_restrictions": {"type": "boolean", "description": "true or false only — explain which rails/corridors trigger this in data_residency_requirements or retention_period, not here."},
                    "consent_required": {"type": "boolean", "description": "true or false only."},
                    "right_to_erasure_applicable": {"type": "boolean", "description": "true or false only."},
                    "lawful_basis": {"type": "string"},
                },
            },
        },
    },
}

# ── Helper functions ────────────────────────────────────────────────────────

def _build_user_message(profile: DatasetProfile) -> str:
    if _MEMORY_OK:
        n = glossary_size()
        memory_hint = (
            f"\n\nThe enterprise glossary contains {n} field definitions from past runs. "
            f"Call search_field_glossary with all {len(profile.fields)} field names now."
            if n > 0
            else "\n\n(Enterprise glossary is empty — check it first, then generate.)"
        )
    else:
        memory_hint = ""

    return f"""Generate comprehensive banking-grade metadata for the following dataset.

{profile.to_prompt_context()}{memory_hint}

Be precise about PII classification, sensitivity levels, and regulatory obligations.
For every field, write descriptions and business context that a data analyst can act on immediately.
Apply BCBS 239 lineage and quality principles where appropriate."""


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Tool schemas declare these as booleans, but the model isn't always compliant —
    it occasionally writes an explanatory sentence instead of true/false. Treat any
    non-empty freeform text as an affirmative explanation rather than crash the run."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0", ""):
            return False
        return True  # non-empty explanatory text — the model was justifying a "yes"
    return default


def _parse_tool_result(raw: dict[str, Any], dataset_name: str) -> DatasetMetadata:
    fields = []
    for f in raw.get("fields", []):
        constraints_raw = f.get("constraints") or {}
        constraints_raw = dict(constraints_raw)
        if "nullable" in constraints_raw:
            constraints_raw["nullable"] = _coerce_bool(constraints_raw["nullable"], True)
        if "unique" in constraints_raw:
            constraints_raw["unique"] = _coerce_bool(constraints_raw["unique"], False)
        constraints = FieldConstraints(**{k: v for k, v in constraints_raw.items() if v is not None})
        pii_type_raw = f.get("pii_type")
        pii_type = PIIType(pii_type_raw) if pii_type_raw else None
        fields.append(
            FieldMetadata(
                name=f["name"],
                display_name=f.get("display_name", f["name"].replace("_", " ").title()),
                description=f.get("description", ""),
                business_context=f.get("business_context", ""),
                data_type=DataType(f.get("data_type", "string")),
                format=f.get("format"),
                constraints=constraints,
                is_pii=_coerce_bool(f.get("is_pii", False)),
                pii_type=pii_type,
                sensitivity_level=SensitivityLevel(f.get("sensitivity_level", "internal")),
                is_key_field=_coerce_bool(f.get("is_key_field", False)),
                usage_guidance=f.get("usage_guidance", ""),
                example_usage=f.get("example_usage"),
                business_rules=f.get("business_rules"),
                data_lineage=f.get("data_lineage"),
                quality_notes=f.get("quality_notes"),
                tags=f.get("tags", []),
            )
        )

    compliance_raw = raw.get("compliance", {})
    reg_frameworks = []
    for rf in compliance_raw.get("regulatory_frameworks", []):
        try:
            reg_frameworks.append(RegulatoryFramework(rf))
        except ValueError:
            pass

    compliance = ComplianceInfo(
        gdpr_applicable=_coerce_bool(compliance_raw.get("gdpr_applicable", False)),
        uk_gdpr_applicable=_coerce_bool(compliance_raw.get("uk_gdpr_applicable", False)),
        regulatory_frameworks=reg_frameworks,
        data_residency_requirements=compliance_raw.get("data_residency_requirements"),
        retention_period=compliance_raw.get("retention_period"),
        cross_border_transfer_restrictions=_coerce_bool(compliance_raw.get("cross_border_transfer_restrictions", False)),
        consent_required=_coerce_bool(compliance_raw.get("consent_required", False)),
        right_to_erasure_applicable=_coerce_bool(compliance_raw.get("right_to_erasure_applicable", False)),
        lawful_basis=compliance_raw.get("lawful_basis"),
    )

    return DatasetMetadata(
        dataset_name=raw.get("dataset_name", dataset_name),
        description=raw.get("description", ""),
        business_context=raw.get("business_context", ""),
        data_domain=DataDomain(raw.get("data_domain", "reference")),
        sub_domain=raw.get("sub_domain"),
        classification=SensitivityLevel(raw.get("classification", "internal")),
        data_classification_rationale=raw.get("data_classification_rationale", ""),
        source_system=raw.get("source_system"),
        fields=fields,
        compliance=compliance,
        usage_guidance=raw.get("usage_guidance", ""),
        known_limitations=raw.get("known_limitations"),
        related_datasets=raw.get("related_datasets", []),
    )


# ── Agent class ─────────────────────────────────────────────────────────────

class MetadataAgent(BaseAgent):
    """
    Generates banking-grade metadata from a DatasetProfile.

    Agentic loop order:
      search_field_glossary → get_regulation_updates → get_dataset_history
      → generate_dataset_metadata
    """

    def __init__(self, config: AgentConfig | None = None):
        super().__init__(config)
        self._metadata_raw: dict | None = None

        tool_list = []
        if _MEMORY_OK:
            tool_list.extend(_MEMORY_TOOLS)
        if _REGULATIONS_OK:
            tool_list.append(_REGULATION_TOOL)
        tool_list.append(_METADATA_TOOL)
        self._tools = tool_list

    @property
    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    @property
    def tools(self) -> list[dict]:
        return self._tools

    def handle_tool_call(self, name: str, inputs: dict) -> str:
        if name == "generate_dataset_metadata":
            self._metadata_raw = inputs
            return "Metadata accepted."

        if name == "search_field_glossary" and _MEMORY_OK:
            results = search_glossary(inputs.get("field_names", []))
            return json.dumps(results) if results else "No matching fields in glossary."

        if name == "get_dataset_history" and _MEMORY_OK:
            results = get_run_history(inputs.get("limit", 10))
            return json.dumps(results) if results else "No previous datasets found."

        if name == "get_regulation_updates" and _REGULATIONS_OK:
            return get_regulation_context(
                frameworks=inputs.get("frameworks", []),
                max_age_hours=24,
            )

        return "Tool not available."

    def run(self, profile: DatasetProfile) -> tuple[DatasetMetadata, QualityScore]:
        """Generate metadata, apply guardrails, run evals, persist to memory."""
        self._metadata_raw = None
        messages = [{"role": "user", "content": _build_user_message(profile)}]

        for _turn in range(_MAX_TURNS):
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                tools=self.tools,
                tool_choice={"type": "any"},
            )

            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_blocks:
                raise RuntimeError("Agent turn produced no tool use.")

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_blocks:
                result = self.handle_tool_call(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
            messages.append({"role": "user", "content": tool_results})

            if self._metadata_raw is not None:
                break

        if self._metadata_raw is None:
            raise RuntimeError(f"Agent did not generate metadata within {_MAX_TURNS} turns.")

        self._metadata_raw["dataset_name"] = profile.dataset_name
        metadata = _parse_tool_result(self._metadata_raw, profile.dataset_name)

        metadata, guardrail_messages = apply_all(metadata)
        quality = run_evals(metadata, profile, guardrail_messages)
        metadata.quality_score = quality

        if _MEMORY_OK:
            try:
                store_run(metadata, quality)
            except Exception:
                pass

        return metadata, quality

    # Backward-compat alias used by demo.py, watcher.py, app.py
    def generate(self, profile: DatasetProfile) -> tuple[DatasetMetadata, QualityScore]:
        return self.run(profile)

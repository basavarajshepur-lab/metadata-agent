"""Unit tests for parsing the raw tool-call dict Claude returns into DatasetMetadata.

Both bugs covered here (2026-07-15) crashed the live trial in production before
being caught locally, because nothing exercised `_parse_tool_result` against
anything other than a clean, complete tool call.
"""

from src.agents.metadata_agent import _coerce_bool, _coerce_str_list, _parse_tool_result


def test_coerce_bool_passes_through_real_booleans():
    assert _coerce_bool(True) is True
    assert _coerce_bool(False) is False


def test_coerce_bool_parses_common_string_forms():
    assert _coerce_bool("true") is True
    assert _coerce_bool("Yes") is True
    assert _coerce_bool("false") is False
    assert _coerce_bool("No") is False
    assert _coerce_bool("") is False


def test_coerce_bool_freeform_explanation_does_not_crash():
    """Regression: model wrote a full sentence into cross_border_transfer_restrictions
    instead of true/false. Pydantic's bool_parsing crashed the whole run. Any non-empty
    freeform explanation is treated as an affirmative rather than raising."""
    text = "SEPA and SWIFT transactions to third-country recipients trigger this."
    assert _coerce_bool(text) is True


def test_coerce_bool_missing_value_uses_default():
    assert _coerce_bool(None, default=True) is True
    assert _coerce_bool(None, default=False) is False


def test_coerce_str_list_stringifies_non_string_items():
    """Regression: model wrote allowed_values: [true, false] for a boolean-typed
    column instead of ["true", "false"], crashing FieldConstraints validation."""
    assert _coerce_str_list([True, False]) == ["true", "false"]
    assert _coerce_str_list(["already", "strings"]) == ["already", "strings"]
    assert _coerce_str_list([1, 2.5, "x"]) == ["1", "2.5", "x"]
    assert _coerce_str_list(None) == []
    assert _coerce_str_list([]) == []


def _minimal_raw_tool_call(**overrides) -> dict:
    raw = {
        "description": "d",
        "business_context": "b",
        "data_domain": "transaction",
        "classification": "restricted",
        "data_classification_rationale": "r",
        "usage_guidance": "u",
        "fields": [],
        "compliance": {"gdpr_applicable": False, "uk_gdpr_applicable": False},
    }
    raw.update(overrides)
    return raw


def test_parse_tool_result_handles_truncated_response():
    """Regression: max_tokens hit mid-generation left `fields` and `compliance`
    entirely absent from the tool input. Must degrade to empty, not crash."""
    raw = {
        "description": "d",
        "business_context": "b",
        "data_domain": "transaction",
        "classification": "restricted",
        "data_classification_rationale": "r",
    }
    md = _parse_tool_result(raw, "Truncated Dataset")
    assert md.fields == []
    assert md.compliance.gdpr_applicable is False


def test_parse_tool_result_coerces_boolean_fields_in_compliance():
    raw = _minimal_raw_tool_call(
        compliance={
            "gdpr_applicable": "true",
            "uk_gdpr_applicable": "Restrictions apply because of cross-border rails.",
            "cross_border_transfer_restrictions": "yes",
        }
    )
    md = _parse_tool_result(raw, "Test Dataset")
    assert md.compliance.gdpr_applicable is True
    assert md.compliance.uk_gdpr_applicable is True  # freeform text -> affirmative
    assert md.compliance.cross_border_transfer_restrictions is True


def test_parse_tool_result_coerces_boolean_fields_in_field_entries():
    raw = _minimal_raw_tool_call(
        fields=[
            {
                "name": "account_number",
                "display_name": "Account Number",
                "description": "d",
                "business_context": "b",
                "data_type": "string",
                "is_pii": "Yes, this identifies a specific customer account.",
                "pii_type": "account_number",
                "sensitivity_level": "restricted",
                "is_key_field": "false",
                "usage_guidance": "u",
                "constraints": {"nullable": "false", "unique": "true"},
            }
        ]
    )
    md = _parse_tool_result(raw, "Test Dataset")
    assert len(md.fields) == 1
    f = md.fields[0]
    assert f.is_pii is True
    assert f.is_key_field is False
    assert f.constraints.nullable is False
    assert f.constraints.unique is True


def test_parse_tool_result_coerces_allowed_values_and_tags():
    raw = _minimal_raw_tool_call(
        fields=[
            {
                "name": "is_active",
                "display_name": "Is Active",
                "description": "d",
                "business_context": "b",
                "data_type": "boolean",
                "is_pii": False,
                "sensitivity_level": "internal",
                "usage_guidance": "u",
                "tags": ["flag", True],
                "constraints": {"allowed_values": [True, False]},
            }
        ],
        related_datasets=["Account Master", 42],
    )
    md = _parse_tool_result(raw, "Test Dataset")
    assert md.fields[0].constraints.allowed_values == ["true", "false"]
    assert md.fields[0].tags == ["flag", "true"]
    assert md.related_datasets == ["Account Master", "42"]


def test_parse_tool_result_full_realistic_payload():
    raw = _minimal_raw_tool_call(
        fields=[
            {
                "name": "customer_id",
                "display_name": "Customer ID",
                "description": "d",
                "business_context": "b",
                "data_type": "uuid",
                "is_pii": False,
                "sensitivity_level": "internal",
                "usage_guidance": "u",
            }
        ],
        compliance={
            "gdpr_applicable": True,
            "uk_gdpr_applicable": True,
            "regulatory_frameworks": ["UK_GDPR", "BCBS_239"],
        },
    )
    md = _parse_tool_result(raw, "Test Dataset")
    assert len(md.fields) == 1
    assert md.compliance.gdpr_applicable is True
    assert len(md.compliance.regulatory_frameworks) == 2

"""Exporter regression tests.

These are fast and deterministic — no API key, no network. They exist because
both live export bugs (2026-07-15) were triggered by Unicode punctuation and
paragraph/table confusion that a synthetic fixture reproduces perfectly; they
did not require a real model call to catch, they were just never tested.
"""

from src.exporters import export_csv, export_pdf, export_word
from src.exporters.pdf_exporter import _sanitize_pdf_text


def test_csv_export_does_not_crash(adversarial_metadata):
    data = export_csv(adversarial_metadata)
    assert data
    assert b"transaction_id" in data


def test_pdf_export_does_not_crash(adversarial_metadata):
    """Regression: FPDFUnicodeEncodingException on en-dash in compliance text."""
    data = export_pdf(adversarial_metadata)
    assert data
    assert data[:4] == b"%PDF"


def test_word_export_does_not_crash(adversarial_metadata):
    """Regression: AttributeError 'CT_P' object has no attribute '_tc'
    from shading the classification banner paragraph with the cell-shading helper."""
    data = export_word(adversarial_metadata)
    assert data
    assert data[:2] == b"PK"  # docx is a zip archive


def test_pdf_sanitize_strips_unencodable_unicode():
    dirty = "GDPR Article 44–50, don't use 'smart' quotes… or • bullets"
    clean = _sanitize_pdf_text(dirty)
    clean.encode("latin-1")  # must not raise
    assert "–" not in clean
    assert "…" not in clean
    assert "•" not in clean


def test_pdf_export_survives_missing_optional_fields(adversarial_metadata):
    """Optional fields (sub_domain, source_system, known_limitations, ...) are
    routinely None — the exporters must not assume they're always populated."""
    md = adversarial_metadata.model_copy(deep=True)
    md.sub_domain = None
    md.source_system = None
    md.known_limitations = None
    md.data_steward = None
    md.data_owner = None
    md.compliance.retention_period = None
    md.compliance.data_residency_requirements = None
    md.compliance.lawful_basis = None
    export_pdf(md)
    export_word(md)
    export_csv(md)


def test_pdf_export_handles_zero_fields():
    """Regression: a model call that truncates before emitting the fields array
    (2026-07-15, max_tokens too low) must not also break the exporters."""
    md = _make_empty_fields_metadata()
    export_pdf(md)
    export_word(md)
    export_csv(md)


def _make_empty_fields_metadata():
    from src.schema import ComplianceInfo, DataDomain, DatasetMetadata, SensitivityLevel

    return DatasetMetadata(
        dataset_name="Empty Fields Regression",
        description="Simulates a truncated agent response with no fields.",
        business_context="—",
        data_domain=DataDomain.REFERENCE,
        classification=SensitivityLevel.INTERNAL,
        data_classification_rationale="—",
        fields=[],
        compliance=ComplianceInfo(gdpr_applicable=False, uk_gdpr_applicable=False),
        usage_guidance="—",
    )

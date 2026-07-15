"""Shared fixtures for the regression suite.

The `adversarial_metadata` fixture is deliberately built from hand-picked
values that have already broken an exporter in production, rather than
generic sample data. Add to it whenever a new exporter bug is found instead
of only fixing the code — that is what keeps this suite from regressing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schema import (  # noqa: E402
    ComplianceInfo,
    DataDomain,
    DataType,
    DatasetMetadata,
    FieldConstraints,
    FieldMetadata,
    QualityDimension,
    QualityScore,
    RegulatoryFramework,
    SensitivityLevel,
)


@pytest.fixture
def adversarial_metadata() -> DatasetMetadata:
    """A DatasetMetadata built to trip every exporter bug seen so far:
    - Unicode em/en dashes and curly quotes in free text (broke the PDF exporter,
      2026-07-15: FPDFUnicodeEncodingException on Helvetica's latin-1-only core font)
    - Very long key labels and long unbroken strings (URLs) in compliance fields
    - Every field left populated so the classification banner + all tables render
      (broke the Word exporter, 2026-07-15: _set_cell_bg called on a paragraph, not
      a cell — AttributeError: 'CT_P' object has no attribute '_tc')
    """
    fields = [
        FieldMetadata(
            name="transaction_id",
            display_name="Transaction ID",
            description="UUID assigned at transaction creation — used as the primary key.",
            business_context="Joins to the Clearing House Ledger and Settlement System — see https://internal.example.com/very/long/unbroken/documentation/link/that/does/not/wrap/naturally/at/all",
            data_type=DataType.STRING,
            constraints=FieldConstraints(nullable=False, unique=True),
            is_pii=False,
            sensitivity_level=SensitivityLevel.INTERNAL,
            is_key_field=True,
            usage_guidance="Safe to use in non-prod without masking — it is not a natural-language identifier.",
        ),
        FieldMetadata(
            name="counterparty_name",
            display_name="Counterparty Name",
            description="Full legal name of the external payee — GDPR Article 4(1) personal data.",
            business_context="Used for statement generation and dispute handling – never export in bulk without aggregation.",
            data_type=DataType.STRING,
            constraints=FieldConstraints(nullable=True, unique=False),
            is_pii=True,
            sensitivity_level=SensitivityLevel.CONFIDENTIAL,
            is_key_field=False,
            usage_guidance="Restricted to Payment Ops and Compliance — mask in UAT/DEV per data residency rules (see Article 44–50).",
            quality_notes="Null rate ~40% — only populated for external transfers, not internal account moves.",
        ),
    ]

    compliance = ComplianceInfo(
        gdpr_applicable=True,
        uk_gdpr_applicable=True,
        regulatory_frameworks=[RegulatoryFramework.UK_GDPR, RegulatoryFramework.BCBS_239],
        data_residency_requirements=(
            "All customer data must remain within UK/EEA (GDPR Article 44–50). "
            "Cross–border transfers to non‑EEA jurisdictions prohibited without "
            "an adequacy decision or Standard Contractual Clauses (SCCs)."
        ),
        retention_period="Retain for account lifetime + 7 years post–closure (HMRC, GDPR Art. 17 exception).",
        cross_border_transfer_restrictions=True,
        consent_required=False,
        right_to_erasure_applicable=True,
        lawful_basis="Performance of contract — Article 6(1)(b); legitimate interest — Article 6(1)(f).",
    )

    quality_score = QualityScore(
        completeness=QualityDimension(score=95.0, passed=True),
        pii_detection=QualityDimension(score=100.0, passed=True),
        type_consistency=QualityDimension(score=98.0, passed=True),
        banking_standards=QualityDimension(score=90.0, passed=True),
        sensitivity_consistency=QualityDimension(score=100.0, passed=True),
        overall_score=96.6,
        passed=True,
        guardrails_applied=["PII floor enforcement", "Sensitivity consistency check"],
    )

    return DatasetMetadata(
        dataset_name="Payment Transaction — Regression Fixture",
        description="Retail and corporate payment transactions – domestic and cross–border rails.",
        business_context="Primary source of truth for payment activity — feeds AML, finance, and regulatory reporting.",
        data_domain=DataDomain.TRANSACTION,
        sub_domain="payment",
        classification=SensitivityLevel.RESTRICTED,
        data_classification_rationale="Contains financial identifiers and AML risk intelligence — restricted by default.",
        source_system="Core Banking Platform",
        fields=fields,
        compliance=compliance,
        usage_guidance="Role-based access only — see field-level guidance for specifics.",
        known_limitations="Settlement date lags timestamp by 1–3 business days.",
        related_datasets=["Account Master", "AML Alert Feed"],
        quality_score=quality_score,
    )

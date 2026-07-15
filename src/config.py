"""Agent configuration and banking standards constants."""

import os
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class AgentConfig:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 16000
    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))

    def validate(self) -> None:
        if not self.api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Add it to your .env file or environment."
            )


# Minimum sensitivity level for each PII type. Guardrail enforces these floors.
PII_SENSITIVITY_FLOORS: Dict[str, str] = {
    "name": "confidential",
    "email": "confidential",
    "phone": "confidential",
    "address": "confidential",
    "date_of_birth": "confidential",
    "national_id": "restricted",
    "account_number": "restricted",
    "sort_code": "restricted",
    "iban": "restricted",
    "card_number": "secret",
    "cvv": "secret",
    "tax_id": "restricted",
    "biometric": "secret",
    "special_category": "restricted",
}

# Regex patterns for rule-based PII detection (used in evals to cross-check AI output)
PII_PATTERNS: Dict[str, str] = {
    "email": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "phone": r"(\+44\s?|0)[\d\s\-]{9,13}",
    "account_number": r"^[0-9]{8}$",
    "sort_code": r"^\d{2}[-\s]?\d{2}[-\s]?\d{2}$",
    "iban": r"^[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]{0,16})$",
    "national_id": r"^[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]$",  # UK NI
    "card_number": r"^\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}$",
    "date_of_birth": r"(dob|date.of.birth|birth.date|birthdate)",
}

# Column name heuristics for PII detection
PII_COLUMN_HINTS: Dict[str, str] = {
    "email": ["email", "email_address", "e_mail", "electronic_mail"],
    "name": [
        "first_name", "last_name", "full_name", "surname", "forename",
        "given_name", "family_name", "customer_name", "client_name",
    ],
    "phone": ["phone", "mobile", "telephone", "tel", "phone_number", "mobile_number"],
    "address": [
        "address", "street", "postcode", "zip_code", "city", "county",
        "address_line", "billing_address", "home_address",
    ],
    "date_of_birth": ["dob", "date_of_birth", "birth_date", "birthdate", "born"],
    "national_id": [
        "ni_number", "nino", "national_insurance", "ssn", "social_security",
        "passport_number", "national_id",
    ],
    "account_number": [
        "account_number", "account_no", "acct_number", "bank_account",
    ],
    "sort_code": ["sort_code", "sort_no", "bank_sort_code"],
    "iban": ["iban", "international_bank_account"],
    "card_number": ["card_number", "pan", "card_no", "credit_card", "debit_card"],
    "tax_id": ["tax_id", "tin", "vat_number", "tax_reference", "utr"],
}

# BCBS 239 required metadata fields per principle
BCBS_239_REQUIRED_FIELDS: List[str] = [
    "description",
    "business_context",
    "data_lineage",
    "usage_guidance",
    "quality_notes",
]

# Eval thresholds
EVAL_THRESHOLDS = {
    "completeness": 70.0,
    "pii_detection": 80.0,
    "type_consistency": 75.0,
    "banking_standards": 70.0,
    "sensitivity_consistency": 90.0,
    "overall": 75.0,
}

# Field completeness weights (must sum to 1.0)
FIELD_COMPLETENESS_WEIGHTS = {
    "description": 0.25,
    "business_context": 0.20,
    "data_type": 0.15,
    "sensitivity_level": 0.15,
    "usage_guidance": 0.15,
    "constraints": 0.05,
    "data_lineage": 0.05,
}

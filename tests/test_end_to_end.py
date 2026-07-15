"""Real end-to-end regression tests — actually call the live Anthropic API.

These are the only tests that would have caught the two bugs that reached the
live trial on 2026-07-15 at their source: the model truncating mid-JSON before
emitting the `fields` array (max_tokens too low), and the model writing
freeform text into a boolean tool-input field. Both are model-behavior bugs
that no synthetic fixture reproduces reliably — they have to be caught by
actually calling the model.

Costs real API tokens and takes ~30-60s per sample. Not run by default.
Run explicitly before any change to metadata_agent.py, config.py, the
agents/*.md prompts, or the exporters:

    pytest -m integration tests/test_end_to_end.py -v

Requires ANTHROPIC_API_KEY in the environment / .env.
"""

import os

import pytest

from src.agents.metadata_agent import MetadataAgent
from src.config import AgentConfig
from src.exporters import export_csv, export_pdf, export_word
from src.extractors import extract

pytestmark = pytest.mark.integration

_SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "samples")

# The largest sample (most fields, most prose) is the one that actually
# triggered the max_tokens truncation — always include it.
_SAMPLES = [
    "transaction_schema.json",
    "customer_accounts.csv",
    "risk_positions.sql",
]


def _skip_if_no_api_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live integration test")


@pytest.mark.parametrize("sample_file", _SAMPLES)
def test_full_pipeline_generates_and_exports_without_error(sample_file):
    """Generate real metadata for a sample and push it through every exporter.

    Assertions are deliberately loose on content (model output varies run to
    run) and strict on structure: the run must not truncate before emitting
    fields/compliance, and every exporter must produce non-empty bytes.
    """
    _skip_if_no_api_key()

    profile = extract(os.path.join(_SAMPLES_DIR, sample_file))
    config = AgentConfig(model="claude-haiku-4-5-20251001")  # matches TRIAL_MODE on the live app
    agent = MetadataAgent(config)

    metadata, quality = agent.run(profile)

    # The core regression: fields/compliance must not come back empty due to
    # the model running out of output budget before reaching them.
    assert len(metadata.fields) == len(profile.fields), (
        f"Expected {len(profile.fields)} fields, got {len(metadata.fields)} — "
        "likely truncated before the fields array (check max_tokens / prompt verbosity)."
    )
    assert metadata.compliance is not None
    assert quality.overall_score > 0

    # Every field's boolean properties must have actually coerced to bool
    # (a raw passthrough bug would leave a str here, and every downstream
    # exporter/UI comparison like `if f.is_pii:` would silently misbehave).
    for f in metadata.fields:
        assert isinstance(f.is_pii, bool)
        assert isinstance(f.is_key_field, bool)
    assert isinstance(metadata.compliance.gdpr_applicable, bool)
    assert isinstance(metadata.compliance.cross_border_transfer_restrictions, bool)

    # The actual export step that broke last time — run against real model
    # output, not a synthetic fixture, since that's what broke in production.
    assert export_csv(metadata)
    assert export_pdf(metadata)[:4] == b"%PDF"
    assert export_word(metadata)[:2] == b"PK"

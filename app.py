"""
Metadata Intelligence Agent — Streamlit Web Interface

Run:  streamlit run app.py
"""

import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

# ── Page config (must be first Streamlit call) ─────────────────────────────
st.set_page_config(
    page_title="Metadata Intelligence Agent",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"Get help": None, "Report a bug": None, "About": None},
)

sys.path.insert(0, str(Path(__file__).parent))

# ── Trial mode ───────────────────────────────────────────────────────────────
TRIAL_MODE = os.environ.get("TRIAL_MODE", "false").lower() == "true"
TRIAL_RUN_CAP = 3
TRIAL_MODEL = "claude-haiku-4-5-20251001"
GITHUB_URL = "https://github.com/basavarajshepur-lab/metadata-agent"

from src.agents import DataQualityAgent, LineageAgent, MetadataAgent
from src.config import AgentConfig
from src.exporters import export_csv, export_pdf, export_word
from src.extractors import extract
from src.schema import DatasetMetadata, QualityScore, SensitivityLevel

try:
    from src.memory.memory_store import get_run_history
    _MEMORY_OK = True
except Exception:
    _MEMORY_OK = False
    get_run_history = lambda limit=5: []  # noqa: E731

try:
    from src.connectors.google_auth import (
        credentials_file_exists,
        is_authenticated,
        start_auth_thread,
        revoke,
    )
    from src.connectors.gmail_connector import (
        download_attachment as gmail_download,
        list_emails_with_attachments,
    )
    from src.connectors.drive_connector import download_drive_file, list_drive_files
    _GOOGLE_OK = True
except ImportError:
    _GOOGLE_OK = False

# ── CSS ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* Hide streamlit chrome */
  #MainMenu, footer, header { visibility: hidden; }

  /* Page background */
  .stApp { background: #F8FAFC; }

  /* Agent header */
  .agent-header {
    background: linear-gradient(135deg, #1E3A5F 0%, #2D5282 100%);
    border-radius: 12px;
    padding: 1.5rem 2rem;
    margin-bottom: 1.5rem;
    color: white;
  }
  .agent-header h1 { color: white !important; margin: 0; font-size: 1.6rem; }
  .agent-header p  { color: #CBD5E1 !important; margin: 0.25rem 0 0; font-size: 0.9rem; }

  /* Sensitivity badges */
  .badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: white;
    text-transform: uppercase;
  }
  .badge-public       { background: #2D9E47; }
  .badge-internal     { background: #2E86AB; }
  .badge-confidential { background: #F18F01; }
  .badge-restricted   { background: #D13232; }
  .badge-secret       { background: #8B0000; }

  /* Stat cards */
  .stat-card {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    padding: 1.1rem 1.2rem;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }
  .stat-card .stat-value { font-size: 2rem; font-weight: 700; color: #1E3A5F; line-height: 1.1; }
  .stat-card .stat-label { font-size: 0.75rem; color: #64748B; margin-top: 2px; }

  /* Quality pill */
  .quality-pass { color: #2D9E47; font-weight: 700; }
  .quality-fail { color: #D13232; font-weight: 700; }

  /* Upload zone enhancement */
  .upload-card {
    background: white;
    border: 2px dashed #CBD5E1;
    border-radius: 12px;
    padding: 2.5rem;
    text-align: center;
  }

  /* Field table row styling */
  .pii-yes { color: #8B0000; font-weight: 600; }

  /* Section label */
  .section-label {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #64748B;
    margin-bottom: 0.5rem;
  }

  /* Download section */
  .download-section {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    padding: 1.2rem 1.5rem;
    margin-top: 1.5rem;
  }

  /* Metric overrides */
  [data-testid="metric-container"] {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 0.8rem 1rem;
  }
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────────────────────
SENSITIVITY_COLORS = {
    "public": "#2D9E47",
    "internal": "#2E86AB",
    "confidential": "#F18F01",
    "restricted": "#D13232",
    "secret": "#8B0000",
}

SAMPLE_FILES = {
    "Customer Accounts (CSV)": "samples/customer_accounts.csv",
    "Payment Transactions (JSON Schema)": "samples/transaction_schema.json",
    "Risk Positions (SQL DDL)": "samples/risk_positions.sql",
}

MODELS = {
    "Claude Sonnet 4.6 (Recommended)": "claude-sonnet-4-6",
    "Claude Haiku 4.5 (Faster / cheaper)": "claude-haiku-4-5-20251001",
    "Claude Opus 4.7 (Most capable)": "claude-opus-4-7",
}


# ── Helpers ────────────────────────────────────────────────────────────────
def badge(level: str) -> str:
    return f'<span class="badge badge-{level}">{level}</span>'


def quality_colour(score: float, passed: bool) -> str:
    cls = "quality-pass" if passed else "quality-fail"
    return f'<span class="{cls}">{score:.1f}/100 {"✓ PASS" if passed else "✗ FAIL"}</span>'


def _ext(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def _api_key_set() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and key != "your_api_key_here"


@st.cache_data(show_spinner=False)
def _extract_profile(file_bytes: bytes, filename: str):
    """Cache the extraction step — it doesn't call the API."""
    ext = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        return extract(tmp_path)
    finally:
        os.unlink(tmp_path)


def _staged_temp_path() -> str:
    """Write the currently staged file to a temp file and return its path.

    LineageAgent.run() and DataQualityAgent.run() take a file path (they call
    extract() themselves), unlike MetadataAgent.generate() which takes an
    already-extracted profile.
    """
    file_bytes = st.session_state["_staged_bytes"]
    filename = st.session_state["_staged_name"]
    ext = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        return tmp.name


# ── Session state init ──────────────────────────────────────────────────────
for key, default in {
    "metadata": None,
    "profile": None,
    "lineage": None,
    "quality_report": None,
    "error": None,
    "last_filename": None,
    "_staged_bytes": None,
    "_staged_name": None,
    "trial_runs_used": 0,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

trial_limit_reached = TRIAL_MODE and st.session_state["trial_runs_used"] >= TRIAL_RUN_CAP


def _consume_trial_run() -> None:
    if TRIAL_MODE:
        st.session_state["trial_runs_used"] += 1


# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Agent Settings")

    if TRIAL_MODE:
        st.caption(
            f"🔒 Trial mode — model locked to Haiku, {TRIAL_RUN_CAP} runs/session. "
            f"[Clone the repo]({GITHUB_URL}) to use your own API key and model choice."
        )
        selected_model = TRIAL_MODEL
    else:
        api_key_input = st.text_input(
            "Anthropic API Key",
            value=os.environ.get("ANTHROPIC_API_KEY", ""),
            type="password",
            help="Get your key at console.anthropic.com. You can also set ANTHROPIC_API_KEY in a .env file.",
        )
        if api_key_input:
            os.environ["ANTHROPIC_API_KEY"] = api_key_input

        model_label = st.selectbox("Model", list(MODELS.keys()), index=0)
        selected_model = MODELS[model_label]

    st.divider()

    if st.session_state.metadata:
        md = st.session_state.metadata
        qs = md.quality_score
        st.markdown("### Last Run")
        st.markdown(f"**{md.dataset_name}**")
        st.markdown(f"Domain: `{md.data_domain.value}`")
        st.markdown(
            f"Classification: {badge(md.classification.value)}",
            unsafe_allow_html=True,
        )
        st.markdown(f"Fields: **{len(md.fields)}**")
        pii = sum(1 for f in md.fields if f.is_pii)
        st.markdown(f"PII fields: **{pii}**")
        if qs:
            colour = "green" if qs.passed else "red"
            st.markdown(
                f"Quality: :{colour}[**{qs.overall_score:.0f}/100 {'PASS' if qs.passed else 'FAIL'}**]"
            )
        st.divider()
        if st.button("Clear results / New file", use_container_width=True):
            for k in [
                "metadata", "profile", "lineage", "quality_report",
                "last_filename", "error", "_staged_bytes", "_staged_name",
            ]:
                st.session_state[k] = None
            st.rerun()

    # ── Run history ────────────────────────────────────────────────────────
    if _MEMORY_OK:
        try:
            history = get_run_history(limit=6)
            if history:
                st.markdown("### Run History")
                for run in history:
                    qs = run.get("quality_score")
                    passed = run.get("quality_passed")
                    score_str = f"{qs:.0f}" if qs is not None else "—"
                    icon = "✅" if passed else ("❌" if passed is False else "—")
                    date_str = (run.get("created_at") or "")[:10]
                    st.markdown(
                        f"**{run['dataset_name']}**  \n"
                        f"`{run.get('data_domain','—')}` · {run.get('field_count',0)} fields "
                        f"· {icon} {score_str}  \n"
                        f"<small style='color:#94A3B8'>{date_str}</small>",
                        unsafe_allow_html=True,
                    )
                st.divider()
        except Exception:
            pass

    st.markdown("### About")
    st.markdown(
        """
Banking-grade metadata generation powered by Claude.

**Supported inputs**
- CSV datasets
- JSON Schema (draft-07 / 2020-12)
- SQL DDL (PostgreSQL, Oracle, SQL Server)

**Standards applied**
- BCBS 239 (risk data lineage)
- UK GDPR / GDPR (PII classification)
- DAMA-DMBOK (data stewardship)
- FCA retention rules
        """
    )


# ── Header ─────────────────────────────────────────────────────────────────
st.markdown("""
<div class="agent-header">
  <h1>🏦 Metadata Intelligence Agent</h1>
  <p>Generate banking-grade metadata from any dataset &mdash; BCBS 239 compliance,
  PII guardrails, and 5-dimension quality evals in seconds.</p>
</div>
""", unsafe_allow_html=True)

if TRIAL_MODE:
    st.info(
        f"🔒 **Live Trial** — sample data only, {TRIAL_RUN_CAP} agent runs per browser session. "
        f"[Clone the repo]({GITHUB_URL}) to bring your own files and API key.",
        icon="🔒",
    )
    if trial_limit_reached:
        st.warning(
            f"Trial limit reached ({TRIAL_RUN_CAP} runs used this session). "
            f"[Clone the repo]({GITHUB_URL}) to keep exploring with your own data."
        )


# ── Upload / sample section (shown when no metadata yet) ────────────────────
if st.session_state.metadata is None:

    # API key warning
    if not TRIAL_MODE and not _api_key_set():
        st.warning(
            "Set your **Anthropic API Key** in the sidebar to generate metadata. "
            "You can still upload a file to preview its structure first.",
            icon="🔑",
        )

    # ── Input source tabs ────────────────────────────────────────────────────
    tab_upload, tab_gmail, tab_drive = st.tabs([
        "📁 Upload File", "📧 From Gmail", "☁️ From Google Drive",
    ])

    # ── Tab 1: Upload / sample ──────────────────────────────────────────────
    with tab_upload:
        col_upload, col_sample = st.columns([3, 1], gap="large")

        with col_upload:
            if TRIAL_MODE:
                st.info("File upload is disabled in trial mode — pick a sample on the right.")
                uploaded = None
            else:
                st.markdown('<p class="section-label">Upload your dataset or schema</p>', unsafe_allow_html=True)
                uploaded = st.file_uploader(
                    "Drag and drop or browse",
                    type=["csv", "json", "sql", "ddl"],
                    label_visibility="collapsed",
                    help="CSV datasets, JSON Schemas, or SQL DDL files up to 50 MB",
                )
            if uploaded is not None:
                fb = uploaded.getvalue()
                fn = getattr(uploaded, "name", "upload")
                if fn != st.session_state["_staged_name"]:
                    st.session_state["profile"] = None
                    st.session_state["lineage"] = None
                    st.session_state["quality_report"] = None
                st.session_state["_staged_bytes"] = fb
                st.session_state["_staged_name"] = fn

        with col_sample:
            st.markdown('<p class="section-label">Or try a sample</p>', unsafe_allow_html=True)
            for label, path in SAMPLE_FILES.items():
                if st.button(label, use_container_width=True, key=f"sample_{label}"):
                    with open(path, "rb") as f:
                        data = f.read()
                    new_name = Path(path).name
                    if new_name != st.session_state["_staged_name"]:
                        st.session_state["profile"] = None
                        st.session_state["lineage"] = None
                        st.session_state["quality_report"] = None
                    st.session_state["_staged_bytes"] = data
                    st.session_state["_staged_name"] = new_name

    # ── Tab 2: Gmail ────────────────────────────────────────────────────────
    with tab_gmail:
        if TRIAL_MODE:
            st.info(
                f"Gmail connection is disabled in trial mode (requires uploading Google OAuth "
                f"credentials). [Clone the repo]({GITHUB_URL}) to connect your own Gmail account."
            )
        elif not _GOOGLE_OK:
            st.info(
                "Google API libraries not installed. Run:\n\n"
                "```\npip install google-api-python-client google-auth-httplib2 google-auth-oauthlib\n```"
            )
        elif not credentials_file_exists():
            st.markdown("#### Connect Gmail")
            st.markdown(
                "To access Gmail attachments you need a Google Cloud credentials file.\n\n"
                "1. Go to [console.cloud.google.com](https://console.cloud.google.com)\n"
                "2. Create a project → Enable **Gmail API** and **Drive API**\n"
                "3. Create **OAuth 2.0 credentials** (type: *Desktop App*)\n"
                "4. Download `credentials.json` and upload it below"
            )
            cred_upload = st.file_uploader(
                "Upload credentials.json", type="json", key="cred_upload"
            )
            if cred_upload is not None:
                Path("credentials.json").write_bytes(cred_upload.getvalue())
                st.success("credentials.json saved. Click **Authorize** to connect.")
                st.rerun()
        elif not is_authenticated():
            st.markdown("#### Authorize Google Account")
            st.markdown(
                "Click the button below. Your browser will open for Google authorization. "
                "After approving, click **Done**."
            )
            if st.button("🔑 Authorize Gmail & Drive", key="btn_auth_google"):
                status = start_auth_thread(port=8502)
                st.session_state["_google_auth"] = status
            if "_google_auth" in st.session_state:
                status = st.session_state["_google_auth"]
                if status.get("done"):
                    if status.get("error"):
                        st.error(f"Authorization failed: {status['error']}")
                    else:
                        del st.session_state["_google_auth"]
                        st.success("Google account connected!")
                        st.rerun()
                else:
                    st.info("Complete authorization in your browser, then click **Done**.")
                    if st.button("Done / Refresh", key="btn_google_done"):
                        st.rerun()
        else:
            # Connected — list emails with attachments
            gcol1, gcol2 = st.columns([4, 1])
            gcol1.markdown("#### Gmail — Emails with attachments")
            if gcol2.button("Disconnect", key="btn_google_revoke"):
                revoke()
                st.rerun()

            with st.spinner("Loading emails..."):
                try:
                    emails = list_emails_with_attachments(max_results=20)
                except Exception as exc:
                    st.error(f"Could not load emails: {exc}")
                    emails = []

            if not emails:
                st.info("No emails with CSV / JSON / SQL attachments found.")
            else:
                st.markdown(f"Found **{len(emails)}** emails with supported attachments.")
                for email in emails:
                    label = f"📧 {email['subject'][:60]}  —  {email['sender'][:40]}"
                    with st.expander(label, expanded=False):
                        st.markdown(f"**Date:** {email['date']}")
                        for att in email["attachments"]:
                            size_kb = att["size_bytes"] // 1024
                            btn_label = f"⬇ Process  {att['filename']}  ({size_kb} KB)"
                            if st.button(btn_label, key=f"gmail_{email['message_id']}_{att['filename']}"):
                                with st.spinner(f"Downloading {att['filename']}…"):
                                    try:
                                        tmp = gmail_download(
                                            email["message_id"],
                                            att["attachment_id"],
                                            att["filename"],
                                        )
                                        with open(tmp, "rb") as fh:
                                            raw = fh.read()
                                        os.unlink(tmp)
                                        if att["filename"] != st.session_state["_staged_name"]:
                                            st.session_state["profile"] = None
                                            st.session_state["lineage"] = None
                                            st.session_state["quality_report"] = None
                                        st.session_state["_staged_bytes"] = raw
                                        st.session_state["_staged_name"] = att["filename"]
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Download failed: {exc}")

    # ── Tab 3: Google Drive ─────────────────────────────────────────────────
    with tab_drive:
        if TRIAL_MODE:
            st.info(
                f"Google Drive connection is disabled in trial mode (requires uploading Google "
                f"OAuth credentials). [Clone the repo]({GITHUB_URL}) to connect your own Drive."
            )
        elif not _GOOGLE_OK:
            st.info(
                "Google API libraries not installed. Run:\n\n"
                "```\npip install google-api-python-client google-auth-httplib2 google-auth-oauthlib\n```"
            )
        elif not credentials_file_exists():
            st.info("Upload `credentials.json` in the Gmail tab first.")
        elif not is_authenticated():
            st.info("Authorize your Google account in the Gmail tab first.")
        else:
            dcol1, dcol2 = st.columns([4, 1])
            dcol1.markdown("#### Google Drive — Supported files")
            search_term = dcol2.text_input("Search", placeholder="filter by name", key="drive_search")

            with st.spinner("Loading Drive files..."):
                try:
                    drive_files = list_drive_files(max_results=50)
                except Exception as exc:
                    st.error(f"Could not load Drive files: {exc}")
                    drive_files = []

            if search_term:
                drive_files = [f for f in drive_files if search_term.lower() in f["name"].lower()]

            if not drive_files:
                st.info("No CSV / JSON / SQL files found in your Drive.")
            else:
                st.markdown(f"Found **{len(drive_files)}** files.")
                for df_item in drive_files:
                    size_kb = df_item.get("size_bytes", 0) // 1024
                    mod = (df_item.get("modified") or "")[:10]
                    btn_lbl = f"⬇ {df_item['name']}  ({size_kb} KB · {mod})"
                    if st.button(btn_lbl, key=f"drive_{df_item['id']}"):
                        with st.spinner(f"Downloading {df_item['name']}…"):
                            try:
                                tmp = download_drive_file(df_item["id"], df_item["name"])
                                with open(tmp, "rb") as fh:
                                    raw = fh.read()
                                os.unlink(tmp)
                                if df_item["name"] != st.session_state["_staged_name"]:
                                    st.session_state["profile"] = None
                                    st.session_state["lineage"] = None
                                    st.session_state["quality_report"] = None
                                st.session_state["_staged_bytes"] = raw
                                st.session_state["_staged_name"] = df_item["name"]
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Download failed: {exc}")

    # ── Read staged file (set by any of the three tabs above) ──────────────
    file_bytes = st.session_state["_staged_bytes"]
    filename = st.session_state["_staged_name"]

    # ── Extract profile if not already done for this file ───────────────────
    if file_bytes and st.session_state["profile"] is None:
        with st.spinner("Profiling dataset..."):
            try:
                profile = _extract_profile(file_bytes, filename)
                st.session_state.profile = profile
            except Exception as e:
                st.error(f"Could not read file: {e}")
                st.stop()

    # ── Show preview and Generate button whenever a profile exists ──────────
    current_profile = st.session_state.profile
    if current_profile:
        st.success(
            f"**{current_profile.dataset_name}** — {len(current_profile.fields)} fields detected "
            f"{'· ' + str(current_profile.row_count) + ' rows' if current_profile.row_count else ''}"
        )

        pii_count = sum(1 for f in current_profile.fields if f.is_potential_pii)
        if pii_count:
            st.info(
                f"**{pii_count} potential PII field(s)** detected by column-name heuristic "
                f"(values masked). AI will confirm and classify each one.",
                icon="🔍",
            )

        preview_df = pd.DataFrame([
            {
                "Field": f.name,
                "Inferred Type": f.inferred_type,
                "Null Rate": f"{f.null_rate:.0%}" if f.total_count > 0 else "—",
                "Unique Values": f.unique_count if f.unique_count is not None else "—",
                "Potential PII": "🔴" if f.is_potential_pii else "",
            }
            for f in current_profile.fields
        ])
        st.dataframe(
            preview_df,
            hide_index=True,
            use_container_width=True,
            height=min(400, 40 + len(current_profile.fields) * 36),
        )

        st.divider()

        btn_disabled = (not _api_key_set()) or trial_limit_reached
        btn_reason = " (trial limit reached)" if trial_limit_reached else (" (API key required)" if btn_disabled else "")
        if st.button(
            "Generate Metadata" + btn_reason,
            type="primary",
            use_container_width=True,
            disabled=btn_disabled,
        ):
            _consume_trial_run()
            with st.spinner("Claude is analysing the dataset and generating metadata... (30–90 seconds)"):
                try:
                    config = AgentConfig(model=selected_model)
                    agent = MetadataAgent(config)
                    # Always read from session state — local variables don't survive reruns
                    metadata, _ = agent.generate(st.session_state.profile)
                    st.session_state.metadata = metadata
                    st.session_state.last_filename = st.session_state["_staged_name"]
                    st.session_state.error = None
                    st.rerun()
                except Exception as e:
                    st.session_state.error = str(e)

        if st.session_state.error:
            st.error(f"Agent error: {st.session_state.error}")


# ── Results section ─────────────────────────────────────────────────────────
else:
    md: DatasetMetadata = st.session_state.metadata
    qs: QualityScore | None = md.quality_score
    pii_fields = md.pii_fields

    # ── Banner ─────────────────────────────────────────────────────────────
    b1, b2, b3, b4, b5 = st.columns(5)
    with b1:
        st.markdown(
            f'<div class="stat-card"><div class="stat-value">{len(md.fields)}</div>'
            f'<div class="stat-label">Total Fields</div></div>',
            unsafe_allow_html=True,
        )
    with b2:
        st.markdown(
            f'<div class="stat-card"><div class="stat-value">{len(pii_fields)}</div>'
            f'<div class="stat-label">PII Fields</div></div>',
            unsafe_allow_html=True,
        )
    with b3:
        restricted = sum(
            1 for f in md.fields if f.sensitivity_level.rank >= SensitivityLevel.RESTRICTED.rank
        )
        st.markdown(
            f'<div class="stat-card"><div class="stat-value">{restricted}</div>'
            f'<div class="stat-label">Restricted / Above</div></div>',
            unsafe_allow_html=True,
        )
    with b4:
        guardrails = len(qs.guardrails_applied) if qs else 0
        st.markdown(
            f'<div class="stat-card"><div class="stat-value">{guardrails}</div>'
            f'<div class="stat-label">Guardrails Applied</div></div>',
            unsafe_allow_html=True,
        )
    with b5:
        if qs:
            colour = "#2D9E47" if qs.passed else "#D13232"
            label = "PASS" if qs.passed else "FAIL"
            st.markdown(
                f'<div class="stat-card">'
                f'<div class="stat-value" style="color:{colour}">{qs.overall_score:.0f}</div>'
                f'<div class="stat-label">Quality Score ({label})</div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="stat-card"><div class="stat-value">—</div>'
                '<div class="stat-label">Quality Score</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown(
        f"<br><b>{md.dataset_name}</b> &nbsp;·&nbsp; "
        f"Domain: <code>{md.data_domain.value}</code> &nbsp;·&nbsp; "
        f"Classification: {badge(md.classification.value)} &nbsp;·&nbsp; "
        f"v{md.version}",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # ── Tabs ───────────────────────────────────────────────────────────────
    tab_overview, tab_fields, tab_quality, tab_compliance, tab_lineage, tab_dataquality, tab_raw = st.tabs([
        "Overview", "Fields", "Quality Report", "Compliance", "Lineage", "Data Quality (Full)", "Raw YAML",
    ])

    # ──────────────────── OVERVIEW tab ────────────────────────────────────
    with tab_overview:
        c1, c2 = st.columns([3, 2], gap="large")

        with c1:
            st.markdown("#### Dataset Description")
            st.markdown(md.description)

            st.markdown("#### Business Context")
            st.markdown(md.business_context)

            st.markdown("#### Usage Guidance")
            st.markdown(md.usage_guidance)

            if md.known_limitations:
                st.markdown("#### Known Limitations")
                st.warning(md.known_limitations)

        with c2:
            st.markdown("#### Dataset Details")
            details = {
                "Data Domain": md.data_domain.value.title(),
                "Sub-Domain": md.sub_domain or "—",
                "Classification": md.classification.value.upper(),
                "Classification Rationale": md.data_classification_rationale,
                "Source System": md.source_system or "—",
                "Data Steward": md.data_steward or "—",
                "Data Owner": md.data_owner or "—",
                "Schema Version": md.schema_version,
                "Generated By": md.generated_by,
            }
            for k, v in details.items():
                col_k, col_v = st.columns([2, 3])
                col_k.markdown(f"**{k}**")
                col_v.markdown(str(v))

            if md.related_datasets:
                st.markdown("**Related Datasets**")
                for ds in md.related_datasets:
                    st.markdown(f"- `{ds}`")

            if pii_fields:
                st.markdown("#### PII Summary")
                pii_by_type: dict = {}
                for f in pii_fields:
                    t = f.pii_type.value if f.pii_type else "other"
                    pii_by_type.setdefault(t, []).append(f.name)
                for pii_type, names in sorted(pii_by_type.items()):
                    st.markdown(f"**{pii_type}**: {', '.join(f'`{n}`' for n in names)}")

    # ──────────────────── FIELDS tab ──────────────────────────────────────
    with tab_fields:
        # Filter controls
        fc1, fc2, fc3 = st.columns(3)
        filter_pii = fc1.selectbox("Filter PII", ["All", "PII only", "Non-PII only"], index=0)
        filter_sens = fc2.selectbox(
            "Filter Sensitivity",
            ["All", "public", "internal", "confidential", "restricted", "secret"],
        )
        search = fc3.text_input("Search field name", placeholder="e.g. account")

        filtered = md.fields
        if filter_pii == "PII only":
            filtered = [f for f in filtered if f.is_pii]
        elif filter_pii == "Non-PII only":
            filtered = [f for f in filtered if not f.is_pii]
        if filter_sens != "All":
            filtered = [f for f in filtered if f.sensitivity_level.value == filter_sens]
        if search:
            filtered = [f for f in filtered if search.lower() in f.name.lower()]

        st.markdown(f"Showing **{len(filtered)}** of **{len(md.fields)}** fields")

        # Summary table
        table_rows = []
        for f in filtered:
            table_rows.append({
                "Field": f.name,
                "Type": f.data_type.value,
                "PII": f.pii_type.value if f.is_pii and f.pii_type else ("Yes" if f.is_pii else ""),
                "Sensitivity": f.sensitivity_level.value.upper(),
                "Key": "✓" if f.is_key_field else "",
                "Nullable": "Yes" if f.constraints.nullable else "No",
                "Description": f.description[:100] + ("…" if len(f.description) > 100 else ""),
                "Usage Guidance": f.usage_guidance[:80] + ("…" if len(f.usage_guidance) > 80 else ""),
            })

        df = pd.DataFrame(table_rows)
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            height=min(600, 60 + len(filtered) * 36),
            column_config={
                "Sensitivity": st.column_config.TextColumn("Sensitivity", width="small"),
                "Key": st.column_config.TextColumn("Key", width="small"),
                "Nullable": st.column_config.TextColumn("Nullable", width="small"),
                "Description": st.column_config.TextColumn("Description", width="large"),
            },
        )

        st.divider()
        st.markdown("#### Detailed Field View")
        field_names = [f.name for f in md.fields]
        selected_field_name = st.selectbox(
            "Select a field for full detail",
            options=field_names,
            index=0,
        ) if field_names else None
        selected_field = next((f for f in md.fields if f.name == selected_field_name), None)

        if not selected_field:
            st.info("No fields available to display.")
            st.stop()

        fd1, fd2 = st.columns(2)
        with fd1:
            st.markdown(f"**{selected_field.display_name}**")
            st.markdown(f"*{selected_field.description}*")
            st.markdown("")
            for label, val in [
                ("Data Type", selected_field.data_type.value),
                ("Format", selected_field.format or "—"),
                ("Is Key Field", "Yes" if selected_field.is_key_field else "No"),
                ("Sensitivity", selected_field.sensitivity_level.value.upper()),
                ("Nullable", "Yes" if selected_field.constraints.nullable else "No"),
                ("Unique", "Yes" if selected_field.constraints.unique else "No"),
                ("Pattern", selected_field.constraints.pattern or "—"),
                ("Foreign Key Ref", selected_field.constraints.foreign_key_ref or "—"),
            ]:
                r1, r2 = st.columns([1, 2])
                r1.markdown(f"**{label}**")
                r2.markdown(str(val))

            if selected_field.is_pii:
                st.markdown(f"**PII Type:** `{selected_field.pii_type.value if selected_field.pii_type else 'flagged'}`")

        with fd2:
            st.markdown("**Business Context**")
            st.markdown(selected_field.business_context or "—")
            st.markdown("**Usage Guidance**")
            st.info(selected_field.usage_guidance or "—")
            if selected_field.business_rules:
                st.markdown("**Business Rules**")
                st.markdown(selected_field.business_rules)
            if selected_field.data_lineage:
                st.markdown("**Data Lineage**")
                st.markdown(selected_field.data_lineage)
            if selected_field.quality_notes:
                st.markdown("**Quality Notes**")
                st.warning(selected_field.quality_notes)
            if selected_field.tags:
                st.markdown("**Tags**")
                st.markdown(" ".join(f"`{t}`" for t in selected_field.tags))
            if selected_field.guardrail_applied:
                st.markdown("**Guardrail Applied**")
                st.warning(selected_field.guardrail_applied)

    # ──────────────────── QUALITY REPORT tab ──────────────────────────────
    with tab_quality:
        if not qs:
            st.info("No quality score available.")
        else:
            # Overall score
            overall_color = "green" if qs.passed else "red"
            status_label = "PASSED" if qs.passed else "FAILED"
            st.markdown(
                f"### Overall Quality Score: "
                f":{overall_color}[**{qs.overall_score:.1f} / 100 — {status_label}**]"
            )
            st.progress(int(qs.overall_score))
            st.markdown("")

            # Dimension breakdown
            st.markdown("#### Dimension Scores")
            dims = [
                ("Completeness", qs.completeness, "30%",
                 "All required metadata fields populated with meaningful content"),
                ("PII Detection", qs.pii_detection, "25%",
                 "AI PII flags vs column name heuristics and extractor detection"),
                ("Type Consistency", qs.type_consistency, "20%",
                 "Declared data types match what was inferred from the data"),
                ("Banking Standards (BCBS 239)", qs.banking_standards, "15%",
                 "Data lineage, GDPR compliance fields, key field identification"),
                ("Sensitivity Consistency", qs.sensitivity_consistency, "10%",
                 "PII sensitivity floors and classification internal consistency"),
            ]
            for name, dim, weight, help_text in dims:
                col_label, col_bar, col_score, col_status = st.columns([3, 4, 1.5, 1.5])
                col_label.markdown(f"**{name}** `{weight}`")
                col_label.markdown(f"<small>{help_text}</small>", unsafe_allow_html=True)
                bar_color = "normal" if dim.passed else "off"
                col_bar.progress(int(dim.score), text="")
                col_score.markdown(f"**{dim.score:.1f}**")
                if dim.passed:
                    col_status.success("PASS")
                else:
                    col_status.error("FAIL")

                if dim.issues:
                    for issue in dim.issues:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;🔴 {issue}")
                if dim.warnings:
                    for warning in dim.warnings[:3]:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;🟡 {warning}")
                st.markdown("")

            # Guardrails
            if qs.guardrails_applied:
                st.divider()
                st.markdown(f"#### Guardrails Applied ({len(qs.guardrails_applied)})")
                for msg in qs.guardrails_applied:
                    st.warning(msg, icon="⚑")

    # ──────────────────── COMPLIANCE tab ──────────────────────────────────
    with tab_compliance:
        c = md.compliance
        cl1, cl2 = st.columns(2)

        with cl1:
            st.markdown("#### GDPR / UK GDPR")
            flags = [
                ("GDPR Applicable", c.gdpr_applicable),
                ("UK GDPR Applicable", c.uk_gdpr_applicable),
                ("Right to Erasure Applicable", c.right_to_erasure_applicable),
                ("Consent Required", c.consent_required),
                ("Cross-Border Transfer Restrictions", c.cross_border_transfer_restrictions),
            ]
            for label, val in flags:
                icon = "🟡" if val else "⚪"
                st.markdown(f"{icon} **{label}:** {'Yes' if val else 'No'}")

            st.markdown("")
            st.markdown("#### Data Governance")
            for label, val in [
                ("Lawful Basis", c.lawful_basis or "Not specified"),
                ("Retention Period", c.retention_period or "Not specified"),
                ("Data Residency", c.data_residency_requirements or "Not specified"),
            ]:
                k, v = st.columns([2, 3])
                k.markdown(f"**{label}**")
                v.markdown(val)

        with cl2:
            st.markdown("#### Regulatory Frameworks")
            if c.regulatory_frameworks:
                for rf in c.regulatory_frameworks:
                    st.markdown(f"✓ **{rf.value}**")
            else:
                st.markdown("*None specified*")

            if pii_fields:
                st.markdown("")
                st.markdown("#### PII Field Register")
                for f in sorted(pii_fields, key=lambda x: x.sensitivity_level.rank, reverse=True):
                    sens_col = SENSITIVITY_COLORS.get(f.sensitivity_level.value, "#64748B")
                    st.markdown(
                        f"<span style='color:{sens_col}; font-weight:700;'>"
                        f"● {f.sensitivity_level.value.upper()}</span> &nbsp;"
                        f"`{f.name}` — {f.pii_type.value if f.pii_type else 'PII'}",
                        unsafe_allow_html=True,
                    )

    # ──────────────────── LINEAGE tab ─────────────────────────────────────
    with tab_lineage:
        staged_name = st.session_state["_staged_name"] or ""
        if not staged_name.lower().endswith((".sql", ".ddl")):
            st.info(
                "The Lineage Agent parses SQL/DDL and maps field-level lineage against "
                "BCBS 239 Principle 2. The current file isn't SQL/DDL, so lineage isn't "
                "applicable — try the **Risk Positions (SQL DDL)** sample."
            )
        else:
            lineage = st.session_state.get("lineage")
            if lineage is None:
                lineage_disabled = (not _api_key_set()) or trial_limit_reached
                lineage_reason = " (trial limit reached)" if trial_limit_reached else (" (API key required)" if lineage_disabled else "")
                if st.button(
                    "Run Lineage Analysis" + lineage_reason,
                    type="primary",
                    disabled=lineage_disabled,
                    key="btn_run_lineage",
                ):
                    _consume_trial_run()
                    with st.spinner("Claude is parsing SQL and mapping field-level lineage..."):
                        tmp_path = _staged_temp_path()
                        try:
                            agent = LineageAgent(AgentConfig(model=selected_model))
                            st.session_state["lineage"] = agent.run(tmp_path)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Lineage agent error: {e}")
                        finally:
                            os.unlink(tmp_path)
            else:
                bcbs_label = (
                    "✅ BCBS 239 compliant" if lineage.bcbs_239_compliant
                    else "⚠️ Partially compliant"
                )
                st.markdown(f"#### {bcbs_label}")
                if lineage.bcbs_notes:
                    st.markdown(lineage.bcbs_notes)

                l1, l2, l3 = st.columns(3)
                l1.metric("Fields mapped", len(lineage.field_lineages))
                l2.metric("Source tables", len(lineage.source_tables))
                l3.metric("Unresolved fields", len(lineage.unresolved_fields))

                if lineage.source_tables:
                    st.markdown("**Source tables:** " + ", ".join(f"`{t}`" for t in lineage.source_tables))

                st.divider()
                lineage_rows = [
                    {
                        "Target Field": fl.target_field,
                        "Type": fl.lineage_type,
                        "Confidence": fl.confidence,
                        "Sources": ", ".join(f"{s.table}.{s.column}" for s in fl.source_fields) or "—",
                        "Transformation": fl.transformation or "—",
                    }
                    for fl in lineage.field_lineages
                ]
                st.dataframe(pd.DataFrame(lineage_rows), hide_index=True, use_container_width=True)

                if lineage.unresolved_fields:
                    st.warning("Unresolved fields: " + ", ".join(f"`{f}`" for f in lineage.unresolved_fields))

                if st.button("Re-run", key="btn_rerun_lineage"):
                    st.session_state["lineage"] = None
                    st.rerun()

    # ──────────────────── DATA QUALITY (FULL) tab ─────────────────────────
    with tab_dataquality:
        report = st.session_state.get("quality_report")
        if report is None:
            dq_disabled = (not _api_key_set()) or trial_limit_reached
            dq_reason = " (trial limit reached)" if trial_limit_reached else (" (API key required)" if dq_disabled else "")
            if st.button(
                "Run Full Data Quality Analysis" + dq_reason,
                type="primary",
                disabled=dq_disabled,
                key="btn_run_dq",
            ):
                _consume_trial_run()
                with st.spinner("Claude is profiling the dataset against DAMA-DMBOK dimensions..."):
                    tmp_path = _staged_temp_path()
                    try:
                        agent = DataQualityAgent(AgentConfig(model=selected_model))
                        st.session_state["quality_report"] = agent.run(tmp_path)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Data Quality agent error: {e}")
                    finally:
                        os.unlink(tmp_path)
        else:
            dq_colour = "green" if report.passed else "red"
            dq_status = "PASSED" if report.passed else "FAILED"
            st.markdown(f"### Overall Score: :{dq_colour}[**{report.overall_score:.1f} / 100 — {dq_status}**]")
            st.progress(min(100, int(report.overall_score)))

            st.markdown("#### DAMA-DMBOK Dimensions")
            for dim_name, dim in report.dimensions.items():
                dcol_label, dcol_bar, dcol_score = st.columns([2, 4, 1])
                dcol_label.markdown(f"**{dim_name.title()}**")
                dcol_bar.progress(min(100, int(dim.score)))
                dcol_score.markdown(f"**{dim.score:.0f}**")
                for issue in dim.issues:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;🔴 {issue}")
                if dim.notes:
                    st.caption(dim.notes)

            if report.critical_issues:
                st.divider()
                st.markdown("#### Critical Issues")
                for issue in report.critical_issues:
                    st.error(issue)

            if report.recommendations:
                st.markdown("#### Recommendations")
                for rec in report.recommendations:
                    st.markdown(f"- {rec}")

            total_expectations = sum(len(fq.expectations) for fq in report.field_quality)
            st.divider()
            st.markdown(
                f"**{total_expectations}** Great Expectations rules generated "
                f"across **{len(report.field_quality)}** fields"
            )
            with st.expander("Field-level detail"):
                dq_rows = [
                    {
                        "Field": fq.field_name,
                        "Completeness": f"{fq.completeness_score:.0f}",
                        "Issues": "; ".join(fq.issues) or "—",
                        "GE Rules": len(fq.expectations),
                    }
                    for fq in report.field_quality
                ]
                st.dataframe(pd.DataFrame(dq_rows), hide_index=True, use_container_width=True)

            dq_base_name = report.dataset_name.lower().replace(" ", "_")
            st.download_button(
                "⬇ Download Quality Report (JSON)",
                data=json.dumps(json.loads(report.model_dump_json()), indent=2, default=str).encode("utf-8"),
                file_name=f"{dq_base_name}_quality_report.json",
                mime="application/json",
            )

            if st.button("Re-run", key="btn_rerun_dq"):
                st.session_state["quality_report"] = None
                st.rerun()

    # ──────────────────── RAW YAML tab ────────────────────────────────────
    with tab_raw:
        import json as _json
        raw_dict = _json.loads(md.model_dump_json())
        yaml_str = yaml.dump(raw_dict, default_flow_style=False, allow_unicode=True, sort_keys=False)
        st.code(yaml_str, language="yaml", line_numbers=True)

    # ── Download section ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Download Metadata")
    st.markdown(
        "Export the curated metadata for data catalogue ingestion, stakeholder review, or governance sign-off.",
    )

    dl1, dl2, dl3 = st.columns(3)

    base_name = md.dataset_name.lower().replace(" ", "_")

    with dl1:
        try:
            csv_bytes = export_csv(md)
            st.download_button(
                label="⬇ Download CSV",
                data=csv_bytes,
                file_name=f"{base_name}_metadata.csv",
                mime="text/csv",
                use_container_width=True,
                help="Flat field inventory table. Load into Excel or a data catalogue API.",
            )
            st.caption("Field inventory table · best for Excel / catalogue ingestion")
        except Exception as e:
            st.error(f"CSV export error: {e}")

    with dl2:
        try:
            pdf_bytes = export_pdf(md)
            st.download_button(
                label="⬇ Download PDF",
                data=pdf_bytes,
                file_name=f"{base_name}_metadata.pdf",
                mime="application/pdf",
                use_container_width=True,
                help="Structured PDF document for stakeholder review and data governance sign-off.",
            )
            st.caption("Structured document · best for stakeholder review")
        except Exception as e:
            st.error(f"PDF export error: {e}")

    with dl3:
        try:
            word_bytes = export_word(md)
            st.download_button(
                label="⬇ Download Word",
                data=word_bytes,
                file_name=f"{base_name}_metadata.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                help="Editable Word document. Data stewards can annotate and approve.",
            )
            st.caption("Editable document · best for data steward sign-off")
        except Exception as e:
            st.error(f"Word export error: {e}")

    # Also offer YAML download
    import json as _json2
    yaml_download = yaml.dump(
        _json2.loads(md.model_dump_json()),
        default_flow_style=False, allow_unicode=True, sort_keys=False
    ).encode("utf-8")
    st.download_button(
        label="⬇ Download YAML (machine-readable)",
        data=yaml_download,
        file_name=f"{base_name}_metadata.yaml",
        mime="text/yaml",
        help="YAML output for data catalogue API ingestion (Collibra, Atlan, DataHub).",
    )

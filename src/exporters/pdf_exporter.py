"""PDF exporter — structured data catalogue document using fpdf2."""

import io
from datetime import datetime
from typing import Tuple

from fpdf import FPDF
from fpdf.enums import WrapMode, XPos, YPos

from ..schema import DatasetMetadata, SensitivityLevel

# Colour palette
NAVY = (30, 58, 95)
WHITE = (255, 255, 255)
LIGHT_BLUE = (240, 247, 252)
MID_GRAY = (100, 116, 139)
LIGHT_GRAY = (248, 250, 252)
BORDER_GRAY = (226, 232, 240)
BLACK = (15, 23, 42)

SENSITIVITY_RGB: dict[str, Tuple[int, int, int]] = {
    "public": (45, 158, 71),
    "internal": (46, 134, 171),
    "confidential": (241, 143, 1),
    "restricted": (209, 50, 50),
    "secret": (139, 0, 0),
}


def _trunc(text: str, n: int) -> str:
    return text[:n] + "..." if len(text) > n else text


# fpdf2's core Helvetica font only supports latin-1. Claude's prose routinely uses
# em/en dashes, curly quotes, and ellipses that fall outside that range and would
# otherwise raise FPDFUnicodeEncodingException / "not enough horizontal space".
_PDF_CHAR_MAP = {
    "–": "-", "—": "-",       # en dash, em dash
    "‘": "'", "’": "'",       # curly single quotes
    "“": '"', "”": '"',       # curly double quotes
    "…": "...",                     # ellipsis
    " ": " ",                       # non-breaking space
    "•": "-",                       # bullet
    "−": "-",                       # minus sign
}


def _sanitize_pdf_text(text):
    if not isinstance(text, str):
        return text
    for uni, ascii_ in _PDF_CHAR_MAP.items():
        text = text.replace(uni, ascii_)
    # Safety net for anything else outside latin-1 (accented chars beyond cp1252, etc.)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class MetadataPDF(FPDF):
    def __init__(self, dataset_name: str, classification: str):
        super().__init__(orientation="P", format="A4")
        self._dataset_name = dataset_name
        self._classification = classification.upper()
        self.set_margins(15, 28, 15)
        self.set_auto_page_break(True, margin=20)

    def cell(self, w=None, h=None, text="", *args, **kwargs):
        return super().cell(w, h, _sanitize_pdf_text(text), *args, **kwargs)

    def multi_cell(self, w, h=None, text="", *args, **kwargs):
        # Model-generated text can contain unbroken long tokens (URLs, table.column
        # refs) that WORD wrap mode can't break, raising "not enough horizontal
        # space to render a single character". CHAR mode always finds somewhere to
        # break instead of raising.
        kwargs.setdefault("wrapmode", WrapMode.CHAR)
        return super().multi_cell(w, h, _sanitize_pdf_text(text), *args, **kwargs)

    def header(self):
        self.set_fill_color(*NAVY)
        self.rect(0, 0, 210, 20, style="F")
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*WHITE)
        self.set_xy(15, 5)
        self.cell(100, 10, "Metadata Intelligence Agent", align="L")
        self.set_font("Helvetica", "", 8)
        self.set_xy(110, 5)
        self.cell(85, 10, _trunc(self._dataset_name, 45), align="R")

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*MID_GRAY)
        self.cell(0, 8,
                  f"Page {self.page_no()} | Generated {datetime.now().strftime('%Y-%m-%d')} | {self._classification}",
                  align="C")

    def section_title(self, title: str):
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 7, f"  {title}", fill=True, ln=True)
        self.ln(2)

    def kv_row(self, key: str, value: str, shade: bool = False):
        self.set_fill_color(*(LIGHT_GRAY if shade else WHITE))
        self.set_text_color(*BLACK)
        self.set_font("Helvetica", "B", 8)
        self.cell(45, 6, key, fill=True, border="B", new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font("Helvetica", "", 8)
        # multi_cell's default new_x=RIGHT leaves the cursor at the end of the
        # wrapped text rather than back at the left margin. Left unfixed, that
        # drift compounds across successive kv_row calls until the "remaining
        # width" for some later row's value hits zero or goes negative — fpdf2
        # then either raises "not enough horizontal space" (WORD wrap) or hangs
        # forever (CHAR wrap). Always start the next row at the left margin.
        self.multi_cell(0, 6, value or "—", fill=True, border="B", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def _sensitivity_badge(self, level: str):
        rgb = SENSITIVITY_RGB.get(level.lower(), MID_GRAY if isinstance(MID_GRAY, tuple) else (100, 116, 139))
        self.set_fill_color(*rgb)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 7)
        self.cell(25, 5, level.upper(), fill=True, align="C")
        self.set_text_color(*BLACK)


def _cover_page(pdf: MetadataPDF, metadata: DatasetMetadata):
    pdf.add_page()
    pdf.ln(8)

    # Classification banner
    sens_rgb = SENSITIVITY_RGB.get(metadata.classification.value, (100, 116, 139))
    pdf.set_fill_color(*sens_rgb)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 7, f"  DATA CLASSIFICATION: {metadata.classification.value.upper()}", fill=True, ln=True)
    pdf.ln(4)

    # Title
    pdf.set_text_color(*NAVY)
    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(0, 9, metadata.dataset_name, align="L")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MID_GRAY)
    pdf.cell(0, 6, f"Domain: {metadata.data_domain.value.title()}   |   Version: {metadata.version}", ln=True)
    pdf.ln(6)

    # Description card
    pdf.section_title("DATASET DESCRIPTION")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*BLACK)
    pdf.multi_cell(0, 5, metadata.description)
    pdf.ln(3)
    pdf.section_title("BUSINESS CONTEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, metadata.business_context)
    pdf.ln(3)
    pdf.section_title("USAGE GUIDANCE")
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, metadata.usage_guidance)
    pdf.ln(4)

    # Stats row
    pii_count = sum(1 for f in metadata.fields if f.is_pii)
    restricted = sum(1 for f in metadata.fields if f.sensitivity_level.rank >= SensitivityLevel.RESTRICTED.rank)
    qs = metadata.quality_score

    pdf.set_fill_color(*LIGHT_BLUE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*NAVY)
    col_w = 55
    for label, val in [
        ("Total Fields", str(len(metadata.fields))),
        ("PII Fields", str(pii_count)),
        ("Restricted / Above", str(restricted)),
        ("Quality Score", f"{qs.overall_score:.0f}/100 {'PASS' if qs.passed else 'FAIL'}" if qs else "—"),
    ]:
        pdf.cell(col_w, 10, f"{label}\n{val}", fill=True, border=1, align="C")
    pdf.ln(12)


def _compliance_section(pdf: MetadataPDF, metadata: DatasetMetadata):
    pdf.section_title("COMPLIANCE & REGULATORY")
    c = metadata.compliance
    shade = False
    for key, val in [
        ("GDPR Applicable", "Yes" if c.gdpr_applicable else "No"),
        ("UK GDPR Applicable", "Yes" if c.uk_gdpr_applicable else "No"),
        ("Regulatory Frameworks", ", ".join(f.value for f in c.regulatory_frameworks) or "None specified"),
        ("Retention Period", c.retention_period or "Not specified"),
        ("Data Residency", c.data_residency_requirements or "Not specified"),
        ("Cross-Border Transfer Restrictions", "Yes" if c.cross_border_transfer_restrictions else "No"),
        ("Right to Erasure Applicable", "Yes" if c.right_to_erasure_applicable else "No"),
        ("Lawful Basis", c.lawful_basis or "Not specified"),
    ]:
        pdf.kv_row(key, val, shade)
        shade = not shade
    pdf.ln(4)


def _fields_section(pdf: MetadataPDF, metadata: DatasetMetadata):
    pdf.add_page()
    pdf.section_title(f"FIELD INVENTORY  ({len(metadata.fields)} fields)")

    # Table header
    col_w = [40, 22, 12, 28, 78]
    headers = ["Field Name", "Data Type", "PII", "Sensitivity", "Description"]
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 7)
    for w, h in zip(col_w, headers):
        pdf.cell(w, 6, h, fill=True, border=1, align="C")
    pdf.ln()

    for i, field in enumerate(metadata.fields):
        shade = i % 2 == 0
        bg = LIGHT_GRAY if shade else WHITE
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*BLACK)

        row_y = pdf.get_y()

        # Name
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(col_w[0], 6, _trunc(field.name, 24), fill=True, border=1)

        # Type
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(col_w[1], 6, field.data_type.value, fill=True, border=1, align="C")

        # PII
        pii_text = "Yes" if field.is_pii else ""
        if field.is_pii:
            pdf.set_fill_color(255, 235, 235)
            pdf.set_text_color(139, 0, 0)
        else:
            pdf.set_fill_color(*bg)
            pdf.set_text_color(*MID_GRAY)
        pdf.set_font("Helvetica", "B" if field.is_pii else "", 7)
        pdf.cell(col_w[2], 6, pii_text, fill=True, border=1, align="C")

        # Sensitivity
        sens_rgb = SENSITIVITY_RGB.get(field.sensitivity_level.value, (100, 116, 139))
        pdf.set_fill_color(*sens_rgb)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(col_w[3], 6, field.sensitivity_level.value.upper(), fill=True, border=1, align="C")

        # Description
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(col_w[4], 6, _trunc(field.description, 72), fill=True, border=1)
        pdf.ln()


def _pii_section(pdf: MetadataPDF, metadata: DatasetMetadata):
    pii_fields = [f for f in metadata.fields if f.is_pii]
    if not pii_fields:
        return

    pdf.ln(4)
    pdf.section_title(f"PII FIELD REGISTER  ({len(pii_fields)} fields)")
    pdf.set_font("Helvetica", "", 8)

    for i, field in enumerate(sorted(pii_fields, key=lambda f: f.sensitivity_level.rank, reverse=True)):
        shade = i % 2 == 0
        bg = LIGHT_GRAY if shade else WHITE
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*BLACK)

        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(50, 6, field.name, fill=True, border="B")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(35, 6, field.pii_type.value if field.pii_type else "—", fill=True, border="B")
        sens_rgb = SENSITIVITY_RGB.get(field.sensitivity_level.value, (100, 116, 139))
        pdf.set_fill_color(*sens_rgb)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(28, 6, field.sensitivity_level.value.upper(), fill=True, border="B", align="C")
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 7)
        pdf.cell(0, 6, _trunc(field.usage_guidance, 60), fill=True, border="B")
        pdf.ln()


def _quality_section(pdf: MetadataPDF, metadata: DatasetMetadata):
    qs = metadata.quality_score
    if not qs:
        return

    pdf.add_page()
    pdf.section_title("QUALITY EVALUATION REPORT")
    pdf.ln(2)

    # Overall score banner
    color = (45, 158, 71) if qs.passed else (209, 50, 50)
    pdf.set_fill_color(*color)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 12, f"  Overall Score: {qs.overall_score:.1f}/100   {'PASSED' if qs.passed else 'FAILED'}", fill=True, ln=True)
    pdf.ln(4)

    # Dimension table
    dims = [
        ("Completeness", qs.completeness, "30%"),
        ("PII Detection", qs.pii_detection, "25%"),
        ("Type Consistency", qs.type_consistency, "20%"),
        ("Banking Standards (BCBS 239)", qs.banking_standards, "15%"),
        ("Sensitivity Consistency", qs.sensitivity_consistency, "10%"),
    ]
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    for label, width in [("Dimension", 90), ("Score", 30), ("Weight", 20), ("Status", 40)]:
        pdf.cell(width, 6, label, fill=True, border=1, align="C")
    pdf.ln()

    for i, (name, dim, weight) in enumerate(dims):
        bg = LIGHT_GRAY if i % 2 == 0 else WHITE
        pdf.set_fill_color(*bg)
        pdf.set_text_color(*BLACK)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(90, 6, name, fill=True, border=1)
        pdf.cell(30, 6, f"{dim.score:.1f}/100", fill=True, border=1, align="C")
        pdf.cell(20, 6, weight, fill=True, border=1, align="C")
        status_color = (45, 158, 71) if dim.passed else (209, 50, 50)
        pdf.set_fill_color(*status_color)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(40, 6, "PASS" if dim.passed else "FAIL", fill=True, border=1, align="C")
        pdf.ln()

    pdf.ln(4)

    # Guardrails
    if qs.guardrails_applied:
        pdf.section_title(f"GUARDRAILS APPLIED ({len(qs.guardrails_applied)})")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*BLACK)
        for msg in qs.guardrails_applied:
            pdf.set_x(18)
            pdf.multi_cell(0, 5, f"• {msg}")
        pdf.ln(3)

    # Issues
    all_issues = []
    all_warnings = []
    for _, dim, _ in dims:
        all_issues.extend(dim.issues)
        all_warnings.extend(dim.warnings)

    if all_issues:
        pdf.section_title(f"ISSUES ({len(all_issues)})")
        pdf.set_text_color(139, 0, 0)
        pdf.set_font("Helvetica", "", 8)
        for issue in all_issues:
            pdf.set_x(18)
            pdf.multi_cell(0, 5, f"• {issue}")
        pdf.ln(3)

    if all_warnings:
        pdf.section_title(f"WARNINGS ({len(all_warnings)})")
        pdf.set_text_color(180, 100, 0)
        pdf.set_font("Helvetica", "", 8)
        for w in all_warnings:
            pdf.set_x(18)
            pdf.multi_cell(0, 5, f"• {w}")


def export(metadata: DatasetMetadata) -> bytes:
    pdf = MetadataPDF(metadata.dataset_name, metadata.classification.value)

    _cover_page(pdf, metadata)
    _compliance_section(pdf, metadata)
    _fields_section(pdf, metadata)
    _pii_section(pdf, metadata)
    _quality_section(pdf, metadata)

    return bytes(pdf.output())

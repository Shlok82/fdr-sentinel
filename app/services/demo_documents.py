from fpdf import FPDF
from typing import Dict, Any


def generate_demo_fdr_pdf(scenario_data: Dict[str, Any]) -> bytes:
    """Generates a synthetic FDR deposit receipt PDF with a real text layer and watermark.
    
    Guarantees:
    1. Returns valid PDF bytes starting with b"%PDF-".
    2. Embedded text layer contains scenario FDR number, bank name, amounts, and dates.
    3. Clear diagonal 'SPECIMEN - DEMO ONLY, NOT A REAL INSTRUMENT' watermark.
    4. Compact size (< 15 KB, well below the 10 MB cap).
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    if "producer" in scenario_data:
        pdf.set_producer(scenario_data["producer"])
    if "creator" in scenario_data:
        pdf.set_creator(scenario_data["creator"])
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # 1. Diagonal Watermark in light gray across page center
    with pdf.rotation(45, 105, 148):
        pdf.set_font("Helvetica", "B", 24)
        pdf.set_text_color(225, 225, 225)
        pdf.text(12, 148, "SPECIMEN - DEMO ONLY, NOT A REAL INSTRUMENT")

    # Reset text color for document body
    pdf.set_text_color(20, 20, 20)

    # 2. Outer Border Box
    pdf.set_draw_color(180, 180, 180)
    pdf.set_line_width(0.3)
    pdf.rect(15, 15, 180, 267)

    # Bank Name & Header
    bank_name = scenario_data.get("bank_name") or "DEMO NATIONAL BANK"
    branch = scenario_data.get("bank_branch") or "Model Town Branch, New Delhi"
    ifsc = scenario_data.get("ifsc_code") or "DEMO0123456"

    pdf.set_xy(20, 22)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(170, 8, bank_name, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(170, 5, f"Branch: {branch}  |  IFSC: {ifsc}", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_draw_color(234, 88, 12)  # aicte-600
    pdf.set_line_width(0.5)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)

    # Document Title
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(194, 65, 12)
    pdf.cell(170, 7, "FIXED DEPOSIT RECEIPT / TERM DEPOSIT ADVICE", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(170, 4, "(Regulatory Security Deposit Custody Specimen - Synthetic Prototype Document)", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)

    # Table of Key Receipt Information
    def draw_row(label: str, val: str, is_highlight: bool = False):
        y = pdf.get_y()
        if is_highlight:
            pdf.set_fill_color(248, 248, 250)
            pdf.rect(22, y, 166, 8, "F")
        pdf.set_xy(25, y)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(55, 8, label)
        pdf.set_font("Helvetica", "B" if is_highlight else "", 9)
        pdf.set_text_color(10, 10, 10)
        pdf.cell(110, 8, val or "- (Not Declared)")
        pdf.ln(8)

    draw_row("FDR / Receipt Number:", scenario_data.get("fdr_number", ""), is_highlight=True)
    draw_row("Principal Deposit (INR):", f"Rs. {scenario_data.get('principal_amount', '')}")
    draw_row("Maturity Value (INR):", f"Rs. {scenario_data.get('maturity_amount', '')}", is_highlight=True)
    draw_row("Interest Rate (% p.a.):", f"{scenario_data.get('interest_rate_percent', '')}% p.a. (Quarterly Compounding)")
    draw_row("Tenure (Months):", f"{scenario_data.get('tenure_months', '')} Months")
    draw_row("Date of Issue:", scenario_data.get("issue_date", ""), is_highlight=True)
    draw_row("Date of Maturity:", scenario_data.get("maturity_date", ""), is_highlight=True)
    draw_row("Joint Holder / Lien Clause:", scenario_data.get("joint_account_holder", "") or "- (Blank)")

    pdf.ln(6)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(22, pdf.get_y(), 188, pdf.get_y())
    pdf.ln(5)

    # Regulatory Notes Box
    pdf.set_fill_color(255, 251, 235)
    pdf.set_draw_color(245, 158, 11)
    pdf.rect(22, pdf.get_y(), 166, 26, "DF")
    pdf.set_xy(25, pdf.get_y() + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(146, 64, 14)
    pdf.cell(160, 4, "MANDATORY STATUTORY HOLDING & LIEN CLAUSE:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(120, 53, 15)
    pdf.multi_cell(
        160, 3.5,
        "The deposit confirmed herein is issued in joint holding with the All India Council for Technical Education (AICTE). "
        "Neither premature withdrawal, encashment, loan against deposit, nor lien release is permitted without dual-key "
        "statutory written clearance from the AICTE Competent Authority."
    )

    # Signatures
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.set_xy(25, 248)
    pdf.cell(70, 4, "____________________________", align="L")
    pdf.cell(70, 4, "____________________________", align="R")
    pdf.ln(4)
    pdf.set_xy(25, 252)
    pdf.cell(70, 4, "Authorized Bank Signatory", align="L")
    pdf.cell(70, 4, "Branch Manager / Officer Seal", align="R")

    # System Watermark footer
    pdf.set_xy(20, 272)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(170, 4, "Generated by FDR Sentinel Prototype - Synthetic Specimen for Evaluation Purposes Only", align="C")

    return bytes(pdf.output())


def generate_sample_fdr_pdf(scenario_key_or_data: Any, db: Any = None) -> bytes:
    """Helper accepting either a scenario key string or scenario data dictionary."""
    if isinstance(scenario_key_or_data, str):
        from app.services.demo_scenarios import get_scenario_data
        data = get_scenario_data(scenario_key_or_data, db) or {
            "bank_name": "DEMO NATIONAL BANK",
            "fields": {"fdr_number": "DEMO-FDR-001"}
        }
    else:
        data = scenario_key_or_data
    return generate_demo_fdr_pdf(data)

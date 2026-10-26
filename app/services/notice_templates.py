"""Notice template rendering for proactive alerts and lifecycle notifications.

Plain text templates in English ('en') with null-safe placeholders and SMS <= 320 characters.
"""
from datetime import date, datetime
from typing import Dict, Any, Tuple


def _fmt_date(val: Any) -> str:
    """Safely format an ISO date string or date object into DD Mon YYYY."""
    if not val:
        return "N/A"
    if isinstance(val, (date, datetime)):
        return val.strftime("%d %b %Y")
    if isinstance(val, str):
        try:
            # Handle YYYY-MM-DD or ISO timestamp
            dt_part = val.split("T")[0]
            d = datetime.strptime(dt_part, "%Y-%m-%d").date()
            return d.strftime("%d %b %Y")
        except Exception:
            return val
    return str(val)


def render_notice(alert_type: str, details: Dict[str, Any], language: str = "en") -> Tuple[str, str, str]:
    """Renders plain text subject, body, and SMS text for an alert type.

    Returns:
        (subject, body, sms_text)
    """
    fdr_num = details.get("fdr_number") or "[FDR number not recorded]"
    inst_name = details.get("institution_name") or "[Institution not recorded]"
    bank_name = details.get("bank_name") or "[Bank not recorded]"
    maturity_date = _fmt_date(details.get("maturity_date"))

    if alert_type.startswith("MATURITY_REMINDER_"):
        days = details.get("days_to_maturity", details.get("threshold_days", 0))
        urgency_tag = "URGENT" if days <= 7 else "REMINDER"
        action_tag = "ACTION REQUIRED" if days <= 30 else "NOTICE"
        
        subject = f"[{action_tag}] FDR Renewal Notice: {fdr_num} matures in {days} days ({maturity_date})"
        body = (
            f"Dear Institution Administrator,\n\n"
            f"RE: FDR RENEWAL NOTICE - {fdr_num}\n\n"
            f"Please be advised that the Fixed Deposit Receipt detailed below is approaching maturity:\n\n"
            f"Institution: {inst_name}\n"
            f"FDR Number: {fdr_num}\n"
            f"Bank: {bank_name}\n"
            f"Maturity Date: {maturity_date}\n"
            f"Days Remaining: {days}\n\n"
            f"Under AICTE regulatory norms, all security deposit FDRs must remain active and encashable "
            f"throughout the approved programme period. You are required to initiate the renewal process "
            f"with your issuing bank immediately.\n\n"
            f"Once renewed, please submit the renewed FDR receipt through the FDR-Sentinel portal before "
            f"the maturity date to prevent regulatory compliance notices.\n\n"
            f"Regards,\n"
            f"AICTE FDR Monitoring Cell"
        )
        sms_text = (
            f"[FDR-SENTINEL] {urgency_tag}: FDR {fdr_num} ({bank_name}) matures in {days} days on {maturity_date}. "
            f"Please initiate renewal or upload renewal receipt on FDR-Sentinel to avoid regulatory action."
        )[:320]
        return subject, body, sms_text

    elif alert_type == "MATURED_NOTICE":
        days_overdue = details.get("days_overdue", 0)
        subject = f"[CRITICAL] FDR Has Matured: {fdr_num} ({inst_name})"
        body = (
            f"Dear Institution Administrator,\n\n"
            f"RE: CRITICAL NOTICE - FDR MATURITY REACHED ({fdr_num})\n\n"
            f"The following Fixed Deposit Receipt held under AICTE lien reached maturity on {maturity_date}:\n\n"
            f"Institution: {inst_name}\n"
            f"FDR Number: {fdr_num}\n"
            f"Bank: {bank_name}\n"
            f"Maturity Date: {maturity_date}\n"
            f"Days Elapsed Since Maturity: {days_overdue}\n\n"
            f"Immediate regulatory compliance is required:\n"
            f"1. If the FDR has been renewed, upload the renewal certificate/receipt immediately.\n"
            f"2. If approved for release, ensure release formalities are recorded.\n\n"
            f"Unrenewed matured FDRs are subject to automatic compliance escalation and audit flagging under regulatory guidelines.\n\n"
            f"Regards,\n"
            f"AICTE FDR Monitoring Cell"
        )
        sms_text = (
            f"[FDR-SENTINEL] CRITICAL: FDR {fdr_num} ({bank_name}) reached maturity on {maturity_date}. "
            f"Upload renewed deposit receipt immediately on FDR-Sentinel."
        )[:320]
        return subject, body, sms_text

    elif alert_type == "OVERDUE_ESCALATION":
        days_overdue = details.get("days_overdue", 15)
        subject = f"[ESCALATION] FDR {fdr_num} Overdue for Renewal ({days_overdue} days past maturity)"
        body = (
            f"Dear Approving Authority,\n\n"
            f"RE: REGULATORY ESCALATION - FDR OVERDUE FOR RENEWAL\n\n"
            f"The following FDR has been matured for {days_overdue} days without renewal documentation submitted by the institution:\n\n"
            f"Institution: {inst_name}\n"
            f"FDR Number: {fdr_num}\n"
            f"Bank: {bank_name}\n"
            f"Maturity Date: {maturity_date}\n"
            f"Days Overdue: {days_overdue}\n\n"
            f"Despite automated maturity reminders and critical notices, the institution has not submitted renewal verification. "
            f"This instrument is now escalated for Approving Authority review and potential regulatory enforcement.\n\n"
            f"Regards,\n"
            f"FDR-Sentinel Automated Life-Cycle Monitor"
        )
        sms_text = (
            f"[FDR-SENTINEL] ESCALATION: FDR {fdr_num} ({inst_name}) is {days_overdue} days overdue for renewal. "
            f"Approver review recommended."
        )[:320]
        return subject, body, sms_text

    elif alert_type == "PENDING_REVIEW_AGE_7D":
        age_days = details.get("age_days", 7)
        submitted_at = _fmt_date(details.get("submitted_at"))
        subject = f"[WORKSTATION] FDR {fdr_num} awaiting scrutiny for {age_days} days"
        body = (
            f"Dear Scrutiny Officer,\n\n"
            f"RE: WORKSTATION QUEUE ALERT - PENDING REVIEW ({fdr_num})\n\n"
            f"The following instrument has remained in the scrutiny queue for {age_days} days:\n\n"
            f"Institution: {inst_name}\n"
            f"FDR Number: {fdr_num}\n"
            f"Submission Date: {submitted_at}\n\n"
            f"Please review the document verification queue and record scrutiny findings or schedule bank verification.\n\n"
            f"Regards,\n"
            f"FDR-Sentinel Workflow Monitor"
        )
        sms_text = (
            f"[FDR-SENTINEL] Notice: FDR {fdr_num} awaiting scrutiny for {age_days} days. Please review queue."
        )[:320]
        return subject, body, sms_text

    elif alert_type == "UNRESOLVED_FLAG_14D":
        flag_age_days = details.get("flag_age_days", 14)
        flagged_at = _fmt_date(details.get("flagged_at"))
        subject = f"[ESCALATION] Discrepancy on FDR {fdr_num} unresolved for {flag_age_days} days"
        body = (
            f"Dear Approving Authority,\n\n"
            f"RE: ESCALATION - UNRESOLVED DISCREPANCY ({fdr_num})\n\n"
            f"The following instrument has remained flagged with an active discrepancy ticket for {flag_age_days} days without resolution:\n\n"
            f"Institution: {inst_name}\n"
            f"FDR Number: {fdr_num}\n"
            f"Flagged Date: {flagged_at}\n\n"
            f"Administrative review or hearing notice may be required.\n\n"
            f"Regards,\n"
            f"FDR-Sentinel Discrepancy Engine"
        )
        sms_text = (
            f"[FDR-SENTINEL] ESCALATION: Discrepancy on FDR {fdr_num} ({inst_name}) unresolved for {flag_age_days} days. "
            f"Escalation review required."
        )[:320]
        return subject, body, sms_text

    # Default fallback
    subject = f"[ALERT] {alert_type}: FDR {fdr_num}"
    body = (
        f"Notice for FDR {fdr_num} ({inst_name}):\n\n"
        f"Alert Type: {alert_type}\n"
        f"Details: {details}\n\n"
        f"FDR-Sentinel Automated Alert"
    )
    sms_text = f"[FDR-SENTINEL] Notice: {alert_type} on FDR {fdr_num}."[:320]
    return subject, body, sms_text

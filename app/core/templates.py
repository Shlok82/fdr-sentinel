from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from fastapi.templating import Jinja2Templates
from app.core.config import settings


def _to_display_tz(dt: datetime) -> datetime:
    """Converts naive (assumed UTC) or aware datetime to configured DISPLAY_TIMEZONE."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(settings.display_tz)


def format_inr(val: Any) -> str:
    """Formats a numeric amount with the rupee symbol and Indian numbering comma grouping.
    
    Examples:
        3500000.00 -> ₹ 35,00,000.00
        50000      -> ₹ 50,000.00
        None       -> —
    """
    if val is None or val == "":
        return "—"
    try:
        d = Decimal(str(val))
    except Exception:
        return "—"

    s = f"{d:.2f}"
    int_part, dec_part = s.split(".")

    is_negative = int_part.startswith("-")
    if is_negative:
        int_part = int_part[1:]

    if len(int_part) <= 3:
        formatted = int_part
    else:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        formatted = ",".join(groups) + "," + last3

    if is_negative:
        return f"-₹ {formatted}.{dec_part}"

    return f"₹ {formatted}.{dec_part}"


def format_date(val: Any, fmt: str = "%d %b %Y") -> str:
    """Formats a date or datetime object, returning '—' for missing/empty values.
    
    If given a datetime or ISO timestamp string, converts to DISPLAY_TIMEZONE first
    so the calendar date accurately reflects IST.
    """
    if val is None or val == "":
        return "—"
    if isinstance(val, datetime):
        return _to_display_tz(val).strftime(fmt)
    if isinstance(val, date):
        return val.strftime(fmt)
    try:
        s = str(val).strip()
        if "T" in s or " " in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return _to_display_tz(dt).strftime(fmt)
        d = date.fromisoformat(s)
        return d.strftime(fmt)
    except Exception:
        return "—"


def format_datetime(val: Any, fmt: Optional[str] = None) -> str:
    """Formats a datetime object converted to DISPLAY_TIMEZONE, returning '—' for missing values.
    
    Output format: 'DD Mon YYYY, HH:MM IST' (e.g. '26 Oct 2026, 03:02 IST').
    Always ends with 'IST'.
    """
    if val is None or val == "":
        return "—"
    dt = None
    if isinstance(val, datetime):
        dt = val
    elif hasattr(val, "strftime") and hasattr(val, "hour"):
        dt = val
    elif isinstance(val, date):
        return f"{val.strftime('%d %b %Y')} IST"
    else:
        try:
            s = str(val).strip()
            if "T" in s or " " in s:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                d = date.fromisoformat(s)
                return f"{d.strftime('%d %b %Y')} IST"
        except Exception:
            return "—"

    if dt is None:
        return "—"

    local_dt = _to_display_tz(dt)
    if fmt:
        clean_fmt = fmt.replace("UTC", "IST")
        res = local_dt.strftime(clean_fmt)
        if not res.endswith("IST"):
            res = f"{res} IST"
        return res
    return f"{local_dt.strftime('%d %b %Y, %H:%M')} IST"


def format_filesize(val: Any) -> str:
    """Formats byte size into KB or MB with 1 decimal place, returning '—' for missing values."""
    if val is None or val == "":
        return "—"
    try:
        b = float(val)
        if b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        return f"{b / (1024 * 1024):.2f} MB"
    except Exception:
        return "—"


def default_dash(val: Any) -> str:
    """Returns '—' if val is None or empty string, else str(val)."""
    if val is None or str(val).strip() == "":
        return "—"
    return str(val)


# Shared singleton Jinja2Templates environment
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["format_inr"] = format_inr
templates.env.filters["format_date"] = format_date
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["format_filesize"] = format_filesize
templates.env.filters["default_dash"] = default_dash

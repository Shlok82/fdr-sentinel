from datetime import date, datetime
from decimal import Decimal
from typing import Any
from fastapi.templating import Jinja2Templates


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
    """Formats a date or datetime object, returning '—' for missing/empty values."""
    if val is None or val == "":
        return "—"
    if hasattr(val, "strftime"):
        return val.strftime(fmt)
    try:
        s = str(val).strip()
        if "T" in s or " " in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime(fmt)
        d = date.fromisoformat(s)
        return d.strftime(fmt)
    except Exception:
        return "—"


def format_datetime(val: Any, fmt: str = "%d %b %Y, %H:%M") -> str:
    """Formats a datetime object with hours and minutes, returning '—' for missing values."""
    if val is None or val == "":
        return "—"
    if hasattr(val, "strftime"):
        return val.strftime(fmt)
    try:
        s = str(val).strip()
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime(fmt)
    except Exception:
        return "—"


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

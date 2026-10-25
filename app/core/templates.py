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
        return str(val)

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
        formatted = "-" + formatted

    return f"₹ {formatted}.{dec_part}"


# Shared singleton Jinja2Templates environment
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["format_inr"] = format_inr

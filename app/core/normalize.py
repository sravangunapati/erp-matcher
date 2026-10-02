import html
import math
import re

PLACEHOLDERS = {"NA", "NONE", "TBD", "UNKNOWN", "MISC", "VARIOUS", "SEEDESC", "NOPN"}

def clean(value) -> str:
    """None/NaN -> '', decode HTML (&amp;), drop ®/™, collapse spaces, uppercase."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""

    text = html.unescape(str(value)).replace("®", " ").replace("™", " ")
    return re.sub(r"\s+", " ", text).strip().upper()

def compact(text: str) -> str:
    """'829064-1001' -> '8290641001'"""
    return re.sub(r"[^A-Z0-9]", "", text.upper())

def no_zeros(text: str) -> str:
    """'04016' -> '4016'"""
    return compact(text).lstrip("0")

def is_placeholder(text: str) -> bool:
    """'TBD', 'N/A', '0000000', '000000000000055'"""
    key = compact(text)
    repeated = len(set(key)) == 1 and (key[0] == "0" or len(key) >= 4)
    return not key or key in PLACEHOLDERS or repeated or (len(key) >= 8 and len(key.lstrip("0")) <= 3)

def _check_digit(body: str) -> str:
    """GS1 check digit for the digits before it: weights 3,1,3,1... from the right."""
    total = sum(int(d) * (3 if i% 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)

def gtin14(text: str) -> str | None:
    """Valid UPC/EAN -> 14-digit GTIN, else None."""
    digits = re.sub(r"\D", "", text)
    if len(digits) not in (8, 12, 13, 14) or _check_digit(digits[:-1]) != digits[-1]:
        return None
    return digits.zfill(14)
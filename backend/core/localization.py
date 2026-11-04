"""Bangladesh localization: Bangla digits, BDT formatting, mobile/NID/district validators, dates."""
import re
import difflib
from datetime import date, datetime
from typing import Optional, Tuple

BN_DIGITS = "০১২৩৪৫৬৭৮৯"
_BN2EN = {ord(b): str(i) for i, b in enumerate(BN_DIGITS)}
_EN2BN = {ord(str(i)): b for i, b in enumerate(BN_DIGITS)}


def to_en_digits(s) -> str:
    """১২৩ -> 123"""
    return str(s).translate(_BN2EN)


def to_bn_digits(s) -> str:
    """123 -> ১২৩"""
    return str(s).translate(_EN2BN)


def has_bn_digits(s) -> bool:
    return any(ch in BN_DIGITS for ch in str(s))


def fmt_bdt(amount, lang: str = "en", symbol: bool = True, decimals: int = 0) -> str:
    """Format using the South-Asian lakh/crore grouping: 12500000 -> ৳1,25,00,000."""
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    neg = amt < 0
    amt = abs(amt)
    whole, frac = divmod(round(amt, decimals), 1)
    s = str(int(whole))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    if decimals:
        s += "." + f"{frac:.{decimals}f}"[2:]
    out = ("-" if neg else "") + ("৳" if symbol else "") + s
    return to_bn_digits(out) if lang == "bn" else out


# ---------------------------------------------------------------- mobile numbers
OPERATORS = {
    "13": "Grameenphone", "17": "Grameenphone",
    "14": "Banglalink", "19": "Banglalink",
    "15": "Teletalk",
    "16": "Airtel/Robi", "18": "Robi",
}


def normalize_bd_mobile(value) -> Optional[str]:
    """Return +8801XXXXXXXXX for any plausible BD mobile format (incl. Bangla digits), else None."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", to_en_digits(value))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("880"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"1[3-9]\d{8}", digits):
        return "+880" + digits
    return None


def bd_operator(value) -> Optional[str]:
    n = normalize_bd_mobile(value)
    return OPERATORS.get(n[4:6]) if n else None


# ---------------------------------------------------------------- NID
NA_PREFIXES = ("n/a", "na", "passport", "foreign")


def valid_bd_nid(value) -> bool:
    """Bangladesh NID: 10 (smart), 13 (old) or 17 (old with birth year) digits."""
    s = to_en_digits(value).strip()
    return bool(re.fullmatch(r"\d{10}|\d{13}|\d{17}", s))


def nid_not_applicable(value) -> bool:
    """Expatriates legitimately have no NID (e.g. 'N/A (Passport)')."""
    return str(value).strip().lower().startswith(NA_PREFIXES)


# ---------------------------------------------------------------- administrative geography
DIVISIONS = {
    "Dhaka": ["Dhaka", "Faridpur", "Gazipur", "Gopalganj", "Kishoreganj", "Madaripur", "Manikganj",
              "Munshiganj", "Narayanganj", "Narsingdi", "Rajbari", "Shariatpur", "Tangail"],
    "Chattogram": ["Bandarban", "Brahmanbaria", "Chandpur", "Chattogram", "Cumilla", "Cox's Bazar", "Feni",
                   "Khagrachhari", "Lakshmipur", "Noakhali", "Rangamati"],
    "Rajshahi": ["Bogura", "Joypurhat", "Naogaon", "Natore", "Chapainawabganj", "Pabna", "Rajshahi", "Sirajganj"],
    "Khulna": ["Bagerhat", "Chuadanga", "Jashore", "Jhenaidah", "Khulna", "Kushtia", "Magura", "Meherpur",
               "Narail", "Satkhira"],
    "Barishal": ["Barguna", "Barishal", "Bhola", "Jhalokati", "Patuakhali", "Pirojpur"],
    "Sylhet": ["Habiganj", "Moulvibazar", "Sunamganj", "Sylhet"],
    "Rangpur": ["Dinajpur", "Gaibandha", "Kurigram", "Lalmonirhat", "Nilphamari", "Panchagarh", "Rangpur",
                "Thakurgaon"],
    "Mymensingh": ["Jamalpur", "Mymensingh", "Netrokona", "Sherpur"],
}
DISTRICTS = [d for ds in DIVISIONS.values() for d in ds]
_ALIASES = {"comilla": "Cumilla", "chittagong": "Chattogram", "bogra": "Bogura", "jessore": "Jashore",
            "barisal": "Barishal", "coxs bazar": "Cox's Bazar", "cox bazar": "Cox's Bazar",
            "narayanganj": "Narayanganj", "munshigonj": "Munshiganj", "dacca": "Dhaka"}


def normalize_district(value) -> Optional[str]:
    """Match a (possibly misspelt / old-spelling) district or division name to the official one."""
    v = re.sub(r"[^a-z' ]", "", str(value).lower()).strip()
    if not v:
        return None
    v = v.replace("'", "")
    if v in _ALIASES:
        return _ALIASES[v]
    pool = {d.lower().replace("'", ""): d for d in DISTRICTS + list(DIVISIONS)}
    if v in pool:
        return pool[v]
    m = difflib.get_close_matches(v, list(pool), n=1, cutoff=0.82)
    return pool[m[0]] if m else None


# ---------------------------------------------------------------- numbers and dates
_NUM_JUNK = re.compile(r"(?i)(৳|bdt|tk\.?|taka|usd|\$|us\$|,|\s)")


def to_number(value) -> Optional[float]:
    """'৳ 1,25,000' / '১২৫০০০' / '$1,200.50' -> float. None when not numeric."""
    if value is None:
        return None
    s = _NUM_JUNK.sub("", to_en_digits(value))
    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DMON = re.compile(r"^(\d{1,2})[ \-]([A-Za-z]{3})[a-z]*[ \-,]*(\d{4})$")


def parse_date_flex(value) -> Tuple[Optional[date], Optional[str]]:
    """Return (date, kind) where kind is 'iso' | 'dmy' | 'dmon' | None. BD convention = day-first."""
    s = to_en_digits(value).strip()
    try:
        m = _ISO.match(s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])), "iso"
        m = _DMY.match(s)
        if m:
            return date(int(m[3]), int(m[2]), int(m[1])), "dmy"
        m = _DMON.match(s)
        if m and m[2].lower() in _MONTHS:
            return date(int(m[3]), _MONTHS[m[2].lower()], int(m[1])), "dmon"
    except ValueError:
        return None, None
    return None, None


def today() -> date:
    return datetime.now().date()

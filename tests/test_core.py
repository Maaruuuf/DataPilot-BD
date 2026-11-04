from backend.core.localization import (fmt_bdt, to_en_digits, to_bn_digits, normalize_bd_mobile, valid_bd_nid,
                                       normalize_district, to_number, parse_date_flex, bd_operator)
from backend.core.privacy import Tokenizer, mask_value


def test_bdt_lakh_crore():
    assert fmt_bdt(125000) == "৳1,25,000"
    assert fmt_bdt(12500000) == "৳1,25,00,000"
    assert fmt_bdt(999) == "৳999" and fmt_bdt(-4500000) == "-৳45,00,000"
    assert fmt_bdt(125000, "bn") == "৳১,২৫,০০০"
    assert fmt_bdt(1234.5, decimals=2) == "৳1,234.50"


def test_bangla_digits():
    assert to_en_digits("০১৭১২-৩৪৫") == "01712-345" and to_bn_digits("2026") == "২০২৬"
    assert to_number("৳ ১,২৫,০০০") == 125000 and to_number("$1,200.50") == 1200.5 and to_number("abc") is None


def test_mobile_and_nid():
    for v in ("01712345678", "+8801712345678", "8801712345678", "০১৭১২৩৪৫৬৭৮", "017-1234 5678"):
        assert normalize_bd_mobile(v) == "+8801712345678", v
    assert normalize_bd_mobile("01212345678") is None and normalize_bd_mobile("+88017123") is None
    assert bd_operator("01812345678") == "Robi" and bd_operator("01912345678") == "Banglalink"
    assert valid_bd_nid("1234567890") and valid_bd_nid("1234567890123") and not valid_bd_nid("12345")


def test_district_and_dates():
    assert normalize_district("Comilla") == "Cumilla" and normalize_district("chittagong") == "Chattogram"
    assert normalize_district("Dhaka") == "Dhaka" and normalize_district("Atlantis") is None
    assert normalize_district("Sylet") == "Sylhet"
    assert parse_date_flex("15/03/2026")[0].isoformat() == "2026-03-15" and parse_date_flex("31/02/2026")[0] is None
    assert parse_date_flex("১৫-০৩-২০২৬")[0].isoformat() == "2026-03-15"


def test_privacy_tokenizer_roundtrip():
    t = Tokenizer()
    masked = t.mask("Call 01712345678 or mail rafiq@biman.com, NID 1234567890123")
    assert "01712345678" not in masked and "rafiq@" not in masked and "1234567890123" not in masked
    assert t.unmask(masked) == "Call 01712345678 or mail rafiq@biman.com, NID 1234567890123"
    assert mask_value("+8801712345678").startswith("+88") and mask_value("a@b.com") == "a***@b.com"

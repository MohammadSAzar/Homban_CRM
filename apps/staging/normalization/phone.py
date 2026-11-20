"""Only safely recognized Iranian mobile numbers compare equal in ND1."""
import re
from .text import DIGITS


def normalize_phone(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    raw = value.translate(DIGITS).strip()
    if re.search(r"[^0-9+()\s-]", raw):
        return None
    raw = re.sub(r"[()\s-]", "", raw)
    for prefix in ("+98", "0098", "98"):
        if raw.startswith(prefix):
            raw = "0" + raw[len(prefix):]
            break
    return raw if re.fullmatch(r"09[0-9]{9}", raw) else None


def phone_query_forms(value):
    mobile = normalize_phone(value)
    if mobile is None:
        return ()
    national = mobile[1:]
    forms = set()
    for prefix, number in (("", mobile), ("+98", national), ("0098", national), ("98", national)):
        width = 4 if not prefix else 3
        for separator in ("", " ", "-"):
            forms.add(prefix + number[:width] + separator + number[width:width+3] + separator + number[width+3:])
        if prefix:
            forms.add(prefix + " " + number)
            forms.add(prefix + " (" + number[:3] + ") " + number[3:6] + " " + number[6:])
        else:
            forms.add("(" + mobile[:4] + ") " + mobile[4:7] + " " + mobile[7:])
    originals = tuple(forms)
    for alphabet in ("۰۱۲۳۴۵۶۷۸۹", "٠١٢٣٤٥٦٧٨٩"):
        translation = str.maketrans("0123456789", alphabet)
        forms.update(item.translate(translation) for item in originals)
    return tuple(sorted(forms))

"""Comparison-only normalization; preserves punctuation and meaningful joiners."""
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
LETTERS = str.maketrans({"ي": "ی", "ك": "ک"})
NORMALIZATION_VERSION = 1


def normalize_text(value):
    if not isinstance(value, str):
        return None
    return " ".join(value.translate(LETTERS).split()).casefold() or None


def text_query_forms(value):
    normalized = normalize_text(value)
    if normalized is None:
        return ()
    # Bounded raw forms, not an exhaustive whitespace/transliteration expansion.
    return tuple(sorted({value.strip(), normalized, normalized.replace("ی", "ي"),
                         normalized.replace("ک", "ك"), normalized.replace("ی", "ي").replace("ک", "ك")}))

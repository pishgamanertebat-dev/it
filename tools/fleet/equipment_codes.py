"""Owner-verified excavator input aliases, accepted on 2026-11-11.

Only input resolution changes. Historical source text is never rewritten.
"""
import re

DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
EXCAVATOR_ALIASES = {str(1000+n): 'EX'+str(1250+n) for n in range(1, 7)}
ALIAS_VERSION = 'owner-excavator-1001-1006-20261111'


def canonical_input(value):
    original = str(value)
    clean = original.translate(DIGITS).strip().upper().replace('\u200c', ' ')
    match = re.fullmatch(r'(?:EX\s*|بیل\s*)?(100[1-6])', clean)
    return EXCAVATOR_ALIASES[match[1]] if match else original

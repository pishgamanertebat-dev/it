"""Explicit inspected field coordinates shared by maintenance entry and reporting."""
from functools import lru_cache
import json
from pathlib import Path

LAYOUT = Path(__file__).resolve().parents[1] / 'maintenance_daily/layout.json'


@lru_cache(maxsize=1)
def configuration(version):
    return json.loads(LAYOUT.read_text('utf8'))


def inspected_rule(sheet):
    settings = configuration(LAYOUT.stat().st_mtime_ns)
    rule = settings['sheets'].get(sheet.title)
    variants = ([rule, *[{**rule, **v} for v in rule.get('schema_variants', [])]] if rule else [])
    extra = settings.get('additional_schema_variants', {}).get(sheet.title)
    if extra:
        variants.append(extra)
    matches = []
    for candidate in variants:
        actual = {c.column: c.value for c in sheet[candidate['header_row']] if c.value is not None}
        if actual == dict(candidate['columns']) and candidate.get('report_columns'):
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None

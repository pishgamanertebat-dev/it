"""Units explicitly excluded by the operator from oil-service planning."""
from tools.fleet.greasing.source import clean

EXCLUDED_MODELS = {('لودر', '470-6'), ('بیل مکانیکی', '230')}
EXCLUDED_CODES = {'W471', 'WA471', 'EX231'}


def excluded_codes(plans):
    return EXCLUDED_CODES | {clean(p['code']).upper() for p in plans
        if (clean(p['kind']), clean(p['model']).upper()) in EXCLUDED_MODELS}


def included_plans(plans):
    excluded = excluded_codes(plans)
    return [p for p in plans if clean(p['code']).upper() not in excluded]

OUTER = 'AIR_FILTER_OUTER'
BOTH = 'AIR_FILTER_INNER_OUTER'
ACTIONS = {OUTER: 'تعویض هواکش بیرونی', BOTH: 'تعویض هواکش داخلی و بیرونی'}


def rule_for(code, name):
    if code == '231' or name == 'بیل':
        return None
    if name == 'دامپتراک':
        return {'inner': (100, 90), 'outer': (20, 17)}
    if name in {'کامیون سهند زرد', 'کامیون آب پاش', 'ژنراتور'}:
        return {'inner': (50, 45), 'outer': (10, 7)}
    if name == 'خاور':
        return {'inner': (10, 7), 'outer': (10, 7), 'together': True}
    if name in {'مزدا', 'پیکاپ ریچ'}:
        return {'calendar': 2}
    raise ValueError('قاعدهٔ دستگاه مشخص نیست')



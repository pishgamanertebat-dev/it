OUTER = 'AIR_FILTER_OUTER'
BOTH = 'AIR_FILTER_INNER_OUTER'
ACTIONS = {OUTER: 'تعویض هواکش بیرونی', BOTH: 'تعویض هواکش داخلی و بیرونی'}


def rule_for(code, name):
    if code == 'DG1' or 'ژنراتور' in name:
        return None
    if name in {'بیل', 'بیل مکانیکی'} or code.startswith('EX'):
        return {'inner': (10, 7), 'outer': (5, 4)}
    if name == 'لودر' or code.startswith('W'):
        return {'inner': (10, 7), 'outer': (10, 7), 'together': True}
    if name == 'بلدوزر' or code in {'151', '152'}:
        return {'inner': (10, 7), 'outer': (10, 7), 'together': True}
    if name == 'دامپتراک':
        return {'inner': (100, 90), 'outer': (20, 17)}
    if name in {'کامیون سهند زرد', 'کامیون آب پاش'}:
        return {'inner': (50, 45), 'outer': (10, 7)}
    if name == 'خاور':
        return {'inner': (10, 7), 'outer': (10, 7), 'together': True}
    if name in {'مزدا', 'پیکاپ ریچ'}:
        return {'calendar': 2}
    raise ValueError('قاعدهٔ دستگاه مشخص نیست')



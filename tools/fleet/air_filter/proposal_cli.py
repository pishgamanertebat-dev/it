import argparse
import json
from pathlib import Path
from tools.fleet.air_filter.proposal import build_proposal, read_source, MARKS, clean
from tools.fleet.air_filter.rules import BOTH, rule_for
from tools.fleet.preview_air_filter_due import parse_header_date
from tools.fleet.work_orders.channels.bale.proposal_form import render


def parse_target(text):
    parts = text.replace('-', '/').split('/')
    if len(parts) == 3:
        if parts.pop(0) != '1405':
            raise ValueError('Only operational year 1405 is supported.')
    date = parse_header_date('.'.join(parts))
    if date is None:
        raise ValueError('Invalid target date; use 1405/06/09.')
    return date


def run(backtest=False):
    parser=argparse.ArgumentParser()
    parser.add_argument('--file',required=True)
    parser.add_argument('--target',required=backtest)
    parser.add_argument('--show-all',action='store_true')
    args=parser.parse_args()
    target=parse_target(args.target) if args.target else None
    proposal=build_proposal(args.file,target)
    text=render(proposal)
    if backtest:
        machines,*_,digest=read_source(args.file)
        if digest != proposal['source_sha256']:
            raise ValueError('Source changed during comparison; rerun.')
        actual=set()
        recorded=False
        for m in machines:
            rule=rule_for(m['code'],m['name'])
            if rule is None:
                continue
            for d in m['daily']:
                if d['date'] != target:
                    continue
                recorded |= any(clean(d[k]) for k in ('hours','inner','outer'))
                inner=any(clean(value) in MARKS for value in d.get('inner_values',[d['inner']]))
                outer=inner or any(clean(value) in MARKS for value in d.get('outer_values',[d['outer']]))
                if rule.get('together') and outer:
                    inner=True
                if inner and 'calendar' not in rule:
                    actual.add((m['code'],'inner'))
                if outer:
                    actual.add((m['code'],'outer'))
        if not recorded:
            raise ValueError('No recorded target-day observations; a backtest comparison is unavailable.')
        predicted={(i['machine_code'],field) for i in proposal['items'] for field in (['inner','outer'] if i['action_code']==BOTH else ['outer'])}
        proposal['comparison']={key:sorted(values) for key,values in [('matched',predicted & actual),('suggested_not_recorded',predicted-actual),('recorded_not_suggested',actual-predicted)]}
        text += '\n\nمقایسه با انجام ثبت‌شده (تفاوت، لزوماً خطای محاسبه نیست):\n' + json.dumps(proposal['comparison'],ensure_ascii=False,indent=2)
    if args.show_all:
        text += '\n\n' + json.dumps(proposal['machines'],ensure_ascii=False,indent=2)
    folder=Path('E:/KomatsoAI/reports/fleet/air_filter')
    folder.mkdir(parents=True,exist_ok=True)
    stem=('backtest_' if backtest else 'proposal_')+proposal['plan_date'].replace('/','-')
    (folder/(stem+'.txt')).write_text(text,encoding='utf-8')
    (folder/(stem+'.json')).write_text(json.dumps(proposal,ensure_ascii=False,indent=2),encoding='utf-8')
    print(text)

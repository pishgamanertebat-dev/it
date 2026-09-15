"""Verify source rendering and Bale credentials without sending a message."""
import asyncio
import json
import tempfile

from .tasks import ROOT, BaleSender, build_report


def main():
    directory = ROOT / 'runtime/scheduler'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='probe-', dir=directory) as output:
        result = asyncio.run(build_report(None, output))
        if not result.get('ok') or not result.get('images'):
            raise RuntimeError('Report rendering check failed')
        sender = BaleSender()
        try:
            sender.check_connection()
        finally:
            sender.close()
        evidence = dict(bale_connection=True, report_date=result['report']['date'], images=len(result['images']), messages_sent=0)
        (directory / 'probe.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print(json.dumps(evidence))


if __name__ == '__main__':
    main()

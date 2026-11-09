"""Focused Phase 4 suite plus the installed seven-day/manual/NET/Sync contracts."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(ROOT))
    from integrations.hermes import verify_seven_day_entry as previous
    previous.NAMES.insert(0,'tools.fleet.repairs.test_ai_daily_entry')
    previous.NAMES.insert(0,'tools.fleet.repairs.test_ai_daily_manual')
    previous.main()


if __name__=='__main__':main()

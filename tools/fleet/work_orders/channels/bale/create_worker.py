"""Run the existing Work Order Core in the project Python environment.

The live caller passes authenticated sender identity and form fields as JSON on
stdin. Hermes' Python does not have the Excel dependencies installed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from tools.fleet.work_orders.core.permissions import (
    WorkOrderPermissionDenied,
    require_work_order_permission,
)
from tools.fleet.work_orders.core import service
from tools.fleet.work_orders.core.review import confirm_document_review, normalize_shift


def preview_order(order):
    return {"work_order_no": order["work_order_no"], "label": order["work_order_label_fa"],
            "item_count": len(order["items"]), "status": order["status"],
            "file_name": Path(order["excel_path"]).name, "file_path": order["excel_path"]}


def execute_request(request: dict, *, db_path=None) -> dict:
    try:
        actor = require_work_order_permission(request.get("bale_id"), db_path=db_path)
        if request['action'] == 'propose':
            if request.get('work_order_type') == 'GREASING':
                from tools.fleet.greasing.proposal import build_proposal
            else:
                from tools.fleet.air_filter.proposal import build_proposal
            return {'ok': True, 'proposal': build_proposal()}
        if request['action'] == 'validate_add':
            if request.get('work_order_type') == 'GREASING':
                from tools.fleet.greasing.proposal import resolve_items
                return {'ok':True, 'items':resolve_items(request['machine_codes'])}
            from tools.fleet.air_filter.proposal import read_source, rule_for
            from tools.fleet.work_orders.types.air_filter.builder import get_items
            machines, *_ = read_source()
            known = {m['code'] for m in machines if not m.get('duplicate') and rule_for(m['code'],m['name']) is not None}
            if any(c not in known for c in request['machine_codes']):
                raise ValueError('دستگاه ناشناخته، تکراری در منبع یا بیل در فهرست اضافه‌کردن مجاز نیست.')
            return {'ok': True, 'items': get_items(request['machine_codes'])}
        if request["action"] == "confirm_review":
            review = confirm_document_review(request["work_order_no"], actor.bale_id)
            order = service.get_work_order(request['work_order_no'])
            return {"ok": True, "review": review, 'work_order_type':order['work_order_type']}
        if request["action"] in {"preview", "edit"}:
            order = service.get_work_order(request["work_order_no"])
            if order["created_by"] != f"bale:{actor.bale_id}":
                raise ValueError("این حکم متعلق به حساب شما نیست.")
            if request["action"] == "edit":
                if order["status"] != "FILE_READY":
                    raise ValueError("اصلاح فقط پیش از انتخاب سرویسکار امکان‌پذیر است.")
                return {"ok": True, "work_order_type": order["work_order_type"], 'proposal': {
                    'work_order_type':order['work_order_type'],
                    'plan_date':order['jalali_date'], 'cutoff':'نسخهٔ اصلاحی حکم قبلی',
                    'source_sha256':None, 'items':order['items'], 'warnings':[], 'review':[]}}
            return {"ok": True, "order": preview_order(order)}
        work_order_type = request["work_order_type"]
        if request["action"] == "validate_machines":
            items = service._load_builder(work_order_type).get_items(request["machine_codes"])
            return {"ok": True, "machine_codes": [item["machine_code"] for item in items]}
        if request["action"] != "create":
            raise ValueError("درخواست نامعتبر است.")
        proposal = request.get('proposal')
        if proposal and proposal.get('source_sha256'):
            import hashlib
            if work_order_type == 'GREASING':
                from tools.fleet.greasing.source import SOURCE
            else:
                from tools.fleet.air_filter.proposal import SOURCE
            if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != proposal['source_sha256']:
                raise ValueError('فایل اطلاعات سرویس تغییر کرده است؛ با «حکم کار» پیشنهاد جدید بگیرید.')
        order = service.create_work_order(
            work_order_type=work_order_type,
            jalali_date=request["jalali_date"],
            shift=("روزانه" if work_order_type == "GREASING" and request.get("shift") == "روزانه"
                   else normalize_shift(request["shift"])),
            machine_codes=request["machine_codes"],
            created_by=f"bale:{actor.bale_id}",
            item_actions=request.get('item_actions'),
            notes=(work_order_type + '_PROPOSAL: ' + json.dumps(proposal, ensure_ascii=False)) if proposal else None,
        )
        return {"ok": True, "order": preview_order(order)}
    except WorkOrderPermissionDenied:
        return {"ok": False, "error": "DENIED", "message": "شما اجازهٔ مدیریت حکم کار را ندارید."}
    except ValueError as exc:
        return {"ok": False, "error": "INVALID_INPUT", "message": str(exc)}
    except Exception:
        # Do not expose server paths or auto-retry an uncertain write.
        logging.getLogger(__name__).exception("Work-order worker failed")
        return {"ok": False, "error": "CREATE_FAILED", "message": "عملیات با خطا روبه‌رو شد. پیش از ساخت دوباره، وضعیت حکم باید بررسی شود."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Explicit test paths are command-line-only; message JSON cannot override
    # production paths. The live handler never passes these switches.
    parser.add_argument("--test-db", type=Path)
    parser.add_argument("--test-output", type=Path)
    args = parser.parse_args()
    if bool(args.test_db) != bool(args.test_output):
        parser.error("Both isolated test paths are required together.")
    if args.test_db:
        from tools.fleet.work_orders.core import db
        db.DB_PATH = args.test_db
        service.WORK_ORDER_OUTPUT_ROOT = args.test_output
    request = json.load(sys.stdin)
    print(json.dumps(execute_request(request, db_path=args.test_db), ensure_ascii=False))


if __name__ == "__main__":
    main()

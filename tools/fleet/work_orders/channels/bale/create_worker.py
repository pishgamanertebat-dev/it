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
from tools.fleet.work_orders.core.oil_safety import RELIABLE_TYPES


def preview_order(order):
    return {"work_order_no": order["work_order_no"], "label": order["work_order_label_fa"],
            "work_order_type": order['work_order_type'],
            "item_summary": '؛ '.join(f"{item['machine_code']}: {item['action_text']}" for item in order['items']) if order['work_order_type'] == 'OIL_CHANGE' else '',
            "item_count": len(order["items"]), "status": order["status"],
            "file_name": Path(order["excel_path"]).name, "file_path": order["excel_path"]}


def execute_request(request: dict, *, db_path=None) -> dict:
    try:
        action = request.get('action')
        required = {'confirm_review': ('work_orders.review',),
                    'preview': ('work_orders.read_own',),
                    'preview_latest': ('work_orders.read_own',),
                    'resume_dispatch': ('work_orders.read_own','work_orders.send','work_orders.approve'),
                    'delivery_pending': ('work_orders.read_own','work_orders.send','work_orders.approve'),
                    'delivery_resolve': ('work_orders.read_own','work_orders.send','work_orders.approve'),
                    'reissue': ('work_orders.read_own','work_orders.create','work_orders.approve'),
                    'edit': ('work_orders.read_own', 'work_orders.create')}.get(action, ('work_orders.create',))
        actor = require_work_order_permission(request.get("bale_id"), db_path=db_path, capabilities=required)
        def authorize_create():
            require_work_order_permission(actor.bale_id, db_path=db_path,
                capabilities=required if action == 'reissue' else ('work_orders.create',))
        if action == 'resume_dispatch':
            from tools.fleet.work_orders.core.db import connect_db
            from tools.fleet.work_orders.core.oil_safety import current
            con = connect_db()
            try:
                row = current(con,request['work_order_no'])
                dispatch = con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?',(request['work_order_no'],)).fetchone()
                if row['work_order_type'] not in RELIABLE_TYPES or row['created_by'] != f'bale:{actor.bale_id}' or row['status'] not in {'APPROVED','SENT'} or not dispatch or dispatch['state'] not in {'READY','FAILED','SENT'}:
                    raise ValueError('این حکم آمادهٔ ادامه نیست؛ ابتدا «بررسی ارسال» را باز کنید.')
                return {'ok':True,'work_order_type':row['work_order_type']}
            finally:
                con.close()
        if action in {'delivery_pending','delivery_resolve'}:
            from tools.fleet.work_orders.core import oil_safety
            from tools.fleet.work_orders.core.db import connect_db
            con = connect_db()
            try:
                oil_safety.migrate(con)
                with con:
                    con.execute('BEGIN IMMEDIATE')
                    if action == 'delivery_pending':
                        rows = oil_safety.pending(con,actor.bale_id,request.get('chat_id'))
                        from datetime import datetime
                        from zoneinfo import ZoneInfo
                        lines = ['بررسی ارسال حکم کار — نتیجهٔ انتقال نامعلوم است؛ پیش از تصمیم با سرویسکار تطبیق دهید.']
                        for row in rows:
                            number, attempt = row['work_order_no'], row['attempt_id']
                            started = datetime.fromtimestamp(row['claimed_at'], ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M')
                            lines.append(f"{number} | {row['staff_name'] or row['recipient_id']} | تلاش {attempt} | {started}\nمرحله: {'انتقال شروع شده' if row['transfer_started_at'] else 'بدون شروع انتقال ثبت‌شده'}\nرسیده است {number} {attempt} علت بررسی\nارسال مجدد مجاز است {number} {attempt} علت بررسی")
                        if not rows:
                            lines.append('ارسال نیازمند بررسی ندارید.')
                        lines.append('پس از ثبت مجوز ارسال مجدد، بنویسید: ادامه ارسال شماره‌حکم. هیچ پیام قبلی بله به‌طور خودکار بررسی نمی‌شود.')
                        return {'ok':True,'message':'\n\n'.join(lines)}
                    state = oil_safety.resolve(con,request['work_order_no'],int(request['attempt_id']),actor.bale_id,request.get('chat_id'),request['decision'],request.get('reason',''))
                    message = ('رسیدن حکم ثبت شد؛ ارسال مجدد مسدود است.' if state == 'SENT' else
                               'مجوز ثبت شد؛ برای انتخاب سرویسکار و ارسال بنویسید: ادامه ارسال ' + request['work_order_no'] if state == 'READY' else 'این تصمیم قبلاً ثبت شده است؛ ارسال جدیدی انجام نشد.')
                    return {'ok':True,'message':message}
            finally:
                con.close()
        if action == 'reissue':
            old = service.get_work_order(request['work_order_no'])
            if old['created_by'] != f'bale:{actor.bale_id}' or old['work_order_type'] not in RELIABLE_TYPES:
                raise ValueError('صدور مجدد فقط برای حکم متعلق به خودتان مجاز است.')
            if not str(request.get('reason','')).strip():
                raise ValueError('علت صدور مجدد الزامی است.')
            order = service.create_work_order(authorization_check=authorize_create,
                work_order_type=old['work_order_type'],jalali_date=old['jalali_date'],shift=old['shift'],
                machine_codes=[i['machine_code'] for i in old['items']],
                item_actions={i['machine_code']:i['action_code'] for i in old['items']},
                created_by=f'bale:{actor.bale_id}',revision_of=old['work_order_no'],
                reissue_reason=request['reason'],operation_id=request.get('operation_id'))
            return {'ok':True,'orders':[preview_order(order)]}
        if request['action'] == 'propose':
            if request.get('work_order_type') == 'OIL_CHANGE':
                from tools.fleet.oil_change.proposal import build_proposal
            elif request.get('work_order_type') == 'GREASING':
                from tools.fleet.greasing.proposal import build_proposal
            else:
                from tools.fleet.air_filter.proposal import build_proposal
            return {'ok': True, 'proposal': build_proposal()}
        if request['action'] == 'validate_add':
            if request.get('work_order_type') == 'OIL_CHANGE':
                from tools.fleet.oil_change.proposal import resolve_items
                return {'ok': True, 'items': resolve_items(request['machine_codes'])}
            if request.get('work_order_type') == 'GREASING':
                from tools.fleet.greasing.proposal import resolve_items
                return {'ok':True, 'items':resolve_items(request['machine_codes'])}
            from tools.fleet.air_filter.proposal import read_source, rule_for
            from tools.fleet.work_orders.types.air_filter.builder import get_items
            machines, *_ = read_source()
            known = {m['code'] for m in machines if not m.get('duplicate') and rule_for(m['code'],m['name']) is not None}
            if any(c not in known for c in request['machine_codes']):
                raise ValueError('دستگاه ناشناخته یا تکراری در منبع است؛ برای بیل و لودر کد کامل EX یا W را وارد کنید.')
            return {'ok': True, 'items': get_items(request['machine_codes'], source_machines=machines)}
        if request["action"] == "confirm_review":
            review = confirm_document_review(request["work_order_no"], actor.bale_id)
            order = service.get_work_order(request['work_order_no'])
            return {"ok": True, "review": review, 'work_order_type':order['work_order_type']}
        if request['action'] == 'preview_latest':
            from tools.fleet.work_orders.core.db import connect_db
            con = connect_db()
            try:
                columns = {r[1] for r in con.execute('PRAGMA table_info(service_work_orders)')}
                version_filter = ' AND superseded_by IS NULL' if 'superseded_by' in columns else ''
                row = con.execute("SELECT work_order_no FROM service_work_orders WHERE created_by=? AND status='FILE_READY'" + version_filter + ' ORDER BY id DESC LIMIT 1', (f'bale:{actor.bale_id}',)).fetchone()
            finally:
                con.close()
            if not row:
                raise ValueError('حکم آمادهٔ بررسی برای ارسال مجدد ندارید.')
            request = {**request, 'action': 'preview', 'work_order_no': row['work_order_no']}
        if request["action"] in {"preview", "edit"}:
            order = service.get_work_order(request["work_order_no"])
            if order.get('superseded_by'):
                raise ValueError('این نسخه منسوخ است؛ نسخهٔ معتبر: ' + order['superseded_by'])
            if order["created_by"] != f"bale:{actor.bale_id}":
                raise ValueError("این حکم متعلق به حساب شما نیست.")
            if order['status'] not in ({'FILE_READY','ASSIGNED','APPROVED'} if request['action']=='preview' else {'FILE_READY'}):
                raise ValueError('این حکم دیگر در مرحلهٔ بررسی فایل نیست.')
            if request['action']=='preview' and order['status']!='FILE_READY':
                from tools.fleet.work_orders.core.db import connect_db
                con=connect_db()
                try:
                    d=con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?',(order['work_order_no'],)).fetchone()
                    if d and d['state'] in {'SENDING','UNCERTAIN'}:
                        raise ValueError('ابتدا نتیجهٔ ارسال قبلی را در «بررسی ارسال» تعیین تکلیف کنید.')
                finally:con.close()
            if request["action"] == "edit":
                if order["status"] != "FILE_READY":
                    raise ValueError("اصلاح فقط پیش از انتخاب سرویسکار امکان‌پذیر است.")
                if order['work_order_type'] == 'OIL_CHANGE':
                    from tools.fleet.oil_change.proposal import build_proposal
                    proposal = build_proposal()
                    available = {i['machine_code']:i for i in proposal['evaluations']}
                    from tools.fleet.oil_change.proposal import override_interval
                    from tools.fleet.work_orders.types.oil_change.builder import parse_action
                    proposal['items'] = []
                    proposal['service_overrides'] = {}
                    for stored in order['items']:
                        code = stored['machine_code']
                        if code not in available:
                            raise ValueError('دستگاه حکم قبلی در برنامه‌ریزی فعلی معتبر نیست؛ پیشنهاد جدید بگیرید.')
                        current = available[code]
                        model, interval = parse_action(stored['action_code'])
                        if model != parse_action(current['action_code'])[0]:
                            raise ValueError('مدل دستگاه نسبت به حکم قبلی تغییر کرده است؛ پیشنهاد جدید بگیرید.')
                        if stored['action_code'] != current['action_code']:
                            current = override_interval(current, interval)
                            proposal['service_overrides'][code] = interval
                        proposal['items'].append(current)
                    proposal['revision_of'] = order['work_order_no']
                    proposal['plan_date'] = order['jalali_date']
                    return {'ok': True, 'work_order_type': 'OIL_CHANGE', 'proposal': proposal}
                return {"ok": True, "work_order_type": order["work_order_type"], 'proposal': {
                    'work_order_type':order['work_order_type'],
                    'plan_date':order['jalali_date'], 'cutoff':'نسخهٔ اصلاحی حکم قبلی',
                    'source_sha256':None, 'items':order['items'], 'warnings':[], 'review':[],
                    'revision_of':order['work_order_no']}}
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
            if work_order_type == 'OIL_CHANGE':
                from tools.fleet.oil_change.source import source_hash
                current_hash = source_hash()
            elif work_order_type == 'GREASING':
                from tools.fleet.greasing.source import SOURCE
                current_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
            else:
                from tools.fleet.air_filter.proposal import SOURCE
                current_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
            if current_hash != proposal['source_sha256']:
                raise ValueError('فایل اطلاعات سرویس تغییر کرده است؛ با «حکم کار» پیشنهاد جدید بگیرید.')
        if work_order_type == 'OIL_CHANGE' and proposal:
            from tools.fleet.oil_change.source import read_source
            from tools.fleet.oil_change.proposal import resolve_items
            source = read_source()
            if source['sha256'] != proposal.get('source_sha256'):
                raise ValueError('فایل اطلاعات سرویس تغییر کرده است؛ پیشنهاد جدید بگیرید.')
            items = resolve_items(request['machine_codes'], source)
            from tools.fleet.oil_change.proposal import override_interval
            overrides = proposal.get('service_overrides', {})
            selected = {item['machine_code'] for item in items}
            if not isinstance(overrides, dict) or not set(overrides).issubset(selected):
                raise ValueError('ویرایش نوبت سرویس باید فقط برای دستگاه‌های همین حکم باشد.')
            items = [override_interval(item, overrides[item['machine_code']])
                     if item['machine_code'] in overrides else item for item in items]
            actions = {i['machine_code']:i['action_code'] for i in items}
            if actions != request.get('item_actions'):
                raise ValueError('نوبت سرویس با برنامه‌ریزی فعلی تطابق ندارد؛ پیشنهاد جدید بگیرید.')
            if proposal.get('revision_of'):
                parent = service.get_work_order(proposal['revision_of'])
                if parent['items'][0]['machine_code'] not in selected:
                    raise ValueError('دستگاه حکم قبلی باید در نسخهٔ اصلاحی باقی بماند؛ برای حذف کامل حکم تصمیم جداگانه لازم است.')
            orders = []
            for index, item in enumerate(items):
                try:
                    # Each machine has its own number, workbook, review and dispatch.
                    order = service.create_work_order(
                        authorization_check=authorize_create,
                        work_order_type='OIL_CHANGE', jalali_date=request['jalali_date'], shift='روزانه',
                        revision_of=(proposal.get('revision_of') if proposal.get('revision_of') and item['machine_code'] == service.get_work_order(proposal['revision_of'])['items'][0]['machine_code'] else None),
                        machine_codes=[item['machine_code']],created_by=f'bale:{actor.bale_id}',
                        item_actions={item['machine_code']:item['action_code']},
                        notes='OIL_CHANGE_PROPOSAL: ' + json.dumps({**proposal,'item':item},ensure_ascii=False))
                    orders.append(preview_order(order))
                except Exception:
                    logging.getLogger(__name__).exception('Oil order batch stopped at %s', item['machine_code'])
                    if not orders:
                        raise
                    return {'ok':True, 'orders':orders, 'batch_error':
                            'ساخت ادامهٔ حکم‌ها متوقف شد. پیش از تلاش دوباره وضعیت این دستگاه‌ها بررسی شود: ' +
                            '، '.join(i['machine_code'] for i in items[index:])}
            return {'ok':True, 'orders':orders}
        order = service.create_work_order(
            authorization_check=authorize_create,
            work_order_type=work_order_type,
            jalali_date=request["jalali_date"],
            shift=("روزانه" if work_order_type in {"GREASING", "OIL_CHANGE"} and request.get("shift") == "روزانه"
                   else normalize_shift(request["shift"])),
            machine_codes=request["machine_codes"],
            created_by=f"bale:{actor.bale_id}",
            item_actions=request.get('item_actions'),
            revision_of=proposal.get('revision_of') if proposal and work_order_type in RELIABLE_TYPES else None,
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

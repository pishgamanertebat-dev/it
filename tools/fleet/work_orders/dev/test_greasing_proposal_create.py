from unittest.mock import patch
from tools.fleet.work_orders.dev.test_bale_work_order_create import WorkOrderCreateTests
from tools.fleet.work_orders.dev.test_permissions import test_database
from tools.fleet.work_orders.channels.bale.create_worker import execute_request
from tools.fleet.work_orders.core import service


class GreasingIdentityTests(WorkOrderCreateTests):
    def test_lowercase_water_truck_never_links_to_uppercase_white_truck(self):
        with test_database(self.db_path) as con:
            con.execute("INSERT INTO machines(canonical_code) VALUES ('S1')")
        order=service.create_work_order(work_order_type='GREASING',jalali_date='1405/06/16',shift='صبح',
            machine_codes=['s1','HD714'],item_actions={'s1':'GREASING_FULL','HD714':'GREASING_FULL'},
            created_by='bale:455740857')
        self.assertEqual(order['items'][0]['machine_code'],'s1')
        self.assertIsNone(order['items'][0]['machine_id'])
        self.assertIsNotNone(order['items'][1]['machine_id'])

    def test_changed_greasing_source_blocks_creation(self):
        with patch.object(service,'create_work_order') as create:
            result=execute_request({'action':'create','bale_id':'455740857','work_order_type':'GREASING',
                                    'proposal':{'source_sha256':'outdated'}},db_path=self.db_path)
        self.assertEqual(result['error'],'INVALID_INPUT')
        create.assert_not_called()

    def test_daily_shift_is_accepted_only_for_greasing(self):
        request={'action':'create','bale_id':'455740857','work_order_type':'GREASING',
                 'machine_codes':['HD714'],'item_actions':{'HD714':'GREASING_FULL'},
                 'jalali_date':'1405/06/16','shift':'روزانه'}
        with patch.object(service,'create_work_order',return_value={
                'work_order_no':'GR-1405-06-16-001','work_order_label_fa':'گریس‌کاری','work_order_type':'GREASING',
                'items':[{}],'status':'FILE_READY','excel_path':'GR.xlsx'}) as create:
            self.assertTrue(execute_request(request,db_path=self.db_path)['ok'])
        self.assertEqual(create.call_args.kwargs['shift'],'روزانه')
        request['work_order_type']='AIR_FILTER'
        request['item_actions']={'HD714':'AIR_FILTER_OUTER'}
        result=execute_request(request,db_path=self.db_path)
        self.assertEqual(result['error'],'INVALID_INPUT')

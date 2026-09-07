import unittest
from tools.fleet.air_filter.proposal import evaluate_machine, build_proposal
from tools.fleet.air_filter.rules import BOTH, OUTER


def day(md,hours=0,inner=None,outer=None):
    return dict(date=md,hours=hours,inner=inner,outer=outer)


class ProposalTests(unittest.TestCase):
    def evaluate(self, name, daily, code='710', cutoff=(6,8),plan=(6,9)):
        return evaluate_machine(dict(code=code,name=name,daily=daily),cutoff,plan)

    def test_all_hour_thresholds_and_alert_boundaries(self):
        for name,code,outer,oa,inner,ia in [('دامپتراک','710',20,17,100,90),('کامیون سهند زرد','S1',10,7,50,45),('کامیون آب پاش','TA1',10,7,50,45),('ژنراتور','DG1',10,7,50,45),('خاور','TR1',10,7,10,7)]:
            for field,due,alert in [('outer',outer,oa),('inner',inner,ia)]:
                for hours,state in [(alert-1,'OK'),(alert,'NEAR_DUE'),(due-1,'NEAR_DUE'),(due,'DUE')]:
                    with self.subTest(name=name,field=field,hours=hours):
                        r=self.evaluate(name,[day((6,1),24,inner='صبح'),day((6,8),hours)],code)
                        self.assertEqual(r['components'][field]['state'],state)

    def test_cross_month_excludes_service_day_and_future(self):
        r=self.evaluate('دامپتراک',[day((5,31),24,inner='صبح'),day((6,1),11),day((6,8),9),day((6,9),100,inner='ظهر')])
        self.assertEqual(r['components']['outer']['value'],20)
        self.assertEqual(r['action_code'],OUTER)

    def test_inner_service_resets_outer_and_due_includes_both(self):
        r=self.evaluate('دامپتراک',[day((5,1),inner='صبح'),day((5,2),100),day((6,8),outer='صبح')])
        self.assertEqual(r['action_code'],BOTH)
        r=self.evaluate('دامپتراک',[day((5,1),outer='صبح'),day((6,7),inner='شب'),day((6,8),3)])
        self.assertEqual(r['components']['outer']['value'],3)

    def test_calendar_overdue_repeats_and_no_baseline_is_visible(self):
        for name,code in [('مزدا','MZ1'),('پیکاپ ریچ','PR2')]:
            for elapsed in [2,3,5]:
                r=self.evaluate(name,[day((6,9-elapsed),outer='ظهر')],code)
                self.assertEqual(r['action_code'],OUTER)
            r=self.evaluate(name,[],code)
            self.assertTrue(r['issues'])
            self.assertIsNone(r['action_code'])

    def test_unknown_service_and_invalid_hours_are_not_events_or_zero(self):
        r=self.evaluate('ژنراتور',[day((5,8),inner='صبح'),day((5,9),4,outer='ژنراتور'),day((6,8),20)],'DG1')
        self.assertEqual(r['components']['outer']['last_service'],'1405/05/08')
        self.assertEqual(r['components']['outer']['state'],'NEEDS_REVIEW')
        self.assertTrue(r['issues'])
        for bad in [-1,True,float('nan'),'نامشخص']:
            r=self.evaluate('دامپتراک',[day((6,1),inner='صبح'),day((6,8),bad)])
            self.assertEqual(r['components']['outer']['state'],'NEEDS_REVIEW')

    def test_excavator_excluded_and_inner_warning_does_not_replace_outer_due(self):
        self.assertTrue(self.evaluate('بیل',[day((6,8),100)],'231')['excluded'])
        r=self.evaluate('دامپتراک',[day((5,1),inner='صبح'),day((5,2),70),day((6,1),outer='صبح'),day((6,8),20)])
        self.assertEqual(r['components']['inner']['state'],'NEAR_DUE')
        self.assertEqual(r['action_code'],OUTER)

    def test_real_source_target_and_quality(self):
        p=build_proposal()
        self.assertEqual(p['cutoff'],'1405/06/08')
        self.assertEqual(p['plan_date'],'1405/06/09')
        self.assertIn('S3',{r['code'] for r in p['review']})
        self.assertIn('DG1',{r['code'] for r in p['review']})
        self.assertNotIn('231',{i['machine_code'] for i in p['items']})

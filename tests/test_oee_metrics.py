"""Hand-calculated examples and failure cases for the new count-based module."""
from copy import deepcopy
from decimal import Decimal
import unittest

from pak_maintenance.common import ApiError
from pak_maintenance.oee_metrics import aggregate,calculate,facts,normalize_shift


class OeeMetricsCase(unittest.TestCase):
    def setUp(self):
        self.profiles={'p':dict(name='محصول',idealRate=10),'q':dict(name='محصول دوم',idealRate=5)}
        self.body=dict(start='08:00',end='16:00',products=[dict(profileId='p',total=3000,rejected=30)],stops=[
            dict(start='08:00',end='09:00',kind='planned',reason='CIP',componentIds=[]),
            dict(start='10:00',end='10:30',kind='technical',reason='خرابی',componentIds=['a','b']),
            dict(start='12:00',end='12:30',kind='nontechnical',reason='انتظار محصول',componentIds=[])])

    def clean(self,body=None):
        return normalize_shift(body or self.body,self.profiles,{'a':{},'b':{}})

    def test_hand_calculated_company_timebase_includes_cip_and_nontechnical(self):
        m=calculate(self.clean())
        self.assertEqual((m['productionMinutes'],m['runMinutes'],m['technicalMinutes'],m['failureCount']),(480,450,30,1))
        self.assertEqual(m['technicalStopToRunPercent'],Decimal(100)*30/450)
        self.assertEqual(m['technicalAvailability'],Decimal(100)*450/480)
        self.assertEqual(m['oee'],Decimal(100)*297/480)
        self.assertEqual(m['technicalOee'],Decimal(100)*297/480)
        self.assertEqual(m['countQuality'],99)
        self.assertEqual((m['mtbfMinutes'],m['mttrRestorationMinutes']),(450,30))

    def test_multi_product_weighted_quality_matches_oee_identity(self):
        self.body['products']=[dict(profileId='p',total=1000,rejected=0),dict(profileId='q',total=1000,rejected=100)]
        m=calculate(self.clean())
        self.assertEqual(m['idealMinutes'],300)
        self.assertEqual(m['idealGoodMinutes'],280)
        self.assertEqual(m['countQuality'],95)
        self.assertNotEqual(m['quality'],m['countQuality'])
        self.assertAlmostEqual(m['availability']*m['performance']*m['quality']/10000,m['oee'],places=20)

    def test_unknown_quality_or_rate_is_not_zero_or_perfect(self):
        self.body['products'][0]['rejected']=None
        m=calculate(self.clean());self.assertIsNone(m['oee']);self.assertIsNone(m['countQuality'])
        self.body['products'][0]['rejected']=0;self.profiles['p']['idealRate']=None
        m=calculate(self.clean());self.assertIsNone(m['performance']);self.assertEqual(m['countQuality'],100)

    def test_overnight_stops_and_adjacent_intervals(self):
        self.body.update(start='20:00',end='08:00',stops=[dict(start='23:45',end='00:15',kind='technical',reason='خرابی'),dict(start='00:15',end='00:30',kind='planned',reason='CIP')])
        clean=self.clean();self.assertTrue(clean['nextDay']);self.assertEqual(calculate(clean)['runMinutes'],690)

    def test_overlap_even_across_categories_is_rejected(self):
        self.body['stops'][1].update(start='08:30',end='09:30')
        with self.assertRaises(ApiError):self.clean()

    def test_outside_shift_zero_duration_or_foreign_component_rejected(self):
        for update in [dict(start='07:30'),dict(end='10:00'),dict(componentIds=['foreign'])]:
            b=deepcopy(self.body);b['stops'][1].update(update)
            with self.subTest(update=update),self.assertRaises(ApiError):self.clean(b)

    def test_no_failure_does_not_mean_mtbf_zero(self):
        self.body['stops']=[]
        m=calculate(self.clean());self.assertIsNone(m['mtbfMinutes']);self.assertIsNone(m['mttrRestorationMinutes']);self.assertEqual(m['technicalStopToRunPercent'],0)

    def test_full_stopped_shift_cannot_have_positive_output(self):
        self.body['stops']=[dict(start='08:00',end='16:00',kind='technical',reason='خرابی')]
        with self.assertRaises(ApiError):calculate(self.clean())
        self.body['products'][0].update(total=0,rejected=0)
        m=calculate(self.clean());self.assertEqual(m['runMinutes'],0);self.assertIsNone(m['technicalStopToRunPercent'])

    def test_aggregate_is_ratio_of_sums_not_mean_of_percentages(self):
        a=facts(self.clean()); b=deepcopy(a);b.update(productionMinutes=60,runMinutes=60,technicalMinutes=0,nontechnicalMinutes=0,plannedMinutes=0,shiftMinutes=60,failureCount=0,total=600,good=600,idealMinutes=Decimal(60),idealGoodMinutes=Decimal(60))
        m=aggregate([a,b]);self.assertEqual(m['oee'],Decimal(35700)/540);self.assertEqual(m['mtbfMinutes'],510)

    def test_invalid_counts_nonfinite_rate_and_equal_shift_rejected(self):
        for update in [dict(start='08:00',end='08:00'),dict(products=[dict(profileId='p',total=10,rejected=11)]),dict(products=[dict(profileId='p',total=True,rejected=0)])]:
            b=deepcopy(self.body);b.update(update)
            with self.subTest(update=update),self.assertRaises(ApiError):self.clean(b)
        self.profiles['p']['idealRate']='NaN'
        with self.assertRaises(ApiError):self.clean()

    def test_over_100_performance_is_flagged_not_clamped(self):
        self.body['products'][0]['total']=10000
        m=calculate(self.clean());self.assertGreater(m['performance'],100);self.assertTrue(m['warnings'])

    def test_confirmed_zero_production_is_zero_oee_not_missing_data(self):
        self.body['products'][0].update(total=0,rejected=0)
        m=calculate(self.clean())
        self.assertEqual((m['total'],m['good'],m['rejected'],m['oee'],m['performance']),(0,0,0,0,0))
        self.assertIsNone(m['countQuality'])
        self.body['products']=[]
        self.assertIsNone(calculate(self.clean())['oee'])

    def test_count_and_runtime_totals_exact_across_shifts_with_confirmed_zero(self):
        first=self.clean();second=deepcopy(first)
        second['products'][0].update(total=0,rejected=0,good=0)
        m=aggregate([facts(first),facts(second)])
        self.assertEqual((m['total'],m['good'],m['rejected'],m['runMinutes']),(3000,2970,30,900))
        self.assertEqual(m['oee'],Decimal(29700)/960)


    def test_eight_hour_shift_with_cip_and_failure_matches_company_example(self):
        self.body['stops']=[
            dict(start='08:00',end='09:00',kind='planned',reason='CIP'),
            dict(start='10:00',end='11:00',kind='technical',reason='خرابی')]
        m=calculate(self.clean())
        self.assertEqual((m['shiftMinutes'],m['plannedMinutes'],m['runMinutes']),(480,60,420))
        self.assertEqual(m['availability'],Decimal('87.5'))
        self.assertEqual(m['technicalAvailability'],Decimal('87.5'))
        self.assertEqual(m['formulaVersion'],'pak-oee-count-3')

    def test_full_cip_shift_is_operating_time_and_not_a_failure(self):
        self.body['stops']=[dict(start='08:00',end='16:00',kind='planned',reason='CIP')]
        self.body['products'][0].update(total=0,rejected=0)
        m=calculate(self.clean())
        self.assertEqual((m['runMinutes'],m['plannedMinutes'],m['failureCount']),(480,480,0))
        self.assertEqual((m['availability'],m['technicalAvailability']),(100,100))
        self.assertIsNone(m['mtbfMinutes'])
        self.assertEqual(m['oee'],0)  # No output is different from availability.

    def test_nontechnical_duration_does_not_reduce_company_availability(self):
        before=calculate(self.clean())
        self.body['stops']=[s for s in self.body['stops'] if s['kind']=='technical']
        after=calculate(self.clean())
        for key in ('runMinutes','availability','technicalAvailability','technicalStopToRunPercent','mtbfMinutes'):
            self.assertEqual(before[key],after[key],key)
        self.assertEqual(before['nontechnicalMinutes'],30)
        self.assertEqual(before['plannedMinutes'],60)
        self.assertEqual(after['nontechnicalMinutes'],0)

    def test_all_nontechnical_shift_is_available_under_company_policy(self):
        self.body['stops']=[dict(start='08:00',end='16:00',kind='nontechnical',reason='انتظار محصول')]
        self.body['products'][0].update(total=0,rejected=0)
        m=calculate(self.clean())
        self.assertEqual((m['nontechnicalMinutes'],m['runMinutes'],m['technicalAvailability']),(480,480,100))

    def test_legacy_cached_times_are_recomputed_for_mixed_period_reports(self):
        current=facts(self.clean())
        legacy=deepcopy(current)
        legacy.update(productionMinutes=420,runMinutes=360)
        original=deepcopy(legacy)
        result=aggregate([legacy,current])
        expected=aggregate([current,current])
        self.assertEqual(result,expected)
        self.assertEqual((result['runMinutes'],result['plannedMinutes'],result['nontechnicalMinutes']),(900,120,60))
        self.assertEqual(legacy,original)  # Persisted facts/evidence are not rewritten.

    def test_legacy_sheet_read_uses_recorded_rates_and_preserves_encoded_evidence(self):
        from pak_maintenance.common import dumps
        from pak_maintenance.oee_service import OeeService
        document=self.clean()
        document['metrics']={'formulaVersion':'pak-oee-count-2','runMinutes':360}
        encoded=dumps(document)
        read=OeeService._sheet(encoded)
        self.assertEqual(read['metrics']['runMinutes'],450)
        self.assertEqual(read['metrics']['formulaVersion'],'pak-oee-count-3')
        self.assertEqual(document['metrics']['runMinutes'],360)
        self.assertEqual(dumps(document),encoded)

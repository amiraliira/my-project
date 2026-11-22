"""New work categories travel through the real scoped API and final reporting."""
import unittest

from pak_maintenance.common import ApiError
import test_api_integration as fixtures


class ActivityApiIntegrationTests(unittest.TestCase):
    password = fixtures.ApiIntegrationTests.password
    setUpClass = classmethod(fixtures.ApiIntegrationTests.setUpClass.__func__)
    setUp = fixtures.ApiIntegrationTests.setUp
    tearDown = fixtures.ApiIntegrationTests.tearDown
    request = fixtures.ApiIntegrationTests.request
    login = fixtures.ApiIntegrationTests.login
    approve = fixtures.ApiIntegrationTests.approve

    def complete(self, kind):
        body = dict(type=kind,title='فعالیت آزمایشی '+kind,description='شرح واقعی فعالیت آزمایشی',
            workDate='2026-10-10',startTime='08:00',endTime='09:00',
            participants=[dict(personId='m1',minutes=60)],
            parts=[dict(partId='part',quantity=1)],checklist=[])
        if kind=='GENERAL':
            body['hall']='سالن آزمایشی'
        else:
            body['assetId']='asset'
        row=self.request('POST','/api/orders',actor='m1',body=body,
            headers={'x-idempotency-key':'activity-api-create-'+kind}).payload
        row=self.request('POST','/api/orders/'+row['id']+'/submit',actor='m1',
            body=dict(version=row['version'])).payload
        row=self.approve('sm',row,[('m1',2)])
        row=self.approve('head',row,coefficient=3)
        self.assertEqual(row['status'],'completed')
        return row

    def test_create_grade_and_drilldown_distinct_categories_with_people_and_parts(self):
        for actor in ('m1','sm','head','outside'):
            self.login(actor)
        orders=[self.complete(kind) for kind in ('CM','GENERAL')]
        query=dict(**{'from':'2026-10-01','to':'2026-10-31'},type='ALL',groupBy='hall',metric='activityCount')
        summary=self.request('GET','/api/reports/activities',actor='head',query=query).payload
        self.assertEqual(summary['totalOrders'],2)
        self.assertEqual(summary['totals']['workMinutes'],120)
        self.assertEqual(summary['typeCounts'],dict(PM=0,EM=0,CM=1,PROJECT=0,GENERAL=1))
        group=summary['items'][0]
        self.assertEqual(group['hall'],'سالن آزمایشی')
        detail=self.request('GET','/api/reports/activities/orders',actor='head',
            query={**query,'hall':group['hall']}).payload
        self.assertEqual({o['id'] for o in detail['orders']},{o['id'] for o in orders})
        for order in detail['orders']:
            self.assertEqual(order['description'],'شرح واقعی فعالیت آزمایشی')
            self.assertEqual(order['participants'][0]['personId'],'m1')
            self.assertTrue(order['participants'][0]['personName'])
            self.assertEqual(order['parts'][0]['partId'],'part')
        filtered=self.request('GET','/api/reports/activities',actor='head',
            query={**query,'type':'GENERAL'}).payload
        self.assertEqual(filtered['totalOrders'],1)
        outsider=self.request('GET','/api/reports/activities/orders',actor='outside',
            query={**query,'hall':group['hall']}).payload
        self.assertEqual(outsider['total'],0)
        self.assertEqual(outsider['orders'],[])

    def test_report_authentication_role_and_input_validation_are_enforced(self):
        query={'from':'2026-10-01','to':'2026-10-31'}
        with self.assertRaises(ApiError) as denied:
            self.request('GET','/api/reports/activities',query=query)
        self.assertEqual(denied.exception.status,401)
        self.login('admin');self.login('head')
        with self.assertRaises(ApiError) as denied:
            self.request('GET','/api/reports/activities',actor='admin',query=query)
        self.assertEqual(denied.exception.status,403)
        for changed in ({'type':'INVALID'},{'groupBy':'users'},{'metric':'score'},
                        {'from':'2026-11-01'},{'page':'0'},{'pageSize':'1000000'}):
            with self.subTest(changed=changed),self.assertRaises(ApiError) as rejected:
                self.request('GET','/api/reports/activities',actor='head',query={**query,**changed})
            self.assertEqual(rejected.exception.status,400)


if __name__=='__main__':
    unittest.main()

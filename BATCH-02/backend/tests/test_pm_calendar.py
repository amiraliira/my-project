import json
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
import unittest
from pak_maintenance import pm_calendar as cal,pm_schedule as schedule,pm_form_info as info
from pak_maintenance.common import ApiError
from pak_maintenance.pm_hash_json import canonical_json


class CalendarTests(unittest.TestCase):
    def test_dotnet_persisted_request_hash_bytes_unicode_and_html_sensitive_text(self):
        expected=(Path(__file__).parent/"calendar_reference"/"dotnet-hash-json.json").read_text()
        value=dict(text="سلام <> & ' + ` \" / \\ \b \f \n \r \t \u007f 😀",number=1,yes=True)
        self.assertEqual(canonical_json(value),expected)

    def test_exact_dotnet_month_starts_and_lengths_19608_fixtures(self):
        path=Path(__file__).parent/"calendar_reference"/"dotnet-persian-calendar.json"
        rows=json.loads(path.read_text())
        self.assertEqual(len(rows),19608)
        for r in rows:
            first=cal.from_persian(r["year"],r["month"],1)
            self.assertEqual(first.isoformat(),r["start"],r)
            last=cal.from_persian(r["year"],r["month"],r["days"])
            self.assertEqual(cal.to_persian(last),(r["year"],r["month"],r["days"]))
            with self.assertRaises(ValueError):cal.from_persian(r["year"],r["month"],r["days"]+1)

    def test_reference_provenance_and_far_future_years(self):
        self.assertEqual(cal.from_persian(1405,7,1),date(2026,9,23))
        self.assertEqual(cal.from_persian(1405,8,1),date(2026,10,23))
        # These are actual .NET astronomical dates, not arithmetic guesses.
        self.assertEqual(cal.from_persian(1503,1,1),date(2124,3,20))
        self.assertEqual(cal.to_persian(cal.from_persian(9377,12,29)),(9377,12,29))
        for invalid in (0,9378,True):
            with self.assertRaises(ValueError):cal.get_week(invalid,1,1)

    def test_four_saturdays_cross_month_year_and_exclusive_deadline(self):
        windows=cal.get_year(1405)
        self.assertEqual(len(windows),48)
        for w in windows:
            self.assertEqual(w.start_date.weekday(),5)
            self.assertEqual(w.end_date.weekday(),4)
            self.assertEqual(w.end_exclusive_date-w.start_date,timedelta(days=7))
            self.assertEqual(w.utc_window()[1]-w.utc_window()[0],timedelta(days=7))
        self.assertEqual(cal.get_week(1405,7,1).start_date,date(2026,9,26))
        self.assertTrue(any(cal.to_persian(w.end_date)[1]!=w.slot.month for w in windows))
        self.assertTrue(any(cal.to_persian(w.end_date)[0]!=w.persian_year for w in cal.get_year(1403)))
        w=cal.get_week(1405,7,1);deadline=w.utc_window()[1]
        self.assertEqual(cal.tehran_date(deadline-timedelta(microseconds=1)),w.end_date)
        self.assertEqual(cal.tehran_date(deadline),w.end_exclusive_date)

    def test_enumeration_activation_floor_and_bound(self):
        rows=list(cal.enumerate_active(date(2026,9,27),date(2026,10,10)))
        self.assertEqual([w.start_date for w in rows],[date(2026,10,3),date(2026,10,10)])
        with self.assertRaises(ValueError):list(cal.enumerate_active(cal.from_persian(1400,1,1),cal.from_persian(1402,1,1)))
        with self.assertRaises(ValueError):list(cal.enumerate_active(date(2026,10,10),date(2026,9,27)))

    def test_historical_dst_gap_and_ambiguous_execution_fail_closed(self):
        boundary=cal.utc_boundary(date(2021,3,22))
        self.assertEqual(boundary.astimezone(cal.TEHRAN).date(),date(2021,3,22))
        self.assertEqual(boundary.astimezone(cal.TEHRAN).hour,1)
        with self.assertRaises(ValueError):cal.local_execution_utc(datetime(2021,3,22,0,30))
        with self.assertRaises(ValueError):cal.local_execution_utc(datetime(2021,9,21,23,30))

    def test_continuous_ten_month_and_week_interval_recurrence(self):
        p=dict(recurrence=dict(mode="source-period",unit="month",interval=10,anchorYear=1405,anchorMonth=7,week=2))
        self.assertEqual(schedule.year_slots(p,1405),[dict(month=7,week=2)])
        self.assertEqual(schedule.year_slots(p,1406),[dict(month=5,week=2)])
        self.assertEqual(schedule.year_slots(p,1407),[dict(month=3,week=2)])
        p["recurrence"].update(unit="week",interval=3,anchorMonth=12,week=4)
        self.assertEqual(schedule.slots_for_month(p,1406,1),[cal.MonthSlot(1,3)])
        self.assertEqual(schedule.slots_for_month(p,1405,11),[])

    def test_periods_original_declaration_conflicts_and_search(self):
        for source,category,months in (("ماهانه","monthly",1),("هفتگی","weekly",None),("۱۰ ماهه","ten-monthly",10),("سالانه","annual",12),("یک سال و نیم","multi-year",18),("دوماهه","bimonthly",2)):
            p=schedule.read_period(source);self.assertEqual((p.period_class,p.interval_months),(category,months))
        p=schedule.read_period("۴۰۰ ساعت / ۴۵ روزه")
        self.assertEqual((p.meter_hours,p.interval_days,p.trigger_kind),(400,45,"calendar-and-meter"))
        form=dict(period="نیازمند بررسی",originalPeriodDeclarations=[dict(text="ماهانه"),dict(text="سالانه")])
        self.assertEqual(info.metadata(form)["periodClass"],"monthly")
        self.assertTrue(info.metadata(form)["periodConflict"])
        self.assertEqual(info.normalize_search("پمپ ٣٩ – ۱۲۳ كد ي"),"پمپ 39-123 کد ی")
        with self.assertRaises(ApiError):schedule.normalize(dict(mode="source-period",unit="month",interval=1,anchorYear=1405,anchorMonth=7,week=1),dict(period="۴۰۰ ساعت / ماهانه"))


if __name__=="__main__":unittest.main()

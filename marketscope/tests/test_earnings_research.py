"""Synthetic fixtures are confined to tests; no generated observations enter runtime."""
import copy
from datetime import date, datetime, time, timedelta
import unittest

from marketscope.earnings_research import analyze_event, reaction_session, wilson_interval
from marketscope.market_schedule import IST, get_next_trading_day, is_trading_day


def timestamp(day, clock):
    return int(datetime.combine(day, time.fromisoformat(clock), IST).timestamp())


def candle(day, clock, opening, close, timeframe=5):
    return {'t': timestamp(day, clock), 'o': opening, 'h': max(opening, close) + .05,
            'l': min(opening, close) - .05, 'c': close, 'v': 1000,
            'timeframe': timeframe, 'closed': True, 'source': 'TEST_FIXTURE', 'adjustments_verified': True}


def previous_day(day):
    d = day - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def rows_for(day, direction=1):
    prices = ([100.4, 100.8, 101, 100.7, 100.4, 100.6, 100.9, 101.2, 101.5]
              if direction > 0 else
              [99.6, 99.2, 99, 99.3, 99.6, 99.4, 99.1, 98.8, 98.5])
    rows = [candle(previous_day(day), '09:15', 99, 99, 1440)]
    opening = 100
    for i, close in enumerate(prices):
        minutes = 9 * 60 + 15 + 5 * i
        rows.append(candle(day, f'{minutes // 60:02}:{minutes % 60:02}', opening, close))
        opening = close
    rows.append(candle(day, '09:15', 100, prices[-1], 1440))
    return rows


def event(event_id='target', day=date(2026, 10, 23), period='2026-09-30', growth=20):
    at = datetime.combine(day, time(18), IST).isoformat()
    return {'id': event_id, 'symbol': 'TEST', 'basis': 'consolidated',
            'announced_at': at, 'financials_available_at': at, 'period_end': period,
            'growth': {'revenue_qoq': growth, 'profit_qoq': growth},
            'financials': {'revenue': 120, 'net_profit': 12},
            'previous': {'revenue': 100, 'net_profit': 10}}


def historical_fixture(count=10):
    events, rows = [], []
    day = date(2025, 7, 1)
    # Older accounting quarters announced late: unique period ends prevent two
    # revisions of one quarter being miscounted as independent observations.
    for i in range(count):
        day = get_next_trading_day(day)
        period = f'{2018 + i // 4}-{(3, 6, 9, 12)[i % 4]:02}-30'
        report_day = previous_day(day)
        ev = event(f'history-{i}', report_day, period, 20 if i < 10 else -20)
        events.append(ev)
        rows.extend(rows_for(day, 1 if i < 10 else -1))
        day += timedelta(days=3)
    return events, rows


class TestReactionSession(unittest.TestCase):
    def test_timezone_aware_preopen_and_afterclose(self):
        self.assertEqual(reaction_session('2026-10-26T08:00:00+05:30')['date'], '2026-10-26')
        self.assertEqual(reaction_session('2026-10-23T18:00:00+05:30')['date'], '2026-10-26')
        self.assertEqual(reaction_session('2026-10-26T03:00:00Z')['date'], '2026-10-26')

    def test_holiday_and_weekend_skip(self):
        self.assertEqual(reaction_session('2026-10-01T18:00:00+05:30')['date'], '2026-10-05')
        self.assertEqual(reaction_session('2026-10-24T12:00:00+05:30')['date'], '2026-10-26')

    def test_intraday_is_separate_study(self):
        value = reaction_session('2026-10-26T11:00:00+05:30')
        self.assertFalse(value['eligible'])
        self.assertEqual(value['timing'], 'intraday')

    def test_naive_and_unverified_year_excluded(self):
        self.assertFalse(reaction_session('2026-10-26T08:00:00')['eligible'])
        self.assertFalse(reaction_session('2030-10-26T08:00:00+05:30')['eligible'])

    def test_wilson_small_sample_not_certainty(self):
        low, high = wilson_interval(1, 1)
        self.assertLess(low, 25)
        self.assertAlmostEqual(high, 100)
        self.assertEqual(wilson_interval(0, 0), (None, None))


class TestEarningsStudy(unittest.TestCase):
    def study(self, target=None, history=None, rows=None, now='2026-10-24T12:00:00+05:30'):
        events, candles = historical_fixture()
        return analyze_event(target or event(), history if history is not None else events,
                             {'TEST': rows if rows is not None else candles}, now)

    def test_matching_growth_pattern_and_window_medians(self):
        study = self.study()
        self.assertEqual(study['status'], 'ready')
        self.assertEqual(study['sample_size'], 10)
        self.assertEqual(study['pattern']['observed_count'], 10)
        self.assertAlmostEqual(study['pattern']['windows'][0]['median_return_pct'], 1)
        self.assertLess(study['pattern']['windows'][1]['median_return_pct'], 0)
        self.assertGreater(study['pattern']['windows'][2]['median_return_pct'], 0)
        self.assertEqual(study['setup']['action'], 'WAIT')
        self.assertFalse(study['forecast']['validated_out_of_sample'])

    def test_missing_one_window_does_not_interpolate(self):
        events, rows = historical_fixture()
        one = reaction_session(events[0]['announced_at'])['date']
        rows = [r for r in rows if not (datetime.fromtimestamp(r['t'], IST).date().isoformat() == one
                                        and datetime.fromtimestamp(r['t'], IST).strftime('%H:%M') == '09:35')]
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 9)
        self.assertEqual(study['setup']['action'], 'INSUFFICIENT_DATA')
        self.assertEqual(study['coverage']['excluded']['missing_intraday_windows'], 1)

    def test_exact_previous_close_required(self):
        events, rows = historical_fixture()
        rows = [r for r in rows if r['timeframe'] != 1440]
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 0)
        self.assertEqual(study['coverage']['excluded']['missing_prior_close'], 10)

    def test_late_revision_is_not_historical_knowledge(self):
        events, rows = historical_fixture()
        events[0]['financials_available_at'] = '2026-10-25T18:00:00+05:30'
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 9)
        self.assertEqual(study['coverage']['excluded']['not_known_before_target'], 1)

    def test_unknown_financial_availability_is_excluded(self):
        target = event()
        del target['financials_available_at']
        study = self.study(target=target)
        self.assertEqual(study['setup']['action'], 'INSUFFICIENT_DATA')
        self.assertEqual(study['status'], 'insufficient_data')

    def test_target_figures_known_after_open_block_inference(self):
        target = event()
        target['financials_available_at'] = '2026-10-26T12:00:00+05:30'
        study = self.study(target=target, now='2026-10-26T13:00:00+05:30')
        self.assertEqual(study['status'], 'insufficient_data')

    def test_revision_and_other_basis_not_independent_samples(self):
        events, rows = historical_fixture()
        duplicate = copy.deepcopy(events[0])
        duplicate['id'] = 'revision'
        duplicate['financials_available_at'] = '2025-07-01T07:00:00+05:30'
        events.append(duplicate)
        other = copy.deepcopy(events[1]); other['basis'] = 'standalone'; events.append(other)
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 10)
        self.assertEqual(study['coverage']['excluded']['different_reporting_basis'], 1)

    def test_zero_or_negative_base_is_not_positive_percentage_growth(self):
        target = event(); target['previous']['net_profit'] = -10
        study = self.study(target=target)
        self.assertEqual(study['sample_size'], 0)
        self.assertEqual(study['status'], 'insufficient_data')

    def test_completed_future_outcomes_do_not_leak_into_target(self):
        target = event(day=date(2025, 7, 2), period='2026-09-30')
        study = self.study(target=target)
        self.assertLess(study['sample_size'], 10)
        self.assertGreater(study['coverage']['excluded'].get('not_known_before_target', 0), 0)

    def test_revision_pending_metrics_are_not_reused_as_new_filing(self):
        target = event(); target['financial_status'] = 'revision_pending'
        study = self.study(target=target)
        self.assertEqual(study['status'], 'insufficient_data')
        self.assertEqual(study['sample_size'], 0)
        self.assertEqual(study['setup']['action'], 'INSUFFICIENT_DATA')

    def test_date_only_filing_does_not_become_a_preopen_announcement(self):
        target = event(); target['announcement_time_known'] = False
        study = self.study(target=target)
        self.assertEqual(study['reaction_session']['timing'], 'unknown_time')
        self.assertEqual(study['status'], 'not_applicable')
        events, rows = historical_fixture()
        events[0]['announcement_time_known'] = False
        self.assertEqual(self.study(history=events, rows=rows)['sample_size'], 9)

    def test_growth_cohort_does_not_mix_total_income_and_operating_revenue(self):
        target = event(); target['financials']['revenue_metric'] = 'total_income'
        events, rows = historical_fixture()
        for item in events:
            item['financials']['revenue_metric'] = 'revenue_from_operations'
        study = self.study(target=target, history=events, rows=rows)
        self.assertEqual(study['sample_size'], 0)
        self.assertEqual(study['coverage']['excluded']['different_financial_definition'], 10)

    def test_unverified_adjustment_basis_hides_crosssession_returns(self):
        events, rows = historical_fixture()
        for row in rows:
            row['adjustments_verified'] = False
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 10)
        self.assertTrue(all(row['gap_pct'] is None and row['close_return_pct'] is None for row in study['matched_samples']))
        self.assertEqual(study['daily_study']['close_return_sample_size'], 0)
        self.assertIsNone(study['daily_study']['median_close_return_pct'])
        self.assertGreater(study['daily_study']['median_open_to_close_pct'], 0)

    def test_known_corporate_action_day_is_not_attributed_to_earnings(self):
        events, rows = historical_fixture()
        reaction = reaction_session(events[0]['announced_at'])['date']
        for row in rows:
            if datetime.fromtimestamp(row['t'], IST).date().isoformat() == reaction:
                row['corporate_action'] = True
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 9)
        self.assertEqual(study['daily_study']['sample_size'], 9)
        self.assertEqual(study['coverage']['excluded']['corporate_action_confound'], 1)

    def test_no_data_never_yields_directional_setup(self):
        study = self.study(history=[], rows=[])
        self.assertIsNone(study['pattern']['probability_pct'])
        self.assertIsNone(study['forecast']['median_open_to_1000_pct'])
        self.assertEqual(study['setup']['action'], 'INSUFFICIENT_DATA')

    def test_intraday_target_not_applicable(self):
        target = event(); target['announced_at'] = '2026-10-23T11:00:00+05:30'
        study = self.study(target=target)
        self.assertEqual(study['status'], 'not_applicable')

    def test_baseline_edge_and_fresh_technical_confirmation_required(self):
        events, rows = historical_fixture(20)
        reaction_day = date(2026, 10, 26)
        # A fresh, fully observed opening sequence with a later range break.
        rows += rows_for(reaction_day)
        study = self.study(history=events, rows=rows, now='2026-10-26T09:50:00+05:30')
        self.assertEqual(study['sample_size'], 10)
        self.assertEqual(study['baseline']['sample_size'], 20)
        self.assertEqual(study['setup']['action'], 'WAIT') # 09:45 close below 09:15–09:30 high
        # Give the closed 09:45 bar a genuine range breakout.
        for row in rows:
            if row['t'] == timestamp(reaction_day, '09:45') and row['timeframe'] == 5:
                row['c'] = 101.4; row['h'] = 101.45
        study = self.study(history=events, rows=rows, now='2026-10-26T09:50:00+05:30')
        self.assertEqual(study['setup']['action'], 'BUY_WATCH')
        levels = study['setup']['levels']
        self.assertLess(levels['stop'], levels['entry'])
        self.assertAlmostEqual((levels['target'] - levels['entry']) / (levels['entry'] - levels['stop']), 1.5)
        self.assertFalse(study['setup']['orders_placed'])

    def test_daily_evidence_is_available_without_intraday_inference(self):
        events, rows = historical_fixture()
        rows = [row for row in rows if row['timeframe'] == 1440]
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 0)
        self.assertEqual(study['daily_study']['sample_size'], 10)
        self.assertGreater(study['daily_study']['median_close_return_pct'], 0)
        self.assertEqual(study['setup']['action'], 'INSUFFICIENT_DATA')

    def test_revision_known_after_its_own_reaction_cannot_select_cohort(self):
        events, rows = historical_fixture()
        late = copy.deepcopy(events[0])
        late['id'] = 'restated-after-reaction'
        late['growth'] = {'revenue_qoq': -20, 'profit_qoq': -20}
        late['financials_available_at'] = '2025-08-01T12:00:00+05:30'
        study = self.study(history=events + [late], rows=rows)
        self.assertEqual(study['sample_size'], 10) # original preserved
        self.assertEqual(study['coverage']['excluded']['financials_not_known_before_historical_open'], 1)

    def test_sell_watch_uses_same_evidence_hurdle_and_real_observed_levels(self):
        events, rows = historical_fixture(20)
        target = event(growth=-20)
        reaction_day = date(2026, 10, 26)
        rows += rows_for(reaction_day, -1)
        for row in rows:
            if row['t'] == timestamp(reaction_day, '09:45') and row['timeframe'] == 5:
                row['c'] = 98.6; row['l'] = 98.55
        study = self.study(target=target, history=events, rows=rows, now='2026-10-26T09:50:00+05:30')
        self.assertEqual(study['setup']['action'], 'SELL_WATCH')
        levels = study['setup']['levels']
        self.assertGreater(levels['stop'], levels['entry'])
        self.assertLess(levels['target'], levels['entry'])

    def test_daily_and_forming_candles_never_fill_intraday_gaps(self):
        events, rows = historical_fixture()
        rows = [row for row in rows if row['timeframe'] == 1440]
        study = self.study(history=events, rows=rows)
        self.assertEqual(study['sample_size'], 0)
        events, rows = historical_fixture()
        for row in rows:
            if row['timeframe'] == 5:
                row['closed'] = False
        self.assertEqual(self.study(history=events, rows=rows)['sample_size'], 0)


if __name__ == '__main__':
    unittest.main()

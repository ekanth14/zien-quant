"""Offline financial-ingestion tests. All databases and market data are test fixtures."""
import copy
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from marketscope.results_service import (
    IST, NSEPublicClient, ResultsService, _official_url, apply_financial_rows,
    comparable_growth, normalize_filing, parse_datetime, parse_financial_document,
)


def filing(symbol="TEST", basis="Consolidated", period="30-Sep-2026", stamp="23-Oct-2026 18:10:00"):
    return {"symbol": symbol, "cmName": "Test Fixture Company", "creation_Date": stamp,
            "qe_Date": period, "consolidated": basis, "seq_Id": "fixture-1",
            "xbrl": "https://nsearchives.nseindia.com/corporate/TEST.xml"}


def financial(period, revenue=100, profit=10, basis="consolidated"):
    return {"period_end": period, "basis": basis, "unit": "INR lakh", "revenue_metric": "revenue_from_operations", "revenue": revenue, "net_profit": profit, "eps": 1}


class TestFinancialParsing(unittest.TestCase):
    def test_filings_require_actual_date_period_and_symbol(self):
        event = normalize_filing(filing())
        self.assertEqual(event["announced_at"], "2026-10-23T18:10:00+05:30")
        self.assertEqual(event["basis"], "consolidated")
        self.assertIsNone(normalize_filing({"symbol": "TEST"}))
        self.assertIsNone(normalize_filing(filing(symbol="../../private")))
        self.assertIsNone(normalize_filing(filing(period="31-Dec-2026")))
        self.assertEqual(normalize_filing(filing(basis="Non-Consolidated"))["basis"], "standalone")

    def test_official_document_allowlist_rejects_external_hosts_and_credentials(self):
        self.assertIsNone(_official_url("https://nseindia.com.evil.example/a.xml"))
        self.assertIsNone(_official_url("http://nsearchives.nseindia.com/a.xml"))
        self.assertIsNone(_official_url("https://user:@nseindia.com/a.xml"))
        self.assertIsNone(_official_url("https://nseindia.com:444/a.xml"))
        self.assertTrue(_official_url("https://nsearchives.nseindia.com/a.xml"))

    def test_xbrl_uses_quarter_context_units_scales_and_scope(self):
        content = b'''<xbrli:xbrl xmlns:xbrli="urn:xbrli" xmlns:in="urn:indas" xmlns:iso="urn:currency" xmlns:ix="urn:inline">
          <xbrli:context id="quarter"><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="year"><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:unit id="money"><xbrli:measure>iso:INR</xbrli:measure></xbrli:unit>
          <ix:nonFraction name="in:RevenueFromOperations" contextRef="quarter" unitRef="money" scale="5">120</ix:nonFraction>
          <in:ProfitLossForPeriod contextRef="quarter" unitRef="money">1500000</in:ProfitLossForPeriod>
          <in:RevenueFromOperations contextRef="year" unitRef="money">999999999</in:RevenueFromOperations>
        </xbrli:xbrl>'''
        rows = parse_financial_document(content, normalize_filing(filing()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["revenue"], 120)
        self.assertEqual(rows[0]["net_profit"], 15)
        self.assertEqual(rows[0]["unit"], "INR lakh")
        self.assertEqual(rows[0]["basis"], "consolidated")

    def test_xbrl_never_assumes_unknown_currency_or_converts_annual_to_quarter(self):
        content = b'''<x xmlns:i="urn:i"><context id="q"><startDate>2026-07-01</startDate><endDate>2026-09-30</endDate></context><unit id="usd"><measure>USD</measure></unit><i:RevenueFromOperations contextRef="q" unitRef="usd">100</i:RevenueFromOperations></x>'''
        self.assertEqual(parse_financial_document(content, normalize_filing(filing())), [])
        self.assertEqual(parse_financial_document(b"not structured financial data", normalize_filing(filing())), [])
        with self.assertRaises(ValueError):
            parse_financial_document(b'<!DOCTYPE x [<!ENTITY a "x">]><x/>', normalize_filing(filing()))

    def test_growth_handles_negative_zero_and_incompatible_base(self):
        current = financial("2026-09-30", 120, 15)
        out = comparable_growth(current, financial("2026-06-30", 100, 10))
        self.assertAlmostEqual(out["revenue_qoq"], 20)
        self.assertAlmostEqual(out["profit_qoq"], 50)
        zero = comparable_growth(current, financial("2026-06-30", 0, -10))
        self.assertIsNone(zero["revenue_qoq"])
        self.assertIsNone(zero["profit_qoq"])
        self.assertTrue(any("turnaround" in n for n in zero["notes"]))
        different = comparable_growth(current, financial("2026-06-30", basis="standalone"))
        self.assertIsNone(different["profit_qoq"])
        self.assertTrue(different["notes"])

    def test_exact_previous_quarter_and_year_ago_comparability(self):
        event = normalize_filing(filing())
        rows = [financial("2026-09-30", 120, 15), financial("2026-03-31", 90, 8), financial("2025-09-30", 80, 10)]
        out = apply_financial_rows(event, rows, event["announced_at"], event["source_url"])
        self.assertEqual(out["previous"], {})
        self.assertIsNone(out["growth"]["revenue_qoq"])
        self.assertEqual(out["growth"]["profit_yoy"], 50)
        self.assertEqual(out["financials_available_at"], event["announced_at"])

    def test_comparison_rows_cannot_enrich_consolidated_filings(self):
        event = normalize_filing(filing())
        out = apply_financial_rows(event, [financial("2026-09-30", basis="standalone")], "2026-10-26T12:00:00+05:30", "comparison")
        self.assertEqual(out["financials"], {})

    def test_legacy_shape_and_missing_announcement_time_are_explicit(self):
        raw = {"sm_symbol": "TEST", "companyname": "Test Fixture", "toDate": "30-Sep-2021", "filingDate": "23-Oct-2021", "consol": "true", "seqNumber": 22, "xbrl": "https://nsearchives.nseindia.com/xbrl/-"}
        event = normalize_filing(raw)
        self.assertFalse(event["announcement_time_known"])
        self.assertEqual(event["basis"], "consolidated")
        self.assertEqual(event["company"], "Test Fixture")
        self.assertEqual(event["source_filing_id"], "22")
        self.assertIsNone(event["document_url"])
        client = NSEPublicClient()
        with patch.object(client, "_json", return_value=[raw]) as request:
            self.assertEqual(len(client.filings(date(2021, 10, 1), date(2021, 10, 30))), 1)
            self.assertEqual(request.call_args.args[0], "corporates-financial-results")

    def test_history_chunk_crossing_taxonomy_boundary_queries_both(self):
        client = NSEPublicClient()
        legacy = {"sm_symbol":"TEST","toDate":"30-Sep-2024","filingDate":"23-Dec-2024 18:10:00","consol":"false"}
        modern = filing(stamp="23-Jan-2025 18:10:00",period="31-Dec-2024")
        with patch.object(client, "_json", side_effect=[[legacy], {"data":[modern],"totalCount":1}]) as request:
            rows = client.filings(date(2024,12,15),date(2025,1,30))
        self.assertEqual(len(rows),2)
        self.assertEqual([c.args[0] for c in request.call_args_list], ["corporates-financial-results","integrated-filing-results"])

    def test_integrated_ignored_size_pages_are_not_silently_dropped(self):
        client = NSEPublicClient()
        a,b = filing("TEST"), filing("OTHER")
        with patch.object(client, "_json", side_effect=[{"data":[a],"totalCount":2},{"data":[a],"totalCount":2},{"data":[b],"totalCount":2}]):
            self.assertEqual(len(client.filings(date(2026,10,1),date(2026,10,30))),2)
        with patch.object(client, "_json", return_value={"data":[a],"totalCount":2}):
            with self.assertRaises(ValueError):
                client.filings(date(2026,10,1),date(2026,10,30))

    def test_announced_time_and_precision_come_from_same_timed_field(self):
        raw = filing()
        raw["creation_Date"] = "2026-10-23"
        raw["broadcastDate"] = "23-Oct-2026 18:10:00"
        event = normalize_filing(raw)
        self.assertTrue(event["announcement_time_known"])
        self.assertEqual(event["announced_at"], "2026-10-23T18:10:00+05:30")

    def test_incompatible_profit_definitions_are_not_compared(self):
        current, previous = financial("2026-09-30"), financial("2026-06-30")
        current["profit_metric"] = "profit_for_period"
        previous["profit_metric"] = "profit_continuing_operations"
        self.assertIsNone(comparable_growth(current, previous)["profit_qoq"])

    def test_unsupported_payload_is_failure_not_empty_success(self):
        with self.assertRaises(ValueError):
            NSEPublicClient._records({"error": "blocked"})


class OfflineClient:
    def __init__(self):
        self.down = False
        self.calls = []
    def filings(self, start, end):
        self.calls.append((start, end))
        if self.down:
            raise OSError("Fixture network unavailable")
        return [filing()] if start <= date(2026, 10, 23) <= end else []
    def announcements(self, start, end):
        return [
            {"symbol": "TEST", "an_dt": "23-Oct-2026 18:15:00", "desc": "New order received", "attchmntFile": "https://nsearchives.nseindia.com/corporate/order.pdf"},
            {"symbol": "TEST", "an_dt": "23-Oct-2026 18:16:00", "desc": "Penalty notice"},
        ]
    def document(self, url):
        return b"Unstructured attachment: no assumed figures"
    def comparison(self, symbol):
        return []
    def candles(self, symbol):
        return []


class TestResultsPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = OfflineClient()
        self.service = ResultsService(Path(self.tmp.name)/"results.db", Path(self.tmp.name)/"candles.db", client=self.client, symbol_provider=lambda: {"TEST", "TEST0", "TEST1", "TEST2", "TEST3"})
        self.now = datetime(2026, 10, 26, 10, tzinfo=IST)
        self.clock = patch("marketscope.results_service._now", return_value=self.now)
        self.clock.start()
        self.wait = patch.object(self.service._stop, "wait", return_value=False)
        self.wait.start()
        self.fyers = patch("marketscope.results_history.fyers_configured", return_value=False)
        self.fyers.start()
    def tearDown(self):
        self.clock.stop()
        self.wait.stop()
        self.fyers.stop()
        self.service.stop()
        self.tmp.cleanup()

    def test_refresh_backfills_bounded_chunk_and_preserves_failure_data(self):
        first = self.service.refresh()
        self.assertEqual(first["event_count"], 1)
        self.assertEqual(first["state"], "ready")
        self.assertEqual(len(self.client.calls), 2)
        self.assertLessEqual((self.client.calls[1][1] - self.client.calls[1][0]).days, 29)
        self.assertEqual(first["lookback_days"], 1825)
        self.assertGreater(first["backfill_chunks_remaining"], 1)
        self.client.down = True
        failed = self.service.refresh()
        self.assertEqual(failed["state"], "unavailable")
        self.assertTrue(failed["stale"])
        self.assertEqual(self.service.list_events()["total"], 1)

    def test_original_versions_survive_later_revisions_and_metadata_poll(self):
        event = normalize_filing(filing(), self.now)
        current = financial("2026-09-30", 120, 15)
        previous = financial("2026-06-30", 100, 10)
        event = apply_financial_rows(event, [current, previous], event["announced_at"], event["source_url"])
        self.service._upsert_event(event)
        self.service._upsert_event(normalize_filing(filing(), self.now))
        same = self.service.get_event(event["id"])
        self.assertEqual(same["financials"]["revenue"], 120)
        self.assertEqual(same["previous"]["revenue"], 100)
        self.assertEqual(same["growth"]["revenue_qoq"], 20)
        revised = copy.deepcopy(same)
        revised["financials"]["revenue"] = 130
        revised["financials_available_at"] = "2026-10-26T10:00:00+05:30"
        revised["known_at"] = revised["financials_available_at"]
        self.service._upsert_event(revised)
        all_versions = self.service.history("TEST")
        self.assertTrue(any(e.get("financials", {}).get("revenue") == 120 for e in all_versions))
        self.assertEqual(self.service.get_event(event["id"])["revision"], 2)

    def test_date_search_tone_filters_happen_before_pagination(self):
        for i in range(4):
            event = normalize_filing(filing(symbol="TEST"+str(i)), self.now)
            event = apply_financial_rows(event, [financial("2026-09-30", 120+i, 15), financial("2026-06-30", 100, 10)], event["announced_at"], event["source_url"])
            self.service._upsert_event(event)
        result = self.service.list_events(date_from="2026-10-23", date_to="2026-10-23", search="TEST", tone="positive", limit=2)
        self.assertEqual(result["total"], 4)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["next_offset"], 2)
        last = self.service.list_events(search="TEST3", tone="positive", limit=1)
        self.assertEqual(last["total"], 1)
        self.assertEqual(last["events"][0]["symbol"], "TEST3")
        self.assertEqual(self.service.list_events(date_from="2026-10-24")["total"], 0)
        with self.assertRaises(ValueError):
            self.service.list_events(date_from="oops")
        with self.assertRaises(ValueError):
            self.service.list_events(date_from="2026-10-25", date_to="2026-10-20")

    def test_news_keeps_positive_and_risk_disclosures_with_links(self):
        self.service.refresh()
        event = self.service.get_event(normalize_filing(filing())["id"])
        self.assertEqual({n["category"] for n in event["news"]}, {"potential_catalyst", "risk"})
        self.assertTrue(all(n["url"].startswith("https://") for n in event["news"]))

    def test_current_catalogue_limits_storage_display_and_historical_inputs(self):
        allowed = {"TEST"}
        self.service.symbol_provider = lambda: allowed
        outside = normalize_filing(filing("OUTSIDE"), self.now)
        self.service._upsert_event(outside)
        self.assertEqual(self.service.list_events()["total"], 0)
        self.service._upsert_event(normalize_filing(filing(), self.now))
        self.service._news([{ "symbol": "OUTSIDE", "an_dt": "23-Oct-2026 18:15:00", "desc": "New order" }])
        self.assertEqual(self.service.status()["universe_count"], 1)
        self.assertEqual(self.service.list_events()["total"], 1)
        allowed.clear()
        self.assertEqual(self.service.list_events()["events"], [])
        self.assertEqual(self.service.history(), [])
        self.assertIsNone(self.service.get_event(normalize_filing(filing())["id"]))
        self.assertEqual(self.service.get_candles("TEST"), [])
        self.assertEqual(self.service.status()["event_count"], 0)
        self.assertEqual(self.service.status()["state"], "needs_catalogue")

    def test_empty_or_missing_catalogue_fails_closed_without_network(self):
        self.service.symbol_provider = lambda: set()
        status = self.service.refresh()
        self.assertEqual(status["state"], "needs_catalogue")
        self.assertEqual(self.client.calls, [])
        self.service.symbol_provider = lambda: (_ for _ in ()).throw(OSError("unavailable"))
        self.assertEqual(self.service.status()["universe_count"], 0)

    def test_universe_filter_is_applied_before_page_and_coverage_counts(self):
        permitted = {"TEST", "SECOND"}
        self.service.symbol_provider = lambda: permitted
        self.service._upsert_event(normalize_filing(filing(), self.now))
        self.service._upsert_event(normalize_filing(filing("SECOND"), self.now))
        permitted.remove("SECOND")
        page = self.service.list_events(limit=1)
        self.assertEqual(page["total"], 1)
        self.assertIsNone(page["next_offset"])
        self.assertEqual(page["events"][0]["symbol"], "TEST")
        self.assertEqual(self.service.status()["symbols_queued"], 1)

    def test_archive_preserves_corporate_action_exclusion_and_unverified_basis(self):
        self.service._save_candles("TEST", [{"t": int(datetime(2026, 10, 23, 9, 15, tzinfo=IST).timestamp()), "o":100,"h":102,"l":99,"c":101,"v":1000,"timeframe":5,"closed":True}])
        self.service._set_meta("corporate_actions:TEST", {"2026-10-23":["splits"]})
        candle = self.service.get_candles("TEST")[0]
        self.assertTrue(candle["corporate_action"])
        self.assertFalse(candle["adjustments_verified"])
        self.assertEqual(candle["corporate_action_types"], ["splits"])

    def test_same_document_refiling_id_invalidates_old_financials(self):
        event = normalize_filing(filing(), self.now)
        event = apply_financial_rows(event, [financial("2026-09-30"), financial("2026-06-30")], event["announced_at"], event["source_url"])
        self.service._upsert_event(event)
        raw = filing()
        raw["seq_Id"] = "fixture-new-filing-id"
        self.service._upsert_event(normalize_filing(raw, self.now))
        current = self.service.get_event(event["id"])
        self.assertEqual(current["financial_status"], "revision_pending")
        self.assertEqual(current["known_at"], self.now.isoformat())
        self.assertEqual(current["revision"], 2)

    def test_partial_fyers_history_is_retried_without_starving_next_company(self):
        for symbol in ("TEST", "TEST0"):
            self.service._upsert_event(normalize_filing(filing(symbol), self.now))
        self.service.symbol_batch = 1
        now = datetime(2026,10,27,10,tzinfo=IST)
        prior = {"t":int(datetime(2026,10,23,15,25,tzinfo=IST).timestamp()), "o":100,"h":102,"l":99,"c":101,"v":1000,"timeframe":5,"closed":True}
        opening = int(datetime(2026,10,26,9,15,tzinfo=IST).timestamp())
        complete = [prior] + [{**prior,"t":opening+i*300} for i in range(9)]
        with patch("marketscope.results_history.fyers_configured", return_value=True), patch("marketscope.results_history.fetch_fyers_event_candles", side_effect=[[prior],complete,complete]) as fetch:
            self.service._enrich_fyers({"errors":[]},now)
            self.assertEqual(self.service._meta("fyers_history_done"),[])
            first = fetch.call_args.args[0]
            self.service._enrich_fyers({"errors":[]},now)
            second = fetch.call_args.args[0]
            self.assertNotEqual(first,second)
            self.assertEqual(len(self.service._meta("fyers_history_done")),1)
            later = datetime(2026,10,28,10,tzinfo=IST)
            self.service._enrich_fyers({"errors":[]},later)
            self.assertEqual(fetch.call_args.args[0],first)
            self.assertEqual(len(self.service._meta("fyers_history_done")),2)

    def test_candles_empty_on_cold_start_without_fabrication(self):
        self.assertEqual(self.service.get_candles("TEST"), [])
        self.assertEqual(self.service.list_events()["events"], [])
        self.assertTrue(self.service.status()["stale"])

    def test_historical_metadata_is_collected_even_if_current_feed_fails(self):
        historical = filing(stamp="10-Oct-2026 18:10:00")
        def source(start, end):
            self.client.calls.append((start, end))
            if end == self.now.date():
                raise OSError("Fixture current feed unavailable")
            return [historical]
        self.client.filings = source
        status = self.service.refresh()
        self.assertEqual(status["state"], "partial")
        self.assertEqual(status["event_count"], 1)
        self.assertIsNone(status.get("last_success_at"))
        self.assertEqual(status["last_history_success_at"], self.now.isoformat())
        self.assertEqual(len(self.client.calls), 2)
        self.assertIn("Fixture current feed unavailable", status["errors"][0])
        self.assertEqual(self.service.list_events()["events"][0]["financials"], {})

    def test_bulk_backfill_is_metadata_first_paced_and_resumes_after_restart(self):
        self.client.filings = lambda start, end: self.client.calls.append((start, end)) or []
        with patch.object(self.service, "_enrich_symbols") as enrich:
            first = self.service.backfill(max_batches=2, date_from="2026-07-01", date_to="2026-10-26")
            enrich.assert_not_called()
        self.assertEqual(first["backfill_job_completed_batches"], 2)
        self.assertEqual(first["backfill_job_state"], "partial")
        self.assertTrue(all((end-start).days <= 29 for start,end in self.client.calls))
        expected_end = self.client.calls[-1][0] - timedelta(days=1)
        # A new service instance represents a server/CLI restart and must resume
        # the committed range rather than redownloading its first batch.
        replacement = ResultsService(self.service.db_path, self.service.candle_db_path,
                                     client=self.client, symbol_provider=lambda: {"TEST"})
        with patch.object(replacement._stop, "wait", return_value=False):
            second = replacement.backfill(max_batches=2, date_from="2026-07-01", date_to="2026-10-26")
        self.assertEqual(self.client.calls[2][1], expected_end)
        self.assertEqual(second["backfill_job_state"], "complete")
        self.assertIsNone(replacement._meta("backfill_cursor"))

    def test_failed_history_batch_keeps_cursor_and_committed_earlier_batches(self):
        def source(start, end):
            self.client.calls.append((start, end))
            if len(self.client.calls) == 1:
                return [filing()]
            raise OSError("Fixture history unavailable")
        self.client.filings = source
        first = self.service.backfill(max_batches=3)
        self.assertEqual(first["backfill_job_state"], "failed")
        self.assertEqual(first["backfill_job_completed_batches"], 1)
        self.assertEqual(first["event_count"], 1)
        cursor = self.service._meta("backfill_cursor")
        self.assertEqual(cursor, (self.now.date() - timedelta(days=30)).isoformat())
        self.assertIsNone(first.get("last_success_at"))
        self.service.backfill(max_batches=1)
        self.assertEqual(self.service._meta("backfill_cursor"), cursor)
        self.assertEqual(self.client.calls[-1][1].isoformat(), cursor)

    def test_invalid_history_ranges_and_unknown_shapes_do_not_advance(self):
        for kwargs in ({"max_batches": 0}, {"date_from":"2026-10-27"}, {"date_to":"not-date"}, {"date_from":"1999-01-01"}):
            with self.assertRaises(ValueError):
                self.service.backfill(**kwargs)
        self.assertEqual(self.client.calls, [])
        self.client.filings = lambda start,end: {"error":"unsupported schema"}
        result = self.service.backfill(max_batches=1)
        self.assertEqual(result["backfill_job_state"], "failed")
        self.assertIsNone(self.service._meta("backfill_cursor"))

    def test_manual_official_metadata_import_is_idempotent_and_never_accepts_figures(self):
        raw = filing()
        raw["financials"] = {"revenue": 999999}
        raw["growth"] = {"profit_qoq": 999999}
        rows = [raw, filing("OUTSIDE"), {"symbol":"TEST"}]
        result = self.service.import_filings(rows)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["outside_universe"], 1)
        self.assertEqual(result["rejected"], 1)
        current = self.service.list_events()["events"][0]
        self.assertEqual(current["financials"], {})
        self.assertEqual(current["growth"], {"notes":[]})
        self.service.import_filings(rows)
        self.assertEqual(self.service.list_events()["total"], 1)
        self.assertEqual(self.service.get_event(current["id"])["revision"], 1)
        with self.assertRaises(ValueError):
            self.service.import_filings([raw], source_url="https://outside.example/results")

    def test_official_csv_metadata_headers_preserve_real_dates(self):
        row = {"SYMBOL":"TEST", "COMPANY NAME":"Test Fixture",
               "BROADCAST DATE/TIME\n":"23-Oct-2026 18:10:00", "QUARTER END DATE\n":"30-Sep-2026",
               "CONSOLIDATED / STANDALONE\n":"Consolidated",
               "XBRL":"https://nsearchives.nseindia.com/corporate/TEST.xml"}
        result = self.service.import_filings([row])
        self.assertEqual(result["imported"], 1)
        stored = self.service.list_events()["events"][0]
        self.assertEqual(stored["announced_at"], "2026-10-23T18:10:00+05:30")
        self.assertEqual(stored["basis"], "consolidated")

    def test_bulk_export_latest_revision_cannot_be_replaced_by_older_row_order(self):
        old = filing()
        new = filing(stamp="24-Oct-2026 18:10:00")
        new["seq_Id"] = "fixture-newer"
        new["xbrl"] = "https://nsearchives.nseindia.com/corporate/TEST-new.xml"
        result = self.service.import_filings([new, old])
        self.assertEqual(result["imported"], 1)
        stored = self.service.list_events()["events"][0]
        self.assertEqual(stored["announced_at"], "2026-10-23T18:10:00+05:30")
        self.assertEqual(stored["document_url"], new["xbrl"])
        self.assertEqual(stored["known_at"], "2026-10-24T18:10:00+05:30")


class TestStoredCatalogue(unittest.TestCase):
    def test_all_symbols_are_only_current_database_rows(self):
        from marketscope.stock_database import StockDatabaseManager
        with tempfile.TemporaryDirectory() as directory:
            database = StockDatabaseManager(Path(directory)/"catalogue.db")
            self.assertEqual(database.get_all_symbols(),set())
            with database._get_connection() as con:
                con.execute("INSERT INTO stocks(symbol,name) VALUES('TEST','Test Fixture')")
                con.commit()
            self.assertEqual(database.get_all_symbols(),{"TEST"})

"""Published RSS fallback, chronology and bank taxonomy regression checks."""
import tempfile
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from marketscope.results_rss import RSS_FEEDS, RSSResultsFeed, document_filings, parse_feed
from marketscope.results_service import NSEPublicClient, ResultsService, normalize_filing, parse_financial_document, apply_financial_rows
import json


URL = "https://nsearchives.nseindia.com/corporate/xbrl/TEST_BANKING.xml"
DOC = b'''<xbrl xmlns:i="urn:test"><context id="q"><startDate>2026-07-01</startDate><endDate>2026-09-30</endDate></context><unit id="money"><measure>iso:INR</measure></unit><i:Symbol contextRef="q">TEST</i:Symbol><i:NameOfTheCompany contextRef="q">Test Fixture Company</i:NameOfTheCompany><i:DateOfStartOfReportingPeriod contextRef="q">2026-07-01</i:DateOfStartOfReportingPeriod><i:DateOfEndOfReportingPeriod contextRef="q">2026-09-30</i:DateOfEndOfReportingPeriod><i:NatureOfReportStandaloneConsolidated contextRef="q">Standalone</i:NatureOfReportStandaloneConsolidated><i:TypeOfReportingPeriod contextRef="q">Quarterly</i:TypeOfReportingPeriod><i:Income contextRef="q" unitRef="money">12000000</i:Income><i:ProfitLossForThePeriod contextRef="q" unitRef="money">2000000</i:ProfitLossForThePeriod></xbrl>'''


def feed(description="Original", link=URL):
    return f'<rss><channel><item><title>Test Fixture Company</title><link>{link}</link><description>{description}</description><pubDate>25-Oct-2026 18:00:00</pubDate></item></channel></rss>'.encode()


class TestPublicRSS(unittest.TestCase):
    def test_verified_document_identity_and_original_publication(self):
        item = parse_feed(feed(), RSS_FEEDS[0])[0]
        rows = document_filings(DOC, item)
        self.assertEqual(rows[0]["symbol"], "TEST")
        self.assertEqual(rows[0]["qe_Date"], "2026-09-30")
        self.assertEqual(normalize_filing(rows[0])["announced_at"], "2026-10-25T18:00:00+05:30")

    def test_revision_does_not_invent_original_announcement_time(self):
        item = parse_feed(feed("Revision"), RSS_FEEDS[0])[0]
        normalized = normalize_filing(document_filings(DOC, item)[0])
        self.assertFalse(normalized["announcement_time_known"])
        self.assertTrue(normalized["original_announcement_unknown"])
        self.assertEqual(normalized["revised_at"], "2026-10-25T18:00:00+05:30")

    def test_public_rss_caches_documents_and_never_claims_full_history(self):
        with tempfile.TemporaryDirectory() as directory:
            def read(url):
                if url in RSS_FEEDS:
                    return feed() if url == RSS_FEEDS[0] else b"<rss><channel/></rss>"
                return DOC
            reader = MagicMock(side_effect=read)
            collector = RSSResultsFeed(reader, cache_dir=directory)
            for _ in range(2):
                rows = collector.filings(date(2026, 10, 1), date(2026, 10, 26))
                self.assertEqual(len(rows), 1)
            self.assertEqual(sum(call.args[0] == URL for call in reader.call_args_list), 1)
            self.assertTrue(collector.last_coverage["recent_only"])
            self.assertFalse(collector.last_coverage["history_complete"])

    def test_external_links_and_xml_entities_are_rejected(self):
        self.assertEqual(parse_feed(feed(link="https://outside.example/TEST.xml"), RSS_FEEDS[0]), [])
        with self.assertRaises(ValueError):
            parse_feed(b'<!DOCTYPE x [<!ENTITY x "bad">]><rss><channel/></rss>', RSS_FEEDS[0])

    def test_query_failure_uses_rss_only_for_recent_records(self):
        client = NSEPublicClient()
        client.filings = MagicMock(side_effect=RuntimeError("source blocked"))
        client._rss_feed = MagicMock()
        client._rss_feed.filings.return_value = [{"symbol": "TEST"}]
        client._rss_feed.last_coverage = {"recent_only": True, "history_complete": False}
        self.assertEqual(len(client.recent_filings(date(2026, 10, 1), date(2026, 10, 26))), 1)
        self.assertTrue(client.last_recent_coverage["recent_only"])
        with self.assertRaises(RuntimeError):
            client.filings(date(2025, 1, 1), date(2025, 2, 1))

    def test_bank_income_is_explicitly_total_income(self):
        row = normalize_filing(document_filings(DOC, parse_feed(feed(), RSS_FEEDS[0])[0])[0])
        facts = parse_financial_document(DOC, row)[0]
        self.assertEqual(facts["revenue_metric"], "total_income")
        self.assertEqual(facts["revenue"], 120)
        self.assertEqual(facts["net_profit"], 20)

    def test_older_export_does_not_replace_a_newer_document(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ResultsService(db_path=directory + "/results.db", candle_db_path=directory + "/candles.db", symbol_provider=lambda: {"TEST"})
            old = {"symbol": "TEST", "qe_Date": "30-Sep-2026", "consolidated": "Standalone", "creation_Date": "23-Oct-2026 18:00:00", "xbrl": URL}
            new = {**old, "creation_Date": "24-Oct-2026 18:00:00", "xbrl": URL.replace("TEST_BANKING", "NEW_BANKING")}
            service.import_filings([new, old])
            service.import_filings([old])
            current = service.list_events()["events"][0]
            self.assertEqual(current["document_url"], new["xbrl"])
            self.assertEqual(current["announced_at"], "2026-10-23T18:00:00+05:30")

    def test_legacy_export_resolves_only_an_unambiguous_stored_company_name(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ResultsService(db_path=directory + "/results.db", symbol_provider=lambda: {"TEST"}, company_provider=lambda: [("TEST", "Test Fixture Company Limited")])
            row = {"COMPANY NAME": "Test Fixture Company Ltd.", "PERIOD ENDED": "30-Sep-2026", "CONSOLIDATED / NON-CONSOLIDATED": "Non-Consolidated", "Exchange Dissemination Time": "25-Oct-2026 18:00:00", "** XBRL": URL}
            result = service.import_filings([row, {**row, "COMPANY NAME": "Another Company Limited"}])
            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["unresolved_company"], 1)
            self.assertEqual(service.list_events()["events"][0]["symbol"], "TEST")

    def test_post_covid_scope_hides_and_archives_older_declarations_and_periods(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ResultsService(db_path=directory + "/results.db", symbol_provider=lambda: {"TEST"})
            rows = [
                {"symbol":"TEST","qe_Date":"30-Sep-2022","creation_Date":"25-Oct-2022 18:00:00","consolidated":"Standalone"},
                {"symbol":"TEST","qe_Date":"31-Mar-2021","creation_Date":"25-Oct-2026 18:00:00","consolidated":"Standalone"},
                {"symbol":"TEST","qe_Date":"30-Jun-2023","creation_Date":"25-Jul-2023 18:00:00","consolidated":"Standalone"},
            ]
            events = [normalize_filing(row) for row in rows]
            with service._connection() as con:
                for event in events:
                    con.execute("INSERT INTO result_events VALUES(?,?,?,?,?,?,?)", (event["id"],event["symbol"],event["announced_at"],event["period_end"],event["basis"],json.dumps(event),event["known_at"]))
            self.assertEqual(service.list_events(date_from="2000-01-01")["total"], 1)
            self.assertIsNone(service.get_event(events[0]["id"]))
            self.assertIsNone(service.get_event(events[1]["id"]))
            result = service.archive_scope_exclusions()
            self.assertEqual(result["archived"], 2)
            with service._connection() as con:
                self.assertEqual(con.execute("SELECT count(*) FROM result_scope_archive").fetchone()[0], 2)

    def test_separately_filed_quarters_form_comparisons_without_future_restatement(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ResultsService(db_path=directory + "/results.db", symbol_provider=lambda: {"TEST"})
            def add(period, publication, revenue, profit, revised=None):
                raw = {"symbol":"TEST","qe_Date":period,"creation_Date":publication,"consolidated":"Standalone","xbrl":URL,"revision_Date":revised}
                event = normalize_filing(raw)
                fact = {"period_end":event["period_end"],"basis":"standalone","unit":"INR lakh","revenue_metric":"revenue_from_operations","profit_metric":"profit_for_period","revenue":revenue,"net_profit":profit,"eps":1}
                enriched = apply_financial_rows(event,[fact],event["known_at"],URL)
                service._upsert_event(enriched)
                return event["id"]
            add("31-Mar-2026","25-Apr-2026 18:00:00",100,10)
            target = add("30-Jun-2026","25-Jul-2026 18:00:00",120,15)
            add("31-Mar-2026","25-Apr-2026 18:00:00",200,20,"25-Aug-2026 18:00:00")
            service._rebuild_comparisons("TEST")
            result = service.get_event(target)
            self.assertAlmostEqual(result["growth"]["revenue_qoq"],20)
            self.assertAlmostEqual(result["growth"]["profit_qoq"],50)
            self.assertEqual(result["comparison_available_at"],"2026-07-25T18:00:00+05:30")


if __name__ == "__main__":
    unittest.main()

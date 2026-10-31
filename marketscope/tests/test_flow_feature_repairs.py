"""Regressions for real session selection and the Flow River's UI contracts."""
from __future__ import annotations

import gc
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "marketscope"))
from candle_archive import CandleArchive
from security import DatabaseManager
from session_manager import resolve_previous_session_date
from flow_engine import build_flow_payload
from market_schedule import IST


class PreviousSessionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(str(Path(self.tmp.name) / "app.db"))
        self.archive = CandleArchive(str(Path(self.tmp.name) / "candles.db"))

    def tearDown(self):
        gc.collect()
        self.tmp.cleanup()

    def seed_snapshot(self, day):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO session_snapshots(session_date,timeframe,payload_json,complete,data_source,created_at) VALUES(?,?,?,?,?,?)",
                (day, 5, "{}", 1, "exchange", day + "T10:00:00Z"),
            )

    def test_previous_during_live_and_closed_today_selects_strictly_earlier_snapshot(self):
        self.seed_snapshot("2026-10-23")
        self.seed_snapshot("2026-10-26")
        for state in ("LIVE", "CLOSED_TODAY"):
            actual = resolve_previous_session_date({"session_state": state, "session_date": "2026-10-26"}, self.archive, self.db)
            self.assertEqual(actual, "2026-10-23")

    def test_already_previous_session_does_not_step_back_twice(self):
        actual = resolve_previous_session_date({"session_state": "PREVIOUS", "session_date": "2026-10-23"}, self.archive, self.db)
        self.assertEqual(actual, "2026-10-23")

    def test_archive_lookup_rejects_current_and_future_dates(self):
        for day in ("2026-10-23", "2026-10-26", "2026-10-27"):
            timestamp = int(datetime.fromisoformat(day).replace(hour=15, minute=10, tzinfo=IST).timestamp())
            self.archive.archive_closed_candles("TEST", 5, [{"t": timestamp, "o": 100, "h": 101, "l": 99, "c": 100, "v": 20, "closed": True}])
        actual = resolve_previous_session_date({"session_state": "LIVE", "session_date": "2026-10-26"}, self.archive, self.db)
        self.assertEqual(actual, "2026-10-23")

    def test_empty_archive_falls_back_over_weekend(self):
        actual = resolve_previous_session_date({"session_state": "LIVE", "session_date": "2026-10-26"}, self.archive, self.db)
        self.assertEqual(actual, "2026-10-23")

    def test_flow_builds_selected_prior_date_and_declares_fixed_interval(self):
        self.seed_snapshot("2026-10-23")
        with patch("flow_engine.get_session_state", return_value={"session_state": "LIVE", "session_date": "2026-10-26"}), patch("flow_engine.build_session_snapshot", return_value={"stocks": {}}) as build:
            result = build_flow_payload(session_param="previous", candle_archive=self.archive, db_manager=self.db)
        self.assertEqual(build.call_args.kwargs["session_date"], "2026-10-23")
        self.assertEqual(result["session_state"], "PREVIOUS")
        self.assertEqual(result["timeframe"], 5)


class FlowUIContractsTests(unittest.TestCase):
    def test_flow_navigation_provenance_and_laya_use_selected_observation(self):
        script = r'''
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {parseHTML} from 'linkedom';
const html='<html><body><button class="tf-btn" data-tf="1">1m</button><button class="tf-btn" data-tf="5">5m</button><button class="tf-btn" data-tf="15">15m</button><button id="prevSessionToggleBtn"></button><span id="sessionStateChip"></span><span id="sessionStateLabel"></span><span id="feedStatusChip"></span><div id="sessionBanner"><span id="sessionBannerText"></span></div><div id="funnelGridBody"></div><div id="cardsPanel"></div><div id="layaDrawer"></div><span id="layaTitleSym"></span><div id="layaContentArea"></div><div id="recapContentArea"></div></body></html>';
const {document}=parseHTML(html);
document.addEventListener=()=>{};
const context=vm.createContext({document,window:{addEventListener(){}},console,setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},URLSearchParams,fetch:async()=>{throw Error('Unexpected request')}});
const code=readFileSync('marketscope/static/flow.js','utf8');
vm.runInContext(code+'\nglobalThis.flowTest={initEventListeners,updateTopBarAndBanner,loadLayaPanel,loadRecapPanel,fetchFlowData,set(data,state){flowData=data;sessionState=state;},get(){return flowData}};',context);
const api=context.flowTest;
api.initEventListeners();
assert.equal(document.querySelectorAll('.tf-btn').length,1);
assert.equal(document.querySelector('.tf-btn').dataset.tf,'5');
api.set({session_date:'2026-10-23',feed:{ok_count:2,failed_count:0},stocks:{TEST:{score:72,action:'RESEARCH',rsi:60,rvol:1.2}}},'PREVIOUS');
api.updateTopBarAndBanner();
assert.equal(document.getElementById('feedStatusChip').textContent,'SESSION ARCHIVE');
await api.loadLayaPanel('TEST');
assert.match(document.getElementById('layaContentArea').textContent,/2026-10-23/);
assert.match(document.getElementById('layaContentArea').textContent,/72/);
api.set(null,'UNAVAILABLE');
await api.loadRecapPanel();
assert.match(document.getElementById('recapContentArea').textContent,/No received session/);
api.set({stocks:{TEST:{}},feed:{ok_count:1}},'LIVE');
context.fetch=async()=>({ok:true,json:async()=>({symbol:'TEST',institutional_briefing:'Verified contract briefing',source:'rules_engine'})});
await api.loadLayaPanel('TEST');
assert.match(document.getElementById('layaContentArea').textContent,/Verified contract briefing/);
// A late automatic response must not replace a manually selected previous date.
let release;
context.fetch=url=>url.includes('session=auto')?new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>({session_date:'2026-10-26',session_state:'LIVE',empty:true,feed:{ok_count:1}})})}):Promise.resolve({ok:true,json:async()=>({session_date:'2026-10-23',session_state:'PREVIOUS',empty:true,feed:{ok_count:1}})});
const pending=api.fetchFlowData('auto');
await api.fetchFlowData('previous');release();await pending;
assert.equal(api.get().session_date,'2026-10-23');
assert.equal(document.getElementById('prevSessionToggleBtn').textContent,'Return to auto session');
context.fetch=async()=>({ok:false,status:503});
await api.fetchFlowData('auto');
assert.equal(api.get(),null);
assert.equal(document.getElementById('feedStatusChip').textContent,'FEED: DOWN');
assert.match(document.getElementById('cardsPanel').textContent,/unavailable/);
console.log('Flow interaction contracts passed');
'''
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()


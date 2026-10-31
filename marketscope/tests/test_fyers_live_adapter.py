import json
import unittest
from unittest.mock import MagicMock, patch
from marketscope import fyers_live


class LiveAdapterTests(unittest.TestCase):
    def test_missing_authorization_never_fabricates_price(self):
        with patch.object(fyers_live, 'get_fyers_history_provider', side_effect=RuntimeError()):
            row = fyers_live._fetch_stock('TCS')
        self.assertEqual(row['status'], 'failed')
        self.assertIsNone(row['price'])

    def test_quote_and_history_are_readonly_and_stale_is_excluded(self):
        provider = MagicMock()
        provider.search_instruments.return_value = {'instruments':[{'symbol':'NSE:TCS-EQ','short_symbol':'TCS','kind':'equity'}]}
        provider._credentials.return_value = ('test-app','test-token')
        from datetime import datetime
        stamp=datetime.now(fyers_live.IST).replace(hour=9,minute=15,second=0,microsecond=0).timestamp()
        provider.get_candles.return_value = {'candles':[{'t':stamp,'o':100,'h':102,'l':99,'c':101,'v':10,'closed':True}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'d':[{'n':'NSE:TCS-EQ','s':'ok','v':{'lp':101,'tt':900}}]}).encode()
        with patch.object(fyers_live, 'get_fyers_history_provider', return_value=provider), patch.object(fyers_live.time,'time',return_value=1000), patch.object(fyers_live.urllib.request,'urlopen',return_value=response) as request, patch('marketscope.candle_archive.get_candle_archive'):
            row = fyers_live._fetch_stock('TCS')
        self.assertEqual(row['status'],'stale')
        self.assertEqual(row['quote_timestamp'],900)
        req=request.call_args.args[0]
        self.assertEqual(req.get_method(),'GET')
        self.assertTrue(req.full_url.startswith('https://api-t1.fyers.in/data/quotes?'))


if __name__ == '__main__':
    unittest.main()

import hashlib
import io
import json
import socket
import ssl
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch
from scripts.connect_fyers import exchange_code, save_token, ConnectionFailure


class ConnectorTests(unittest.TestCase):
    def opener(self, payload):
        reply = MagicMock()
        reply.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return MagicMock(return_value=reply)

    def test_exchange_uses_documented_hash_and_fixed_endpoint(self):
        opener = self.opener({'s':'ok','access_token':'private-token'})
        self.assertEqual(exchange_code('app-100','secret','code',opener),'private-token')
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url,'https://api-t1.fyers.in/api/v3/validate-authcode')
        self.assertEqual(json.loads(request.data)['appIdHash'],hashlib.sha256(b'app-100:secret').hexdigest())

    def test_rejections_never_echo_provider_messages(self):
        for opener in [self.opener({'s':'error','code':-8,'message':'SECRET'}),
                       MagicMock(side_effect=urllib.error.HTTPError('url',401,'reason',{},io.BytesIO(b'{"code":-8,"message":"SECRET"}')))]:
            with self.assertRaises(ConnectionFailure) as caught:
                exchange_code('app','secret','code',opener)
            self.assertIn('-8',str(caught.exception))
            self.assertNotIn('SECRET',str(caught.exception))

    def test_network_errors_are_actionable_without_raw_exception(self):
        for cause,word in [(socket.gaierror('SECRET'),'DNS'),(ssl.SSLError('SECRET'),'TLS'),(TimeoutError('SECRET'),'timed out')]:
            with self.assertRaises(ConnectionFailure) as caught:
                exchange_code('app','secret','code',MagicMock(side_effect=urllib.error.URLError(cause)))
            self.assertIn(word,str(caught.exception)); self.assertNotIn('SECRET',str(caught.exception))

    def test_save_preserves_other_settings_and_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'.env').write_text('OTHER=keep\nFYERS_ACCESS_TOKEN=old\n')
            save_token('app','new-token',root)
            content=(root/'.env').read_text()
            self.assertIn('OTHER=keep',content); self.assertNotIn('=old',content)
            if sys.platform != 'win32':
                self.assertEqual((root/'.env').stat().st_mode & 0o777,0o600)
            self.assertEqual(list(root.glob('.env.fyers-*')),[])

    def test_failed_replace_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'.env').write_text('OTHER=keep\n')
            with patch('scripts.connect_fyers.os.replace',side_effect=PermissionError()):
                with self.assertRaises(PermissionError): save_token('app','token',root)
            self.assertEqual((root/'.env').read_text(),'OTHER=keep\n')
            self.assertEqual(list(root.glob('.env.fyers-*')),[])

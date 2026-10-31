"""Local FYERS OAuth callback. Secrets are entered privately, never logged.

Run with Python 3.12. Callback uses the existing app's registered port 8765.
"""
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import socket
import ssl
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REDIRECT = 'http://127.0.0.1:8765/api/fyers/callback'


class ConnectionFailure(Exception):
    """Contains only safe diagnostics, never provider bodies or credentials."""


def exchange_code(app, secret, code, opener=None):
    body = json.dumps({'grant_type': 'authorization_code',
        'appIdHash': hashlib.sha256(f'{app}:{secret}'.encode()).hexdigest(),
        'code': code}).encode()
    request = urllib.request.Request('https://api-t1.fyers.in/api/v3/validate-authcode',
        data=body, headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                            'User-Agent': 'ZienQuant/1.0'})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ConnectionFailure('Oversized FYERS response; no token saved.')
        payload = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # Provider messages may echo inputs. Only retain numeric error codes.
        provider_code = None
        try:
            item = json.loads(exc.read(65536)).get('code')
            if type(item) is int: provider_code = item
        except Exception:
            pass
        raise ConnectionFailure(f'FYERS token exchange HTTP {exc.code}; provider code {provider_code}. Check the full App ID and matching App Secret; restart for a fresh code.') from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, ssl.SSLError):
            hint = 'TLS certificate verification failed. Repair Python CA certificates; do not disable verification.'
        elif isinstance(reason, socket.gaierror):
            hint = 'DNS lookup failed for api-t1.fyers.in. Check your network/DNS/VPN and retry.'
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            hint = 'FYERS request timed out. Check connectivity and retry with a fresh login.'
        else:
            hint = 'Could not reach the FYERS token service. Check network/proxy settings.'
        raise ConnectionFailure(hint) from None
    except (ValueError, UnicodeError):
        raise ConnectionFailure('FYERS returned an unreadable response; no token saved.') from None
    if not isinstance(payload, dict):
        raise ConnectionFailure('FYERS returned an unexpected response format.')
    token = payload.get('access_token')
    if payload.get('s') != 'ok' or not isinstance(token, str) or not token or any(c.isspace() for c in token):
        value = payload.get('code'); safe_code = value if type(value) is int else 'unknown'
        raise ConnectionFailure(f'FYERS rejected token exchange (code {safe_code}). Verify full App ID and matching App Secret; obtain a fresh authorization code.')
    return token


def save_token(app, token, root=ROOT):
    target = root / '.env'
    lines = target.read_text().splitlines() if target.exists() else []
    keys = {'FYERS_APP_ID', 'FYERS_ACCESS_TOKEN', 'MS_MARKET_PROVIDER'}
    lines = [line for line in lines if line.split('=', 1)[0].strip() not in keys]
    lines += [f'FYERS_APP_ID={app}', f'FYERS_ACCESS_TOKEN={token}', 'MS_MARKET_PROVIDER=fyers']
    temporary = root / ('.env.fyers-' + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists(): temporary.unlink()


def main():
    app = getpass.getpass('FYERS App ID (hidden): ').strip()
    secret = getpass.getpass('FYERS App Secret (hidden): ').strip()
    if not app or not secret or any(c in app + secret for c in '\r\n'):
        raise SystemExit('Valid app credentials are required.')
    state = secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs contain sensitive authorization codes.

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            valid = (parsed.path == '/api/fyers/callback'
                     and hmac.compare_digest(query.get('state', [''])[0], state)
                     and bool(query.get('auth_code', [''])[0]))
            if not valid:
                self.send_error(400, 'Invalid callback state or missing authorization code')
                return
            try:
                token = exchange_code(app, secret, query['auth_code'][0])
                save_token(app, token)
                result['ok'] = True
                message = b'FYERS token saved locally. Restart Zien Quant. This connection enables market data only.'
                self.send_response(200)
            except Exception as exc:
                detail = str(exc) if isinstance(exc, ConnectionFailure) else ('Could not save .env; check folder permissions.' if isinstance(exc, OSError) else 'Unexpected connector failure; no successful connection confirmed.')
                result['error'] = detail
                print('FYERS connection failed: ' + detail, flush=True)
                message = ('FYERS connection failed: ' + detail + '\nRun the connector again; do not refresh this callback.').encode()
                self.send_response(502)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    with HTTPServer(('127.0.0.1', 8765), Callback) as server:
        server.timeout = 1
        params = urllib.parse.urlencode(dict(client_id=app, redirect_uri=REDIRECT,
                                            response_type='code', state=state))
        webbrowser.open('https://api-t1.fyers.in/api/v3/generate-authcode?' + params)
        print('Complete FYERS authorization in your browser. Waiting up to five minutes.')
        deadline = time.monotonic() + 300
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if result.get('error'):
        raise SystemExit('No connection completed. ' + result['error'])
    if not result.get('ok'):
        raise SystemExit('No token saved. Authorization timed out or failed.')
    print('Saved securely. Restart the website. FYERS tokens require daily renewal.')


if __name__ == '__main__':
    main()

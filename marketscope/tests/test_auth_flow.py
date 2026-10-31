"""Real HTTP auth/session checks against a separate temporary database."""
from contextlib import ExitStack
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock, patch
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server as service
import manage as management
from security import DatabaseManager, RateLimiter, generate_totp_secret, get_totp_token


class QuietHandler(service.MarketScopeHandler):
    def log_message(self, *args):
        pass


class AuthHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = DatabaseManager(str(Path(self.tmp.name)/"auth.db"))
        self.secret = generate_totp_secret()
        self.uid = self.db.create_user("owner@example.test", "test-only-owner-password", role="owner", totp_secret=self.secret)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(service,"rate_limiter",RateLimiter(1000)))
        self.patches.enter_context(patch.object(service,"rate_limiter_login",RateLimiter(1000)))
        self.patches.enter_context(patch.object(service,"get_stock_database",return_value=MagicMock(get_stock_count=lambda:24)))
        self.httpd = ThreadingHTTPServer(("127.0.0.1",0),QuietHandler)
        self.httpd.db_manager = self.db
        self.httpd.signal_journal = object()
        self.thread = Thread(target=self.httpd.serve_forever,kwargs={"poll_interval":.02},daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)
        self.patches.close()
        self.tmp.cleanup()

    def request(self, path, body=None, cookie=None, csrf=None, origin=None, raw=None):
        conn = HTTPConnection("127.0.0.1",self.httpd.server_port,timeout=5)
        headers = {"Content-Type":"application/json"}
        if cookie: headers["Cookie"] = cookie
        if csrf: headers["X-CSRF-Token"] = csrf
        if origin: headers["Origin"] = origin
        try:
            conn.request("POST" if body is not None or raw is not None else "GET",path,
                         raw if raw is not None else json.dumps(body) if body is not None else None,headers)
            response = conn.getresponse()
            data = response.read()
            return response.status,dict(response.getheaders()),json.loads(data) if "application/json" in response.getheader("Content-Type","") else data.decode()
        finally:
            conn.close()

    def login(self):
        token,_ = get_totp_token(self.secret)
        status,headers,payload = self.request("/api/login",{"email":"OWNER@EXAMPLE.TEST ","password":"test-only-owner-password","totp_code":token})
        self.assertEqual(status,200,payload)
        return headers["Set-Cookie"].split(";",1)[0],payload["csrf_token"]

    def test_password_first_mfa_challenge_and_replay(self):
        credentials = {"email":"owner@example.test","password":"test-only-owner-password"}
        status,_,payload = self.request("/api/login",credentials)
        self.assertEqual(status,400)
        self.assertTrue(payload["requires_mfa"])
        cookie,_ = self.login()
        token,_ = get_totp_token(self.secret)
        status,_,payload = self.request("/api/login",dict(credentials,totp_code=token))
        self.assertEqual(status,401)
        self.assertTrue(payload["requires_mfa"])
        self.assertIsNotNone(self.db.validate_session(cookie.split("=",1)[1]))

    def test_non_mfa_member_can_sign_in_without_dummy_authenticator_code(self):
        self.db.create_user("member@example.test","member-test-password",role="member")
        status,_,payload = self.request("/api/login",{"email":"member@example.test","password":"member-test-password"})
        self.assertEqual(status,200,payload)
        self.assertEqual(payload["user"]["role"],"member")

    def test_cookie_bootstrap_csrf_and_logout_lifecycle(self):
        cookie,csrf = self.login()
        status,headers,payload = self.request("/api/bootstrap",cookie=cookie)
        self.assertEqual(status,200)
        self.assertTrue(payload["session"]["authenticated"])
        self.assertEqual(payload["session"]["csrf_token"],csrf)
        self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertEqual(self.request("/api/logout",{},cookie=cookie)[0],403)
        self.assertTrue(self.request("/api/bootstrap",cookie=cookie)[2]["session"]["authenticated"])
        status,headers,_ = self.request("/api/logout",{},cookie=cookie,csrf=csrf)
        self.assertEqual(status,200)
        self.assertIn("Max-Age=0",headers["Set-Cookie"])
        self.assertFalse(self.request("/api/bootstrap",cookie=cookie)[2]["session"]["authenticated"])

    def test_cookie_http_flags_and_optional_secure_setting(self):
        with patch.dict(os.environ,{"MS_COOKIE_SECURE":"true"}):
            code,_ = get_totp_token(self.secret)
            status,headers,_ = self.request("/api/login",{"email":"owner@example.test","password":"test-only-owner-password","totp_code":code})
        self.assertEqual(status,200)
        for flag in ("HttpOnly","SameSite=Strict","Path=/","Secure"):
            self.assertIn(flag,headers["Set-Cookie"])

    def test_invalid_types_and_json_receive_http_errors(self):
        for raw in ('[]','"not an object"','{broken', '{"email":null,"password":[]}', '{}'):
            self.assertEqual(self.request("/api/login",raw=raw)[0],400)
        status,_,payload = self.request("/api/login",{"email":"unknown@example.test","password":"wrong"})
        self.assertEqual(status,401)
        self.assertNotIn("requires_mfa",payload)

    def test_login_throttling_and_cross_origin_rejection(self):
        with patch.object(service,"rate_limiter_login",RateLimiter(1,60)):
            request={"email":"unknown@example.test","password":"wrong"}
            self.assertEqual(self.request("/api/login",request)[0],401)
            status,headers,_ = self.request("/api/login",request)
            self.assertEqual(status,429)
            self.assertIn("Retry-After",headers)
        self.assertEqual(self.request("/api/login",request,origin="http://external.example")[0],403)

    def test_expired_and_disabled_sessions_are_rejected(self):
        cookie,_ = self.login()
        with self.db.get_connection() as conn:
            conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
        self.assertFalse(self.request("/api/bootstrap",cookie=cookie)[2]["session"]["authenticated"])
        token,_ = self.db.create_session(self.uid)
        with self.db.get_connection() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE id=?",(self.uid,))
        self.assertIsNone(self.db.validate_session(token))

    def test_preview_is_protected_and_malformed_config_returns_400(self):
        self.assertEqual(self.request("/api/config/preview",{"fast_ema_period":"broken"})[0],401)
        cookie,csrf = self.login()
        self.assertEqual(self.request("/api/config/preview",{"fast_ema_period":"broken"},cookie,csrf)[0],400)

    def test_backup_contains_committed_wal_rows(self):
        cookie,csrf = self.login()
        with patch.object(service,"BASE_DIR",Path(self.tmp.name)/"marketscope"):
            status,_,payload = self.request("/api/backup",{},cookie,csrf)
        self.assertEqual(status,200,payload)
        conn=sqlite3.connect(payload["path"])
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM users").fetchone()[0],1)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        finally:
            conn.close()


class AuthRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path=Path(self.tmp.name)/"auth.db"
        self.db=DatabaseManager(str(self.path))
        self.old=generate_totp_secret()
        self.uid=self.db.create_user("owner@example.test","test-only-owner-password",role="owner",totp_secret=self.old)

    def tearDown(self):
        self.tmp.cleanup()

    def test_noninteractive_recovery_refuses_before_credential_access(self):
        with patch.object(management.sys.stdin,"isatty",return_value=False), patch.object(management.getpass,"getpass") as prompt, patch("sys.stderr",new_callable=io.StringIO):
            with self.assertRaises(SystemExit): management.cmd_reset_mfa(type("Args",(),{"db":str(self.path)})())
        prompt.assert_not_called()

    def test_failed_password_keeps_enrollment_and_sessions(self):
        token,_=self.db.create_session(self.uid)
        with patch.object(management.sys.stdin,"isatty",return_value=True),patch.object(management.sys.stdout,"isatty",return_value=True),patch.object(management.getpass,"getpass",return_value="wrong"),patch("sys.stderr",new_callable=io.StringIO):
            with self.assertRaises(SystemExit):management.cmd_reset_mfa(type("Args",(),{"db":str(self.path)})())
        self.assertIsNotNone(self.db.validate_session(token))

    def test_confirmed_new_enrollment_revokes_sessions_and_records_audit(self):
        token,_=self.db.create_session(self.uid)
        new=generate_totp_secret()
        code,_=get_totp_token(new)
        with patch.object(management.sys.stdin,"isatty",return_value=True),patch("sys.stdout",new_callable=io.StringIO) as output,patch.object(management.getpass,"getpass",side_effect=["test-only-owner-password",code]),patch("builtins.input",return_value="RESET"),patch.object(management,"generate_totp_secret",return_value=new):
            output.isatty=lambda:True
            management.cmd_reset_mfa(type("Args",(),{"db":str(self.path)})())
        self.assertIsNone(self.db.validate_session(token))
        with self.db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT totp_secret FROM users").fetchone()[0],new)
            self.assertEqual(conn.execute("SELECT count(*) FROM audit WHERE action='owner_mfa_reenrolled'").fetchone()[0],1)


if __name__ == "__main__":unittest.main()


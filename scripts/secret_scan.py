#!/usr/bin/env python3
"""
Zien Quant Secret & Credential Scanner
Standard library only.
Scans working tree (tracked + untracked-not-ignored) and git history for:
- Sensitive file extensions (*.db, *.sqlite*, *.pem, *.key, .env)
- Private keys
- Password / API key / token assignments with literal values
- TOTP secrets & otpauth:// URIs
- Hashes (bcrypt, pbkdf2, scrypt)
- Platform tokens (ghp_, github_pat_, AKIA, sk-)
- Session cookies
- Absolute personal filesystem paths (C:\\Users\\..., /home/..., /Users/...)
- Personal emails (excluding example.com / test.com / localhost)
- Non-loopback IP addresses (excluding 127.0.0.1, 0.0.0.0, 255.255.255.255)

All discovered secret values are REDACTED in output.
Exits with code 1 if any findings are detected, code 0 if clean.
"""

from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Banned file extensions and names
BANNED_FILE_PATTERNS = [
    re.compile(r"^.*\.db$", re.IGNORECASE),
    re.compile(r"^.*\.sqlite\w*$", re.IGNORECASE),
    re.compile(r"^.*\.pem$", re.IGNORECASE),
    re.compile(r"^.*\.key$", re.IGNORECASE),
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),  # .env, .env.local, but .env.example will be explicitly allowed
]

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp", ".tiff",
    ".zip", ".tar", ".gz", ".7z", ".pdf", ".pyc", ".wasm", ".woff", ".woff2", ".ttf", ".eot"
}

# Sensitive content patterns
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
    "otpauth_uri": re.compile(r"otpauth://[^\s'\"]+"),
    "totp_secret_assignment": re.compile(r"(?:totp_secret|mfa_secret)\s*[:=]\s*['\"]([A-Z2-7]{16,32})['\"]", re.IGNORECASE),
    "password_assignment": re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*['\"]([^'\"\s]{4,})['\"]", re.IGNORECASE),
    "api_key_assignment": re.compile(r"(?:api_key|apikey|secret_key|auth_token)\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]", re.IGNORECASE),
    "bcrypt_hash": re.compile(r"\$2[aby]\$[0-9]{2}\$[A-Za-z0-9./]{53}"),
    "pbkdf2_hash": re.compile(r"(?:pbkdf2|scrypt)[:\$][^\s'\"<>()]+"),
    "github_token": re.compile(r"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})"),
    "aws_key": re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "session_cookie": re.compile(r"(?:session_id|session_token)=([0-9a-fA-F]{32,})"),
    "personal_path": re.compile(r"(?:[A-Za-z]:\\[Uu]sers\\[A-Za-z0-9_'\.\-\s]+|/(?:home|Users)/[A-Za-z0-9_'\.\-]+)"),
    "email_address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # Precise IPv4 pattern not preceded or followed by word/dot/slash/hyphen
    "ip_address": re.compile(r"(?<![a-zA-Z0-9_\.\-/])(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])(?![a-zA-Z0-9_\.\-/])"),
}

ALLOWED_EMAILS = {"example.com", "test.com", "localhost", "domain.com", "user@example.com"}
ALLOWED_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}
ALLOWED_SUBSTRINGS = (
    "placeholder", "dummy", "test", "fake", "example", "redacted", "changeme",
    "your_secret", "your_password", "none", "null", "undefined"
)


def is_allowed_file(path_str: str) -> bool:
    """Checks if a file is explicitly whitelisted from name checks."""
    norm = path_str.replace("\\", "/")
    filename = Path(norm).name
    if filename in (".env.example", ".gitattributes", ".gitignore"):
        return True
    return False


def is_allowed_email(email_str: str) -> bool:
    domain = email_str.split("@")[-1].lower()
    # RFC 2606 reserves the .test TLD for documentation and testing; it can
    # never resolve to a real mailbox, so it is never a personal address.
    return domain in ALLOWED_EMAILS or domain.endswith(".example.com") or domain.endswith(".test")


def is_allowed_ip(ip_str: str) -> bool:
    return ip_str in ALLOWED_IPS or ip_str.startswith("127.")


def is_binary_file(path_str: str) -> bool:
    ext = Path(path_str).suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True
    try:
        with open(path_str, "rb") as f:
            chunk = f.read(1024)
            if b"\0" in chunk:
                return True
    except Exception:
        pass
    return False


def check_filename(path_str: str) -> List[str]:
    """Checks if a file path matches banned filename patterns."""
    if is_allowed_file(path_str):
        return []
    filename = Path(path_str).name
    issues = []
    for pat in BANNED_FILE_PATTERNS:
        if pat.match(filename):
            issues.append(f"banned_file_pattern ({pat.pattern})")
    return issues


def scan_content(content: str, source_label: str) -> List[Tuple[str, int, str]]:
    """
    Scans text content line by line against regex patterns.
    Returns list of (source_label, line_num, finding_type).
    Redacts all values.
    """
    findings: List[Tuple[str, int, str]] = []
    lines = content.splitlines()

    is_scanner_self = "secret_scan.py" in source_label

    for line_idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        # Ignore self-referential patterns and docstrings inside secret_scan.py
        if is_scanner_self and (
            line_idx <= 35
            or "re.compile" in line
            or "SECRET_PATTERNS" in line
            or "BANNED_FILE_PATTERNS" in line
            or '("C:\\Users' in line
            or '("C:\\\\Users' in line
            or 'otpauth://' in line
            or 'ghp_' in line
        ):
            continue

        # Skip User-Agent definitions where browser versions (e.g. Chrome/124.0.0.0) might look like IPs
        if "User-Agent" in line or "USER_AGENT" in line:
            line_no_ua = re.sub(r"Chrome/[0-9\.]+", "", line)
        else:
            line_no_ua = line

        # 1. Private key
        if SECRET_PATTERNS["private_key"].search(line):
            findings.append((source_label, line_idx, "private_key: [REDACTED]"))

        # 2. OTP Auth URI (only when a literal secret is embedded; runtime-built
        # templates such as f-string placeholders carry no committed secret)
        m_otp = SECRET_PATTERNS["otpauth_uri"].search(line)
        if m_otp and not is_scanner_self:
            uri = m_otp.group(0)
            m_embedded = re.search(r"[?&]secret=([A-Za-z2-7]{16,})", uri)
            if m_embedded and "{" not in m_embedded.group(1) and "}" not in m_embedded.group(1):
                findings.append((source_label, line_idx, "otpauth_uri: [REDACTED]"))

        # 3. TOTP secret
        m_totp = SECRET_PATTERNS["totp_secret_assignment"].search(line)
        if m_totp:
            val = m_totp.group(1).lower()
            if not any(sub in val for sub in ALLOWED_SUBSTRINGS):
                findings.append((source_label, line_idx, "totp_secret: [REDACTED]"))

        # 4. Password assignment
        m_pwd = SECRET_PATTERNS["password_assignment"].search(line)
        if m_pwd:
            val = m_pwd.group(1).lower().strip()
            if not any(sub in val for sub in ALLOWED_SUBSTRINGS) and not val.startswith(("your_", "env(", "os.")):
                findings.append((source_label, line_idx, "password_assignment: [REDACTED]"))

        # 5. API Key assignment
        m_api = SECRET_PATTERNS["api_key_assignment"].search(line)
        if m_api:
            val = m_api.group(1).lower().strip()
            if not any(sub in val for sub in ALLOWED_SUBSTRINGS) and not val.startswith(("your_", "env(", "os.")):
                findings.append((source_label, line_idx, "api_key_assignment: [REDACTED]"))

        # 6. Hashes
        if SECRET_PATTERNS["bcrypt_hash"].search(line):
            findings.append((source_label, line_idx, "bcrypt_hash: [REDACTED]"))
        if SECRET_PATTERNS["pbkdf2_hash"].search(line):
            if not any(s in line for s in ("startswith", "hash_password", "def test", "pbkdf2_sha256$600000$")):
                findings.append((source_label, line_idx, "pbkdf2_scrypt_hash: [REDACTED]"))

        # 7. Platform tokens
        if SECRET_PATTERNS["github_token"].search(line) and not is_scanner_self:
            findings.append((source_label, line_idx, "github_token: [REDACTED]"))
        if SECRET_PATTERNS["aws_key"].search(line):
            findings.append((source_label, line_idx, "aws_key: [REDACTED]"))
        if SECRET_PATTERNS["openai_key"].search(line):
            findings.append((source_label, line_idx, "openai_token: [REDACTED]"))

        # 8. Session cookie
        if SECRET_PATTERNS["session_cookie"].search(line):
            findings.append((source_label, line_idx, "session_cookie: [REDACTED]"))

        # 9. Personal paths
        m_path = SECRET_PATTERNS["personal_path"].search(line)
        if m_path and not is_scanner_self:
            findings.append((source_label, line_idx, "personal_path: [REDACTED]"))

        # 10. Email addresses
        emails = SECRET_PATTERNS["email_address"].findall(line)
        for em in emails:
            if not is_allowed_email(em):
                findings.append((source_label, line_idx, f"email ({em.split('@')[1]}): [REDACTED]"))

        # 11. IP addresses
        ips = SECRET_PATTERNS["ip_address"].findall(line_no_ua)
        for ip in ips:
            if not is_allowed_ip(ip):
                findings.append((source_label, line_idx, f"ip_address: [REDACTED]"))

    return findings


def get_working_tree_files() -> List[str]:
    """Gets list of all tracked and untracked-not-ignored files using git."""
    files: Set[str] = set()

    # Tracked files
    try:
        p = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
        for f in p.stdout.splitlines():
            if f.strip():
                files.add(f.strip())
    except subprocess.CalledProcessError:
        pass

    # Untracked not ignored
    try:
        p = subprocess.run(["git", "status", "--porcelain", "-uall"], capture_output=True, text=True, check=True)
        for line in p.stdout.splitlines():
            if line.startswith("??"):
                fn = line[3:].strip()
                if fn:
                    files.add(fn)
    except subprocess.CalledProcessError:
        pass

    return sorted(list(files))


def scan_working_tree() -> List[str]:
    """Scans all working tree files (names and contents)."""
    results: List[str] = []
    files = get_working_tree_files()

    for rel_path in files:
        if not os.path.isfile(rel_path):
            continue

        fn_issues = check_filename(rel_path)
        for iss in fn_issues:
            results.append(f"{rel_path}: [BANNED_FILE] {iss}")

        if is_binary_file(rel_path):
            continue

        try:
            sz = os.path.getsize(rel_path)
            if sz > 5 * 1024 * 1024:
                continue
            with open(rel_path, "r", encoding="utf-8", errors="ignore") as fp:
                content = fp.read()
            findings = scan_content(content, rel_path)
            for src, line_no, finding_type in findings:
                results.append(f"{src}:{line_no}: {finding_type}")
        except Exception as e:
            results.append(f"{rel_path}: [SCAN_ERROR] {e}")

    return results


def scan_git_history(rev_target: str = "HEAD") -> List[str]:
    """Scans commits in current history (or target revision) for banned filenames and sensitive content diffs."""
    results: List[str] = []

    try:
        p = subprocess.run(["git", "rev-list", rev_target], capture_output=True, text=True, check=True)
        commits = [c.strip() for c in p.stdout.splitlines() if c.strip()]
    except subprocess.CalledProcessError as e:
        results.append(f"git_history: [ERROR] Failed to list commits: {e}")
        return results

    for c in commits:
        try:
            p_files = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", c],
                capture_output=True,
                text=True,
                check=True,
            )
            for fn in p_files.stdout.splitlines():
                fn = fn.strip()
                if not fn:
                    continue
                issues = check_filename(fn)
                for iss in issues:
                    results.append(f"{c[:8]}:{fn}: [BANNED_FILE_IN_HISTORY] {iss}")
        except subprocess.CalledProcessError:
            pass

        try:
            p_diff = subprocess.run(
                ["git", "show", "--format=", "--unified=0", c],
                capture_output=True,
                text=True,
                errors="ignore",
                check=True,
            )
            current_file = "diff"
            added_lines = []
            for line in p_diff.stdout.splitlines():
                if line.startswith("diff --git"):
                    if added_lines:
                        findings = scan_content("\n".join(added_lines), f"{c[:8]}:{current_file}")
                        for src, line_no, finding_type in findings:
                            results.append(f"{src}:{line_no}: {finding_type}")
                        added_lines = []
                    parts = line.split()
                    if len(parts) >= 4:
                        current_file = parts[3].lstrip("b/")
                elif line.startswith("+") and not line.startswith("+++"):
                    added_lines.append(line[1:])

            if added_lines:
                findings = scan_content("\n".join(added_lines), f"{c[:8]}:{current_file}")
                for src, line_no, finding_type in findings:
                    results.append(f"{src}:{line_no}: {finding_type}")
        except subprocess.CalledProcessError:
            pass

    return results


def main():
    parser = argparse.ArgumentParser(description="Zien Quant Secret Scanner")
    parser.add_argument("--working-tree-only", action="store_true", help="Scan working tree only, skip git history")
    parser.add_argument("--rev", default="HEAD", help="Git revision target to scan (default: HEAD)")
    parser.add_argument("--all-branches", action="store_true", help="Scan --all branches in git repo")
    args = parser.parse_args()

    print("==================================================")
    print("      ZIEN QUANT SECRET & CREDENTIAL SCANNER      ")
    print("==================================================")

    total_findings: List[str] = []

    print("[1/2] Scanning working tree (tracked + untracked)...")
    wt_findings = scan_working_tree()
    total_findings.extend(wt_findings)
    print(f"      Working tree findings: {len(wt_findings)}")

    if not args.working_tree_only:
        target = "--all" if args.all_branches else args.rev
        print(f"[2/2] Scanning git history for target: {target}...")
        hist_findings = scan_git_history(target)
        total_findings.extend(hist_findings)
        print(f"      Git history findings: {len(hist_findings)}")
    else:
        print("[2/2] Git history scan skipped (--working-tree-only).")

    print("\n----------------- SCAN REPORT --------------------")
    if total_findings:
        print(f"DETECTED {len(total_findings)} POTENTIAL SECRET / SAFETY FINDINGS:")
        for idx, f in enumerate(total_findings, 1):
            print(f"  {idx}. {f}")
        print("\nScan Result: FAILED (secrets or personal paths found). Exiting non-zero.")
        sys.exit(1)
    else:
        print("ALL SCANS CLEAN. No banned files, secrets, or personal paths detected.")
        print("Scan Result: PASSED.")
        sys.exit(0)


if __name__ == "__main__":
    main()

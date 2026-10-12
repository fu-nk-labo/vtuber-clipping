"""Heuristic scan of Git's proposed tracked tree. Never prints matched secrets.
Not a security audit or proof that a tree is free of sensitive information.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{35,})\b'),
    'cloud-access-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'bearer-token': re.compile(r'Bearer\s+[A-Za-z0-9._~+/=-]{24,}'),
    'assigned-secret': re.compile(r'''(?i)(?:api_key|client_secret|password|access_token)["']?\s*[:=]\s*["'][A-Za-z0-9._~+/=-]{20,}["']'''),
    'private-absolute-path': re.compile(r'/(?:workspace|home|root)/[A-Za-z0-9._/-]+|[A-Z]:\\Users\\[^\s]+'),
    'private-session-link': re.compile(r'codex[:][/][/]threads[/][a-f0-9-]{20,}'),
}
DENIED_SUFFIXES={'.mp4','.mov','.mkv','.wav','.mp3','.zip','.whl','.pem','.key'}


def main():
    result=subprocess.run(['git','ls-files','-z'],cwd=ROOT,check=True,capture_output=True)
    paths=[p for p in result.stdout.decode().split('\0') if p]
    if not paths:
        print('No proposed tracked files. Stage the intended source tree before scanning.');return 2
    findings=[]
    for name in paths:
        path=ROOT/name
        if path.is_symlink():findings.append((name,'symlink'));continue
        if path.suffix.lower() in DENIED_SUFFIXES or path.name.startswith('.env'):
            findings.append((name,'unexpected artifact/secret file'))
        if path.stat().st_size>512000:findings.append((name,'large tracked file'))
        try:text=path.read_text(encoding='utf-8')
        except UnicodeError:findings.append((name,'non-text tracked file'));continue
        for label,pattern in PATTERNS.items():
            if pattern.search(text):findings.append((name,label))
    for name,label in findings:print(f'REVIEW {name}: {label}')
    print(f'Scanned {len(paths)} proposed tracked files; {len(findings)} heuristic findings')
    return 1 if findings else 0

if __name__=='__main__':sys.exit(main())

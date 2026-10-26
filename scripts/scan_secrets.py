"""Inspect Git-visible files without printing credential values. Heuristic, not a security audit."""
from pathlib import Path
import subprocess,re,sys
root=Path(__file__).resolve().parents[1]
result=subprocess.run(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root,capture_output=True,check=True)
patterns=[re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),re.compile(r'AKIA[0-9A-Z]{16}'),re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{30,}')]
findings=[];checked=0
for name in set(result.stdout.decode().split('\0')):
 if not name:continue
 p=root/name
 if not p.is_file() or p.stat().st_size>5_000_000:continue
 try:text=p.read_text(encoding='utf-8')
 except (UnicodeError,OSError):continue
 checked+=1
 if any(pattern.search(text) for pattern in patterns):findings.append(name)
print(f'Checked {checked} text files; {len(findings)} potential credential findings. Heuristic scan only.')
for name in findings:print(name)
sys.exit(bool(findings))

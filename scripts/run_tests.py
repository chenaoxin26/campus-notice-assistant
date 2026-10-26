"""Run isolated fake-data suites; never open QQ or enumerate notifications."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
failed=False
for args,cwd in [(["-m","unittest","discover","-s",".","-p","test_*.py","-v"],root),(["-m","unittest","discover","-s","tests","-v"],root/"collector")]:
    result=subprocess.run([sys.executable,*args],cwd=cwd)
    failed=failed or bool(result.returncode)
raise SystemExit(1 if failed else 0)

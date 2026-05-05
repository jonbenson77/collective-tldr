#!/usr/bin/env python3
"""Run the brief generator for all active members."""
import subprocess
import sys

MEMBERS = [
    ("Jon Benson", "jb@bnsn.ai"),
    ("Ben Cope", "bcope1@gmail.com"),
]

for name, email in MEMBERS:
    print(f"\n{'='*60}")
    print(f"Generating brief for {name} ({email})")
    print('='*60)
    result = subprocess.run(
        [sys.executable, "generate-brief.py", "--name", name, "--email", email],
        check=False,
    )
    if result.returncode != 0:
        print(f"WARNING: brief for {email} failed", file=sys.stderr)

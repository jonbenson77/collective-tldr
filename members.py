#!/usr/bin/env python3
"""Member registry. Usage: python members.py name <email>"""
import sys

MEMBERS = {
    "jb@bnsn.ai": {"name": "Jon Benson"},
    "bcope1@gmail.com": {"name": "Ben Cope"},
}

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "name":
        email = sys.argv[2]
        print(MEMBERS.get(email, {}).get("name", "Member"))

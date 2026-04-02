#!/usr/bin/env python3
"""Generate bcrypt password hash for config.yaml users."""
import sys
import bcrypt

if len(sys.argv) < 2:
    print("Usage: python gen_password.py <password>")
    sys.exit(1)

password = sys.argv[1].encode("utf-8")
hashed = bcrypt.hashpw(password, bcrypt.gensalt())
print(f"\nPassword hash (paste into config.yaml):\n{hashed.decode()}\n")

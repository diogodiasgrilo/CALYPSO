#!/usr/bin/env python3
"""Operator CLI for HYDRA dashboard accounts.

Run over SSH as the `calypso` user on the VM (matches how every other
operational task in this repo is done — no web admin panel). Deliberately
NOT a self-service sign-up page: the number of accounts can grow over time,
but only an operator with VM access creates them.

Usage (from /opt/calypso, with the venv active):
    .venv/bin/python -m scripts.manage_dashboard_users add <username>
    .venv/bin/python -m scripts.manage_dashboard_users list
    .venv/bin/python -m scripts.manage_dashboard_users disable <username>
    .venv/bin/python -m scripts.manage_dashboard_users enable <username>
    .venv/bin/python -m scripts.manage_dashboard_users reset-password <username>
    .venv/bin/python -m scripts.manage_dashboard_users reset-2fa <username>

`add` prints a strong random temp password ONCE — relay it to the person
out-of-band (text/call/in person), never through the dashboard itself. They
are forced to change it and enroll in TOTP 2FA on first login.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.backend.config import settings  # noqa: E402
from dashboard.backend.services import auth_crypto, auth_db  # noqa: E402


def _fmt_ts(value):
    if not value:
        return "-"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def cmd_add(args):
    db_path = settings.dashboard_auth_db
    auth_db.init_db(db_path)
    if auth_db.get_user_by_username(db_path, args.username):
        print(f"Error: user '{args.username}' already exists.", file=sys.stderr)
        return 1
    temp_password = auth_crypto.generate_temp_password()
    auth_db.create_user(db_path, args.username, auth_crypto.hash_password(temp_password))
    print(f"Created account '{args.username}'.")
    print(f"Temp password (shown once — relay it out-of-band): {temp_password}")
    print("They will be forced to set a new password and enroll in 2FA on first login.")
    return 0


def cmd_list(args):
    db_path = settings.dashboard_auth_db
    auth_db.init_db(db_path)
    users = auth_db.list_users(db_path)
    if not users:
        print("No accounts configured. The dashboard login gate is currently DISABLED (dev-mode).")
        return 0
    print(f"{'username':<20} {'active':<8} {'2fa':<6} {'failed':<8} {'locked':<10} {'last login'}")
    for u in users:
        locked = "yes" if u["locked_until"] and u["locked_until"] > __import__("time").time() else "no"
        print(
            f"{u['username']:<20} {'yes' if u['is_active'] else 'no':<8} "
            f"{'yes' if u['totp_enabled'] else 'no':<6} {u['failed_login_count']:<8} "
            f"{locked:<10} {_fmt_ts(u['last_login_at'])}"
        )
    return 0


def cmd_disable(args):
    db_path = settings.dashboard_auth_db
    if not auth_db.set_active(db_path, args.username, False):
        print(f"Error: no such user '{args.username}'.", file=sys.stderr)
        return 1
    print(f"Disabled '{args.username}' and revoked all their active sessions.")
    return 0


def cmd_enable(args):
    db_path = settings.dashboard_auth_db
    if not auth_db.set_active(db_path, args.username, True):
        print(f"Error: no such user '{args.username}'.", file=sys.stderr)
        return 1
    print(f"Enabled '{args.username}'.")
    return 0


def cmd_reset_password(args):
    db_path = settings.dashboard_auth_db
    temp_password = auth_crypto.generate_temp_password()
    if not auth_db.admin_reset_password(db_path, args.username, auth_crypto.hash_password(temp_password)):
        print(f"Error: no such user '{args.username}'.", file=sys.stderr)
        return 1
    print(f"Reset password for '{args.username}' and revoked their active sessions.")
    print(f"New temp password (shown once — relay it out-of-band): {temp_password}")
    print("They will be forced to set a new password on next login.")
    return 0


def cmd_reset_2fa(args):
    db_path = settings.dashboard_auth_db
    if not auth_db.reset_totp(db_path, args.username):
        print(f"Error: no such user '{args.username}'.", file=sys.stderr)
        return 1
    print(
        f"Cleared 2FA for '{args.username}' and revoked their active sessions — "
        "they will re-enroll (scan a new QR) on next login."
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Create a new account with a random temp password")
    p_add.add_argument("username")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List all accounts")
    p_list.set_defaults(func=cmd_list)

    p_disable = sub.add_parser("disable", help="Disable an account (kicks any active session immediately)")
    p_disable.add_argument("username")
    p_disable.set_defaults(func=cmd_disable)

    p_enable = sub.add_parser("enable", help="Re-enable a disabled account")
    p_enable.add_argument("username")
    p_enable.set_defaults(func=cmd_enable)

    p_resetpw = sub.add_parser(
        "reset-password", help="Force a new temp password (lost/compromised password)"
    )
    p_resetpw.add_argument("username")
    p_resetpw.set_defaults(func=cmd_reset_password)

    p_reset = sub.add_parser("reset-2fa", help="Clear 2FA enrollment (lost-phone recovery)")
    p_reset.add_argument("username")
    p_reset.set_defaults(func=cmd_reset_2fa)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

"""
Terminal review tool for feedback.Report rows - the only way to change
`status` or `is_public` today (no admin web UI yet, see CLAUDE.md: that
comes later, after auth exists, as a protected route rather than a new
unauthenticated-access problem).

List pending/all feedback, ordered by created_at:

    python scripts/review_feedback.py list [--all]

(by default only shows rows with status != resolved/wont_fix; --all
shows everything, including already-closed rows)

Mark a row's status and/or publish it:

    python scripts/review_feedback.py set <id> [--status open|in_progress|resolved|wont_fix] [--public true|false]

At least one of --status/--public must be given. Marking a row
is_public=true is the ONLY way it can ever appear on GET /feedback/public
(and even then, only category/llm_summary/status/created_at are ever
exposed - raw_text never leaves this CLI/the database).
"""
import argparse
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Feedback, FeedbackStatus
from huntloop.settings import DATABASE_URL


def _make_session():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    return Session(), engine


def cmd_list(args) -> None:
    session, engine = _make_session()
    try:
        query = session.query(Feedback).order_by(Feedback.created_at.asc())
        if not args.all:
            query = query.filter(
                Feedback.status.notin_([FeedbackStatus.RESOLVED, FeedbackStatus.WONT_FIX])
            )
        rows = query.all()
        if not rows:
            print("No feedback rows to show (use --all to include resolved/wont_fix rows).")
            return
        for row in rows:
            summary = row.llm_summary or "(not yet triaged)"
            print(
                f"[{row.id}] {row.created_at:%Y-%m-%d %H:%M} | {row.category.value:8s} | "
                f"triage={row.triage_status.value:14s} | status={row.status.value:11s} | "
                f"public={row.is_public!s:5s} | {summary}"
            )
    finally:
        session.close()
        engine.dispose()


def cmd_set(args) -> None:
    if args.status is None and args.public is None:
        print("Nothing to do - pass --status and/or --public.", file=sys.stderr)
        sys.exit(1)

    session, engine = _make_session()
    try:
        row = session.query(Feedback).filter(Feedback.id == args.id).first()
        if row is None:
            print(f"No feedback row with id={args.id}", file=sys.stderr)
            sys.exit(1)

        if args.status is not None:
            row.status = FeedbackStatus(args.status)
        if args.public is not None:
            row.is_public = args.public == "true"

        session.commit()
        print(
            f"Updated feedback id={row.id}: status={row.status.value}, is_public={row.is_public}"
        )
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List feedback rows.")
    list_parser.add_argument("--all", action="store_true", help="Include resolved/wont_fix rows too.")
    list_parser.set_defaults(func=cmd_list)

    set_parser = sub.add_parser("set", help="Update a row's status and/or is_public.")
    set_parser.add_argument("id", type=int)
    set_parser.add_argument("--status", choices=[s.value for s in FeedbackStatus], default=None)
    set_parser.add_argument("--public", choices=["true", "false"], default=None)
    set_parser.set_defaults(func=cmd_set)

    parsed = parser.parse_args()
    parsed.func(parsed)

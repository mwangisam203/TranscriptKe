"""Read-only deployment checks: python -m app.readiness [--live]."""

import argparse
import json

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Check requirements for the live paid-delivery pilot",
    )
    args = parser.parse_args()
    try:
        from app.db.session import SessionLocal
        from app.services.readiness import report

        with SessionLocal() as db:
            result = report(db, live=args.live)
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "configuration_ready": False,
                    "configuration_errors": [
                        error["msg"]
                        for error in exc.errors(
                            include_input=False,
                            include_context=False,
                            include_url=False,
                        )
                    ],
                }
            )
        )
        return 1
    except SQLAlchemyError:
        print(
            json.dumps(
                {
                    "configuration_ready": False,
                    "error": "Database checks failed; verify connectivity and migrations.",
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["configuration_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

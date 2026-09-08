#!/usr/bin/env python3
"""Verify an enrolled account; every error returns failure to PAM."""
import argparse
import os
import sys

from facelock import auth, cli, drift, feedback, store, telemetry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    cli.arguments(ap)
    ap.add_argument("--pam", action="store_true")
    args = ap.parse_args(argv)
    detail = {"accepted": False}
    cfg = None
    user = None
    reporter = None
    try:
        if args.pam and (args.config != cli.CONFIG or os.environ.get("PAM_TYPE") != "auth"):
            raise PermissionError("invalid PAM invocation")
        cfg = cli.load_config(args.config)
        user = cli.account(args.user, cfg, pam=args.pam)
        with cli.attempt(cfg):
            reporter = feedback.Reporter(user, cfg)
            reporter.send("scanning")
            refs, metadata = store.load(cfg["store"]["dir"], user)
            accepted, detail = auth.verify(cfg, refs, metadata)
            try:
                detail["reenroll_suggested"] = drift.update(cfg["store"]["dir"], user, detail)
            except (OSError, ValueError, TypeError):
                # Suggestions are optional and cannot affect authentication.
                detail["drift_unavailable"] = True
            reporter.send(feedback.result_state(detail), detail.get("reenroll_suggested", False))
        detail["accepted"] = bool(accepted)
    except Exception as exc:
        detail = {"accepted": False, "reason": str(exc), "error": type(exc).__name__}
        if reporter is not None:
            reporter.send("unavailable")
    if cfg is not None:
        telemetry.emit({"event": "verify", "user": user, **detail},
                       os.path.join(cfg["store"]["dir"], "attempts.jsonl"))
    if not args.quiet:
        cli.report(detail, args.json)
    return 0 if detail["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())

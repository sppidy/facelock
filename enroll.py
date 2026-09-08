#!/usr/bin/env python3
"""Guided enrollment with quality feedback, retries and an explicit save step."""
import argparse
import json
import select
import sys

from facelock import auth, cli, store, wizard


def confirm(stream, timeout=60):
    if not select.select([stream], [], [], timeout)[0]:
        return False
    return stream.readline().strip().lower() in ("y", "yes")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    cli.arguments(ap)
    ap.add_argument("--sensor", choices=("rgb", "ir", "both"),
                    help="must agree with auth.required_sensors")
    ap.add_argument("--yes", action="store_true", help="explicitly save without a final prompt")
    ap.add_argument("--progress-json", action="store_true", help="stream wizard events as JSON lines")
    ap.add_argument("--confirm-stdin", action="store_true", help="read the save decision from a GUI pipe")
    args = ap.parse_args(argv)
    def cancelled():
        return bool(args.confirm_stdin and select.select([sys.stdin], [], [], 0)[0]
                    and not sys.stdin.buffer.peek(1))
    def progress(event):
        if args.progress_json:
            print(json.dumps(event), flush=True)
        elif not args.quiet:
            print(wizard.describe(event), file=sys.stderr, flush=True)
    try:
        if not (args.yes or args.confirm_stdin or sys.stdin.isatty()):
            raise ValueError("enrollment needs confirmation; use a terminal or pass --yes")
        cfg = cli.load_config(args.config)
        user = cli.account(args.user, cfg)
        if args.sensor:
            wanted = ["rgb", "ir"] if args.sensor == "both" else [args.sensor]
            if set(wanted) != set(cfg["auth"]["required_sensors"]):
                raise ValueError("--sensor disagrees with auth.required_sensors; select a profile first")
        with cli.attempt(cfg):
            vectors, detail = wizard.collect(cfg, progress, cancelled=cancelled)
            detail["accepted"] = False
            if vectors is not None:
                ready = {"event": "ready", "user": user, "collected": 3,
                         "replacing": (store.ensure_dir(cfg["store"]["dir"]) / f"{user}.face").exists()}
                if args.progress_json:
                    print(json.dumps(ready), flush=True)
                elif not args.yes:
                    verb = "Replace existing" if ready["replacing"] else "Save"
                    print(f"{verb} enrollment for {user}? [y/N] (60 seconds)",
                          file=sys.stderr, flush=True)
                if args.yes or confirm(sys.stdin):
                    store.save(cfg["store"]["dir"], user,
                               metadata=auth.enrollment_metadata(cfg), **vectors)
                    detail.update(accepted=True, enrolled=user, sensors=list(vectors))
                else:
                    detail["reason"] = "enrollment-cancelled"
    except (Exception, KeyboardInterrupt) as exc:
        detail = {"accepted": False, "reason": str(exc) or "enrollment-cancelled",
                  "error": type(exc).__name__}
    if args.progress_json:
        print(json.dumps({"event": "result", **detail}), flush=True)
    elif not args.quiet:
        cli.report(detail, args.json)
    return 0 if detail["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())

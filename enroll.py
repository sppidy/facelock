#!/usr/bin/env python3
"""Enroll using the same capture, quality and illumination policy as verification."""
import argparse
import sys

from facelock import auth, cli, store


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    cli.arguments(ap)
    ap.add_argument("--sensor", choices=("rgb", "ir", "both"),
                    help="must agree with auth.required_sensors in the configuration")
    args = ap.parse_args(argv)
    try:
        cfg = cli.load_config(args.config)
        user = cli.account(args.user, cfg)
        if args.sensor:
            wanted = ["rgb", "ir"] if args.sensor == "both" else [args.sensor]
            if set(wanted) != set(cfg["auth"]["required_sensors"]):
                raise ValueError("--sensor disagrees with auth.required_sensors; select a profile first")
        with cli.attempt(cfg):
            vectors, detail = auth.enroll(cfg)
            if vectors is None:
                detail.update(accepted=False, reason=detail.get("reason", "enrollment-quality-or-illumination-failed"))
            else:
                store.save(cfg["store"]["dir"], user,
                           metadata=auth.enrollment_metadata(cfg), **vectors)
                detail.update(accepted=True, enrolled=user, sensors=list(vectors))
    except Exception as exc:
        detail = {"accepted": False, "reason": str(exc), "error": type(exc).__name__}
    if not args.quiet:
        cli.report(detail, args.json)
    return 0 if detail["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())

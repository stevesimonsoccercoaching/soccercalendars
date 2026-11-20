"""Plan or execute builds for all registered soccer events, without publishing.

From the repository root:
    python scripts/run_soccer_builds.py                 # read-only preview
    python scripts/run_soccer_builds.py --execute --base <commit> --head HEAD

The dispatcher discovers events with plan_soccer_builds, validates every
manifest, selects each event's existing builder, and generates the index once.
It NEVER commits, pushes, deploys, downloads XLSX, or changes GitHub settings.

For metadata-warning events, execution requires an explicit Git comparison.
An old-path/new-path move, missing historical XLSX, or uncommitted input
is rejected rather than silently skipping missing-match detection.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Keep preview mode from producing __pycache__ files in the repository.
sys.dont_write_bytecode = True
from plan_soccer_builds import plan
from soccer_paths import resolve_event_paths


class BuildDispatchError(RuntimeError):
    """A preflight failure that must stop every event build."""


def git_output(root, *args):
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False,
    )
    if proc.returncode:
        raise BuildDispatchError(
            f"Git preflight failed: git {' '.join(args)}: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def preflight_metadata_history(root, event, base, head):
    """Fail closed if EDP-style missing-match comparison would skip XLSX data."""
    
    manifest = Path(event["manifest"])
    registration = json.loads((root / manifest).read_text(encoding="utf-8"))

    paths = resolve_event_paths(
        manifest, registration["competition"], repo_root=root
    )

    event_paths = sorted(
        (paths.data_dir / f"{team['slug']}.xlsx")
        .relative_to(root)
        .as_posix()
        for team in registration["teams"]
    )

    for file_path in event_paths:
        # Existing tracker silently skips a workbook absent at either revision.
        # Check both sides explicitly before running it.
        for revision in (base, head):
            try:
                git_output(root, "cat-file", "-e", f"{revision}:{file_path}")
            except BuildDispatchError as exc:
                raise BuildDispatchError(
                    f"{event['name']}: {file_path} is absent at revision {revision}. "
                    "A new event or moved path needs a separate verified baseline; "
                    "do not run the missing-match sync across this transition."
                ) from exc
        # The missing-match tracker reads committed Git data; calendar builders
        # read working-tree files. They must refer to the same XLSX content.
        if subprocess.run(
            ["git", "diff", "--quiet", head, "--", file_path],
            cwd=root, check=False,
        ).returncode != 0:
            raise BuildDispatchError(
                f"{event['name']}: working-tree XLSX differs from {head}: {file_path}. "
                "Commit the input and choose that commit as --head first."
            )


def prepare(root, *, execute=False, base=None, head=None):
    """Return ordered event operations after read-only preflight checks."""
    root = Path(root).resolve()
    events, errors, warnings = plan(root)
    if errors:
        raise BuildDispatchError("Invalid build plan:\n  - " + "\n  - ".join(errors))

    # Validate every event BEFORE generating anything, including the first one.
    for event in events:
        result = subprocess.run(
            [sys.executable, "scripts/validate_soccer_manifest.py", event["manifest"]],
            cwd=root, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.returncode:
            raise BuildDispatchError(f"Manifest validation failed: {event['manifest']}")

    if execute and any(e["missing_match_sync"] for e in events):
        if not base or not head:
            raise BuildDispatchError(
                "--execute requires explicit --base and --head when metadata-warning "
                "events are registered. A missing-match comparison cannot be skipped."
            )
        # Resolve refs now, before any file writes, and require a forward history.
        base = git_output(root, "rev-parse", "--verify", f"{base}^{{commit}}")
        head = git_output(root, "rev-parse", "--verify", f"{head}^{{commit}}")
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, head],
                          cwd=root, check=False).returncode:
            raise BuildDispatchError("--base must be an ancestor of --head")
        checked_out = git_output(root, "rev-parse", "HEAD")
        if checked_out != head:
            raise BuildDispatchError("--head must refer to the checked-out commit")
        for event in events:
            if event["missing_match_sync"]:
                preflight_metadata_history(root, event, base, head)

    operations = []
    for event in events:
        manifest = event["manifest"]
        if event["missing_match_sync"]:
            if execute:
                sync = [sys.executable, "scripts/sync_missing_soccer_matches.py",
                        "--manifest", manifest, "--base", base, "--head", head]
            else:
                sync = [sys.executable, "scripts/sync_missing_soccer_matches.py",
                        "--manifest", manifest, "--base", "<required-base>",
                        "--head", "<required-head>"]
            operations.append((event["name"], "preserve-missing", sync))
        operations.append((event["name"], "build", [sys.executable, event["builder"], manifest]))
    operations.append(("all events", "index", [sys.executable, "scripts/build_calendar_index.py"]))
    return operations, events, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root")
    parser.add_argument("--execute", action="store_true", help="Generate local docs/state; never commit or publish")
    parser.add_argument("--base", help="Previous Git commit for metadata-warning match comparison")
    parser.add_argument("--head", help="Checked-out Git commit for metadata-warning match comparison")
    args = parser.parse_args()
    root = args.root.resolve()

    try:
        operations, events, warnings = prepare(root, execute=args.execute,
                                               base=args.base, head=args.head)
        for warning in warnings:
            print(f"WARNING: {warning}", flush=True)
        print(f"Validated {len(events)} events / {sum(e['teams'] for e in events)} teams", flush=True)
        for event_name, kind, command in operations:
            print(f"{'RUN' if args.execute else 'PLAN'} {event_name} [{kind}]: "
                  + " ".join(command), flush=True)
            if args.execute:
                subprocess.run(
                    command, cwd=root, check=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
    except (BuildDispatchError, subprocess.CalledProcessError, OSError) as exc:
        parser.exit(1, f"Build dispatch FAILED: {exc}\n")
    print("Local build complete; no publishing actions performed." if args.execute
          else "Read-only preview complete; no calendars or state written.")


if __name__ == "__main__":
    main()

"""PM CLI — Step-by-step project management pipeline.

Usage:
  # Full auto pipeline (existing)
  python3 -m toyshop.pm_cli run --name <project> --input <file_or_text> [--pm-root <dir>]
  python3 -m toyshop.pm_cli run-phased --name <project> --input <file_or_text> [--pm-root <dir>]

  # Step-by-step greenfield
  python3 -m toyshop.pm_cli create --name <project> --input <file_or_text> [--pm-root <dir>]
  python3 -m toyshop.pm_cli spec   --batch <batch_dir>
  python3 -m toyshop.pm_cli tasks  --batch <batch_dir>
  python3 -m toyshop.pm_cli tdd    --batch <batch_dir>

  # Step-by-step change (brownfield)
  python3 -m toyshop.pm_cli change-create  --name <project> --workspace <dir> --input <change_req> [--pm-root <dir>]
  python3 -m toyshop.pm_cli change-analyze --batch <batch_dir>
  python3 -m toyshop.pm_cli change-spec    --batch <batch_dir>
  python3 -m toyshop.pm_cli tdd            --batch <batch_dir>   # auto-detects mode

  # Utilities
  python3 -m toyshop.pm_cli status --batch <batch_dir>
  python3 -m toyshop.pm_cli resume --batch <batch_dir>
  python3 -m toyshop.pm_cli research-deadlock --batch <batch_dir> [--summary <text>] [--problem <file_or_text>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_run(args: argparse.Namespace) -> None:
    """Full auto: create → spec → tasks → tdd."""
    from toyshop.pm import run_batch
    from toyshop.llm import create_llm

    user_input = _read_input(args.input)
    llm = create_llm()
    batch = run_batch(Path(args.pm_root), args.name, user_input, llm,
                      project_type=args.project_type)
    _print_result(batch)


def cmd_run_phased(args: argparse.Namespace) -> None:
    """Phased auto: research -> MVP -> mvp_uploaded -> SOTA."""
    from toyshop.pm import run_batch_phased
    from toyshop.llm import create_llm

    user_input = _read_input(args.input)
    llm = create_llm()
    batch = run_batch_phased(
        Path(args.pm_root),
        args.name,
        user_input,
        llm,
        project_type=args.project_type,
        auto_continue_sota=not args.stop_after_mvp,
        enable_research_agent=not args.skip_research,
        research_timebox_minutes=args.research_timebox,
    )
    _print_result(batch)


def cmd_create(args: argparse.Namespace) -> None:
    """Step 1: Create batch folder with requirements.md."""
    from toyshop.pm import create_batch

    user_input = _read_input(args.input)
    batch = create_batch(Path(args.pm_root), args.name, user_input, project_type=args.project_type)
    print(f"Batch dir: {batch.batch_dir}")
    print("Next: python3 -m toyshop.pm_cli spec --batch <batch_dir>")


def cmd_spec(args: argparse.Namespace) -> None:
    """Step 2: Generate openspec docs (proposal, design, tasks, spec)."""
    from toyshop.pm import load_batch, run_spec_generation
    from toyshop.llm import create_llm

    batch = load_batch(args.batch)
    llm = create_llm()
    batch = run_spec_generation(batch, llm)
    _print_result(batch)
    if batch.status != "failed":
        print("\nGenerated docs:")
        for f in sorted((batch.batch_dir / "openspec").iterdir()):
            print(f"  {f.name}")
        print("\nReview openspec/ docs, then:")
        print("  python3 -m toyshop.pm_cli tasks --batch", args.batch)


def cmd_tasks(args: argparse.Namespace) -> None:
    """Step 3: Parse tasks.md → create task folders (display only)."""
    from toyshop.pm import load_batch, prepare_tasks

    batch = load_batch(args.batch)
    tasks = prepare_tasks(batch)
    print(f"Parsed {len(tasks)} tasks:")
    for t in tasks:
        print(f"  [{t.id}] {t.title}")
    print("\nNext: python3 -m toyshop.pm_cli tdd --batch", args.batch)


def cmd_tdd(args: argparse.Namespace) -> None:
    """Step 4: Run TDD pipeline for the batch (auto-detects create/modify mode)."""
    from toyshop.pm import load_batch, run_batch_tdd
    from toyshop.llm import create_llm

    batch = load_batch(args.batch)
    llm = create_llm()

    # Auto-detect mode from batch metadata
    meta_path = Path(args.batch) / "batch_meta.json"
    mode = "create"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("type") == "change":
            mode = "modify"
    print(f"TDD mode: {mode}")

    result = run_batch_tdd(batch, llm, mode=mode)
    print(f"\nTDD result: {'PASS' if result.success else 'FAIL'}")
    print(f"  Whitebox: {'pass' if result.whitebox_passed else 'fail'}")
    print(f"  Blackbox: {'pass' if result.blackbox_passed else 'fail'}")
    print(f"  Retries: {result.retry_count}")
    if result.files_created:
        print(f"  Files: {len(result.files_created)}")
    _print_result(batch)


def cmd_status(args: argparse.Namespace) -> None:
    """Show batch status."""
    batch_dir = Path(args.batch)
    progress_path = batch_dir / "progress.json"
    if not progress_path.exists():
        print(f"No progress.json in {batch_dir}")
        sys.exit(1)

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    print(f"Batch: {progress['batch_id']}")
    print(f"Project: {progress['project_name']}")
    print(f"Status: {progress['status']}")
    print(f"Tasks: {progress['completed_tasks']}/{progress['total_tasks']} completed, "
          f"{progress['failed_tasks']} failed")
    if progress.get("current_task"):
        print(f"Current: {progress['current_task']}")

    tasks_root = batch_dir / "tasks"
    if tasks_root.exists():
        for td in sorted(tasks_root.iterdir()):
            tj = td / "task.json"
            if tj.exists():
                t = json.loads(tj.read_text(encoding="utf-8"))
                print(f"  [{t['status']:>11}] {t['id']} {t['title']}")

    # Show result if available
    result_path = batch_dir / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if "error" in result:
            print(f"\nLast error: {result['error']}")
        elif "success" in result:
            print(f"\nTDD: {'PASS' if result['success'] else 'FAIL'}")

    stage_checkpoint_path = batch_dir / "stage_checkpoint.json"
    if stage_checkpoint_path.exists():
        ck = json.loads(stage_checkpoint_path.read_text(encoding="utf-8"))
        print("\nStage checkpoint:")
        print(f"  current_stage: {ck.get('current_stage')}")
        print(f"  gate_passed: {ck.get('stage_gate_passed')}")


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume an interrupted batch (re-run TDD)."""
    from toyshop.pm import resume_batch
    from toyshop.llm import create_llm

    llm = create_llm()
    batch = resume_batch(args.batch, llm)
    _print_result(batch)


def cmd_research_deadlock(args: argparse.Namespace) -> None:
    """Manual trigger for deadlock-resolution research planning."""
    from toyshop.pm import load_batch, run_research_planning
    from toyshop.llm import create_llm

    batch = load_batch(args.batch)
    llm = create_llm()
    problem_statement = _read_input(args.problem) if args.problem else None
    plan = run_research_planning(
        batch,
        llm,
        trigger_type="deadlock_resolution",
        timebox_minutes=args.research_timebox,
        enable_external_research=not args.skip_external_research,
        problem_statement=problem_statement,
        local_attempt_summary=args.summary,
    )

    print("Deadlock research plan generated.")
    print(f"  trigger_type: {plan.trigger_type}")
    print(f"  recommended_option: {plan.recommended_option}")
    print(f"  mvp_scope_count: {len(plan.mvp_scope)}")
    print(f"  sources_count: {len(plan.sources)}")
    print(f"Artifacts: {Path(args.batch) / 'research'}")


def cmd_change_create(args: argparse.Namespace) -> None:
    """Change step 1: Create change batch from existing workspace."""
    from toyshop.pm import create_change_batch

    change_request = _read_input(args.input)
    batch = create_change_batch(
        Path(args.pm_root), args.name, Path(args.workspace), change_request,
        project_type=args.project_type,
    )
    print(f"Batch dir: {batch.batch_dir}")
    print("Next: python3 -m toyshop.pm_cli change-analyze --batch", batch.batch_dir)


def cmd_change_analyze(args: argparse.Namespace) -> None:
    """Change step 2: Snapshot code + impact analysis."""
    from toyshop.pm import load_batch, run_change_analysis
    from toyshop.llm import create_llm

    batch = load_batch(args.batch)
    llm = create_llm()
    impact = run_change_analysis(batch, llm)

    print(f"\nSummary: {impact.change_summary}")
    if impact.affected_modules:
        print("Affected modules:")
        for m in impact.affected_modules:
            print(f"  [{m.change_type}] {m.module_name}: {m.reason}")
    if impact.affected_interfaces:
        print("Affected interfaces:")
        for i in impact.affected_interfaces:
            print(f"  [{i.change_type}] {i.interface_name}: {i.reason}")
    if impact.new_modules:
        print("New modules:")
        for n in impact.new_modules:
            print(f"  {n.name} ({n.file_path}): {n.description}")

    print("\nReview impact.json, then:")
    print("  python3 -m toyshop.pm_cli change-spec --batch", args.batch)


def cmd_change_spec(args: argparse.Namespace) -> None:
    """Change step 3: Evolve openspec docs based on impact."""
    from toyshop.pm import load_batch, run_spec_evolution
    from toyshop.impact import load_impact
    from toyshop.llm import create_llm

    batch = load_batch(args.batch)
    impact_path = Path(args.batch) / "impact.json"
    if not impact_path.exists():
        print("No impact.json found. Run change-analyze first.")
        sys.exit(1)

    impact = load_impact(impact_path)
    llm = create_llm()
    batch = run_spec_evolution(batch, impact, llm)
    _print_result(batch)

    print("\nReview updated openspec/ docs, then:")
    print("  python3 -m toyshop.pm_cli tdd --batch", args.batch)


def cmd_wiki_history(args: argparse.Namespace) -> None:
    """Show wiki version history for a batch."""
    from toyshop.storage.database import init_database, get_db
    from toyshop.storage.wiki import list_versions, get_test_suite

    workspace = Path(args.batch) / "workspace"
    db_path = workspace / ".toyshop" / "architecture.db"
    if not db_path.exists():
        print(f"No database at {db_path}")
        sys.exit(1)

    init_database(db_path)
    db = get_db()
    cur = db.execute("SELECT id, name FROM projects LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("No projects in database.")
        return

    project_id = row["id"]
    print(f"Project: {row['name']} ({project_id})\n")

    versions = list_versions(project_id, limit=int(args.limit))
    if not versions:
        print("No versions yet.")
        return

    for v in versions:
        git = v.git_commit_hash[:8] if v.git_commit_hash else "unbound"
        ts = get_test_suite(v.id)
        test_info = f"{ts.passed}/{ts.total_tests} passed" if ts else "no tests"
        print(f"  v{v.version_number:>3}  [{v.change_type:>6}]  {git}  "
              f"{test_info:>16}  {v.change_summary[:60]}")


def cmd_wiki_diff(args: argparse.Namespace) -> None:
    """Show diff between two wiki versions."""
    from toyshop.storage.database import init_database, get_db
    from toyshop.storage.wiki import diff_versions

    workspace = Path(args.batch) / "workspace"
    db_path = workspace / ".toyshop" / "architecture.db"
    if not db_path.exists():
        print(f"No database at {db_path}")
        sys.exit(1)

    init_database(db_path)
    db = get_db()
    cur = db.execute("SELECT id FROM projects LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("No projects in database.")
        return

    diff = diff_versions(row["id"], int(args.from_ver), int(args.to_ver))
    print(f"Diff v{diff.from_version} → v{diff.to_version}")
    print(f"Summary: {diff.change_summary}\n")

    if diff.modules_added:
        print(f"Modules added:    {', '.join(diff.modules_added)}")
    if diff.modules_removed:
        print(f"Modules removed:  {', '.join(diff.modules_removed)}")
    if diff.modules_modified:
        print(f"Modules modified: {', '.join(diff.modules_modified)}")
    if diff.interfaces_added:
        print(f"Interfaces added:    {', '.join(diff.interfaces_added)}")
    if diff.interfaces_removed:
        print(f"Interfaces removed:  {', '.join(diff.interfaces_removed)}")
    if diff.interfaces_modified:
        print(f"Interfaces modified: {', '.join(diff.interfaces_modified)}")
    if diff.tests_added:
        print(f"Tests added:   {len(diff.tests_added)}")
    if diff.tests_removed:
        print(f"Tests removed: {len(diff.tests_removed)}")


def cmd_wiki_commit(args: argparse.Namespace) -> None:
    """Bind a git commit to the latest wiki version."""
    import subprocess
    from toyshop.storage.database import init_database, get_db
    from toyshop.storage.wiki import get_latest_version, bind_git_commit

    workspace = Path(args.batch) / "workspace"
    db_path = workspace / ".toyshop" / "architecture.db"
    if not db_path.exists():
        print(f"No database at {db_path}")
        sys.exit(1)

    init_database(db_path)
    db = get_db()
    cur = db.execute("SELECT id FROM projects LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("No projects in database.")
        return

    version = get_latest_version(row["id"])
    if not version:
        print("No wiki versions to bind.")
        return

    if version.git_commit_hash:
        print(f"v{version.version_number} already bound to {version.git_commit_hash[:8]}")
        return

    # Git commit in workspace
    msg = args.message or f"wiki v{version.version_number}: {version.change_summary[:60]}"
    try:
        subprocess.run(
            ["git", "add", "-A"], cwd=workspace, check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", msg], cwd=workspace, check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=workspace, check=True,
            capture_output=True, text=True,
        )
        commit_hash = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e.stderr.decode() if e.stderr else e}")
        sys.exit(1)

    bind_git_commit(version.id, commit_hash)
    print(f"Bound v{version.version_number} → {commit_hash[:8]}")


# --- Helpers ---

def _read_input(input_arg: str) -> str:
    try:
        input_path = Path(input_arg)
        if input_path.is_file():
            return input_path.read_text(encoding="utf-8")
    except OSError:
        pass
    return input_arg


def _print_result(batch) -> None:
    print(f"\nBatch dir: {batch.batch_dir}")
    print(f"Status: {batch.status}")
    if batch.error:
        print(f"Error: {batch.error}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="toyshop-pm", description="ToyShop PM System")
    sub = parser.add_subparsers(dest="command", required=True)

    # run (full auto)
    p_run = sub.add_parser("run", help="Full auto: create → spec → tasks → tdd")
    p_run.add_argument("--name", required=True)
    p_run.add_argument("--input", required=True, help="Requirements text or path to .md file")
    p_run.add_argument("--pm-root", default=str(Path.home() / ".toyshop" / "projects"))
    p_run.add_argument("--type", dest="project_type", default="python",
                        help="Project type: python, java, java-minecraft, json-minecraft")

    # run-phased (research + staged MVP/SOTA)
    p_run_phased = sub.add_parser(
        "run-phased",
        help="Phased auto: research → MVP → mvp_uploaded → SOTA",
    )
    p_run_phased.add_argument("--name", required=True)
    p_run_phased.add_argument("--input", required=True, help="Requirements text or path to .md file")
    p_run_phased.add_argument("--pm-root", default=str(Path.home() / ".toyshop" / "projects"))
    p_run_phased.add_argument(
        "--type",
        dest="project_type",
        default="python",
        help="Project type: python, java, java-minecraft, json-minecraft",
    )
    p_run_phased.add_argument(
        "--stop-after-mvp",
        action="store_true",
        help="Stop after MVP checkpoint (do not auto-continue to SOTA)",
    )
    p_run_phased.add_argument(
        "--skip-research",
        action="store_true",
        help="Skip research agent and use deterministic fallback MVP/SOTA plan",
    )
    p_run_phased.add_argument(
        "--research-timebox",
        type=int,
        default=8,
        help="Research timebox in minutes for kickoff planning",
    )

    # create (step 1)
    p_create = sub.add_parser("create", help="Step 1: Create batch with requirements")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--input", required=True)
    p_create.add_argument("--pm-root", default=str(Path.home() / ".toyshop" / "projects"))
    p_create.add_argument("--type", dest="project_type", default="python",
                          help="Project type: python, java, java-minecraft, json-minecraft")

    # spec (step 2)
    p_spec = sub.add_parser("spec", help="Step 2: Generate openspec docs")
    p_spec.add_argument("--batch", required=True)

    # tasks (step 3)
    p_tasks = sub.add_parser("tasks", help="Step 3: Parse tasks → create folders")
    p_tasks.add_argument("--batch", required=True)

    # tdd (step 4)
    p_tdd = sub.add_parser("tdd", help="Step 4: Run TDD pipeline")
    p_tdd.add_argument("--batch", required=True)

    # status
    p_status = sub.add_parser("status", help="Show batch status")
    p_status.add_argument("--batch", required=True)

    # resume
    p_resume = sub.add_parser("resume", help="Resume interrupted batch")
    p_resume.add_argument("--batch", required=True)

    # research-deadlock
    p_rd = sub.add_parser(
        "research-deadlock",
        help="Manual trigger: deadlock resolution research for an existing batch",
    )
    p_rd.add_argument("--batch", required=True)
    p_rd.add_argument(
        "--summary",
        default="",
        help="Local attempt summary (why we are stuck)",
    )
    p_rd.add_argument(
        "--problem",
        default=None,
        help="Problem statement text or path; default uses requirements.md",
    )
    p_rd.add_argument(
        "--research-timebox",
        type=int,
        default=8,
        help="Research timebox in minutes",
    )
    p_rd.add_argument(
        "--skip-external-research",
        action="store_true",
        help="Disable external GPT Researcher and keep deterministic local planning",
    )

    # change-create
    p_cc = sub.add_parser("change-create", help="Change step 1: Create change batch")
    p_cc.add_argument("--name", required=True)
    p_cc.add_argument("--workspace", required=True, help="Path to existing workspace")
    p_cc.add_argument("--input", required=True, help="Change request text or path to .md file")
    p_cc.add_argument("--pm-root", default=str(Path.home() / ".toyshop" / "projects"))
    p_cc.add_argument("--type", dest="project_type", default="python",
                      help="Project type: python, java, java-minecraft, json-minecraft")

    # change-analyze
    p_ca = sub.add_parser("change-analyze", help="Change step 2: Snapshot + impact analysis")
    p_ca.add_argument("--batch", required=True)

    # change-spec
    p_cs = sub.add_parser("change-spec", help="Change step 3: Evolve openspec docs")
    p_cs.add_argument("--batch", required=True)

    # wiki-history
    p_wh = sub.add_parser("wiki-history", help="Show wiki version history")
    p_wh.add_argument("--batch", required=True)
    p_wh.add_argument("--limit", default="20")

    # wiki-diff
    p_wd = sub.add_parser("wiki-diff", help="Diff two wiki versions")
    p_wd.add_argument("--batch", required=True)
    p_wd.add_argument("--from", dest="from_ver", required=True)
    p_wd.add_argument("--to", dest="to_ver", required=True)

    # wiki-commit
    p_wc = sub.add_parser("wiki-commit", help="Git commit + bind to latest wiki version")
    p_wc.add_argument("--batch", required=True)
    p_wc.add_argument("--message", "-m", default=None)

    args = parser.parse_args()
    cmd = {
        "run": cmd_run, "run-phased": cmd_run_phased,
        "create": cmd_create, "spec": cmd_spec,
        "tasks": cmd_tasks, "tdd": cmd_tdd, "status": cmd_status,
        "resume": cmd_resume,
        "research-deadlock": cmd_research_deadlock,
        "change-create": cmd_change_create,
        "change-analyze": cmd_change_analyze,
        "change-spec": cmd_change_spec,
        "wiki-history": cmd_wiki_history,
        "wiki-diff": cmd_wiki_diff,
        "wiki-commit": cmd_wiki_commit,
    }
    cmd[args.command](args)


if __name__ == "__main__":
    main()

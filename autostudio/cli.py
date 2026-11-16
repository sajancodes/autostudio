from __future__ import annotations

import argparse
import sys

from autostudio.config import Settings, load_settings

NOT_BUILT = "'{}' is not implemented yet (building step by step)."


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autostudio", description="Make one short video, start to finish.")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the full pipeline")
    run.add_argument("--niche", required=True)
    run.add_argument("--mock", action="store_true")
    run.add_argument("--scenes", type=int, default=4)
    run.add_argument("--aspect", default="9:16")
    run.add_argument("--no-upload", action="store_true")
    run.add_argument("--until", choices=["idea", "script", "clips"], default="clips",
                     help="stop after this step (default: clips, the last one built so far)")
    run.add_argument("--max-cost", type=float, default=None, help="USD cap for this run (default: MAX_COST_USD)")

    step = sub.add_parser("step", help="rerun a single step")
    step.add_argument("name", choices=["idea", "script", "clips", "edit", "upload"])
    step.add_argument("--max-cost", type=float, default=None, help="override the run's USD cap (clips step)")
    step.add_argument("--run", required=True, dest="run_id")

    auth = sub.add_parser("auth", help="one-time OAuth login")
    auth.add_argument("service", choices=["youtube"])

    sub.add_parser("doctor", help="check ffmpeg, keys, OAuth, disk")
    return p


def _cmd_run(args, settings: Settings) -> int:
    from autostudio.clips import get_video_provider, run_clips_step
    from autostudio.costs import CostCapExceeded, CostGuard, ensure_within_cap, estimate_clips
    from autostudio.ideas import run_idea_step
    from autostudio.manifest import build_manifest
    from autostudio.providers.text import get_text_provider
    from autostudio.runs import Run, new_run_id
    from autostudio.script import run_script_step

    cap = args.max_cost if args.max_cost is not None else settings.max_cost_usd
    try:
        manifest = build_manifest(settings, new_run_id(), args.niche, args.scenes, args.aspect,
                                  args.mock, cap, upload=not args.no_upload)
        # Everything that can fail cheaply happens before a run folder exists.
        est = estimate_clips(settings, args.scenes, args.mock)
        ensure_within_cap(0.0, est.total_usd, cap, "planned clips")
        text_provider = get_text_provider(settings, args.mock)
        video_provider = get_video_provider(settings, args.mock) if args.until == "clips" else None
    except (ValueError, CostCapExceeded, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3 if isinstance(e, CostCapExceeded) else 2

    run = Run.create(settings.runs_dir, manifest.run_id)
    manifest.save(run.dir)
    print(f"Run {run.id}{' (mock)' if args.mock else ''}")
    print(f"Plan: {manifest.scenes} scenes x {manifest.clip_duration_s}s, {manifest.aspect} "
          f"({manifest.width}x{manifest.height}), est. clip cost ${est.total_usd:.2f} (cap ${cap:.2f})")
    current = "idea"
    try:
        idea = run_idea_step(run, manifest, text_provider, settings.max_retries)
        print(f"Idea: {idea.title}\nHook: {idea.hook}")
        if args.until in ("script", "clips"):
            current = "script"
            _print_script(run_script_step(run, manifest, text_provider, settings.max_retries))
        if args.until == "clips":
            current = "clips"
            guard = CostGuard(run, cap)
            clips = run_clips_step(run, manifest, video_provider, guard, max_retries=settings.max_retries,
                                   workers=settings.max_concurrent_generations)
            print(f"Clips: {len(clips)} generated, spent ${guard.spent_usd:.2f} of ${cap:.2f} cap")
    except Exception as e:
        print(f"Step '{current}' failed: {e}", file=sys.stderr)
        print(f"Run saved at {run.dir}; fix the problem and rerun: "
              f"python -m autostudio step {current} --run {run.id}", file=sys.stderr)
        return 1
    print(f"Saved to {run.dir}. " + ("Next steps (edit, upload) are not built yet." if args.until == "clips"
                                     else "Later steps were skipped by --until."))
    return 0


def _print_script(script) -> None:
    print(f"Script: {len(script.scenes)} scenes")
    for sc in script.scenes:
        print(f"  {sc.no}. {sc.narration}")


def _cmd_step(args, settings: Settings) -> int:
    if args.name not in ("idea", "script", "clips"):
        print(NOT_BUILT.format(f"step {args.name}"), file=sys.stderr)
        return 2
    from autostudio.clips import get_video_provider, run_clips_step
    from autostudio.costs import CostGuard
    from autostudio.ideas import run_idea_step
    from autostudio.manifest import Manifest
    from autostudio.providers.text import get_text_provider
    from autostudio.runs import Run
    from autostudio.script import run_script_step

    try:
        run = Run.open(settings.runs_dir, args.run_id)
        manifest = Manifest.load(run.dir)
        if args.name == "clips":
            video_provider = get_video_provider(settings, manifest.mock)
        else:
            text_provider = get_text_provider(settings, manifest.mock)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    try:
        if args.name == "idea":
            idea = run_idea_step(run, manifest, text_provider, settings.max_retries)
            print(f"Idea: {idea.title}\nHook: {idea.hook}")
            print("The script and clips were cleared because the idea changed; rerun: "
                  f"python -m autostudio step script --run {run.id}")
        elif args.name == "script":
            _print_script(run_script_step(run, manifest, text_provider, settings.max_retries))
            print("Any clips from an older script were cleared; rerun: "
                  f"python -m autostudio step clips --run {run.id}")
        else:
            cap = args.max_cost if args.max_cost is not None else manifest.max_cost_usd
            guard = CostGuard(run, cap)
            clips = run_clips_step(run, manifest, video_provider, guard, max_retries=settings.max_retries,
                                   workers=settings.max_concurrent_generations)
            print(f"Clips: {len(clips)} ready, spent ${guard.spent_usd:.2f} of ${cap:.2f} cap")
    except Exception as e:
        print(f"Step failed: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings or load_settings()
    if args.cmd == "doctor":
        from autostudio.doctor import doctor
        return doctor(settings)
    if args.cmd == "run":
        return _cmd_run(args, settings)
    if args.cmd == "step":
        return _cmd_step(args, settings)
    print(NOT_BUILT.format(args.cmd), file=sys.stderr)
    return 2

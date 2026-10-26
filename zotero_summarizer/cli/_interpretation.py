"""Inspect by default; explicit execution/capture and separately supplied human labels."""
import json
import os
from uuid import UUID

from zotero_summarizer.settings import Settings


def register(subparsers):
    parser = subparsers.add_parser('interpretation', help='Frozen A/B/C interpretation experiment; inspect without model calls.')
    parser.add_argument('--recipe', default=None, help='Frozen recipe JSON; default ZS_INTERPRETATION_RECIPE.')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--capture-local', action='store_true', help='Consent to private source/prompt/raw completion retention.')
    parser.add_argument('--run-id', default=None, help='Export blind packets or score an existing immutable run.')
    parser.add_argument('--labels', default=None, help='Independent, response-bound human judgments.')
    parser.add_argument('--project-root', default=None)
    parser.set_defaults(func=command)


def validate(args):
    if args.run_id and (args.recipe or args.run or args.capture_local):
        raise ValueError('Existing-run scoring/export cannot request generation')
    if args.labels and not args.run_id:
        raise ValueError('Human labels require an existing run ID')
    if args.run and not args.capture_local:
        raise ValueError('--run requires explicit --capture-local consent')


def command(args):
    from zotero_summarizer.services.faithbench._interpretation import execute
    from zotero_summarizer.services.faithbench._interpretation_inputs import inspect, model_profiles
    from zotero_summarizer.services.faithbench._interpretation_report import report

    validate(args)
    settings = Settings.load(project_root=args.project_root)
    if args.run_id:
        if UUID(args.run_id).hex != args.run_id:
            raise ValueError('Invalid interpretation run ID')
        result = report(settings, settings.faithbench_dir / 'interpretation' / args.run_id, args.labels)
    else:
        recipe = args.recipe or os.environ.get('ZS_INTERPRETATION_RECIPE')
        if not recipe:
            raise ValueError('A frozen recipe is required')
        snapshot = inspect(settings, recipe)
        result = {'status': 'INSPECTED_NOT_EXECUTED', 'evaluation_papers': len(snapshot['evaluation']),
                  'assigned_per_condition': len(snapshot['items']), 'model_constructed': False}
        if args.run:
            from zotero_summarizer.models import AppState
            from zotero_summarizer.runtime import RuntimeState
            from zotero_summarizer.services._common import read_config
            from zotero_summarizer.services.llm import factory

            config = read_config(settings.config_path)
            runtime = RuntimeState(app_state=AppState(config=config))
            def build(role, profile):
                return factory.resolve_stage_client(runtime, profile.stage, enable_thinking=profile.provider.thinking_on if role == 'reduce' else None)
            root = execute(settings, snapshot, config, model_profiles(config), build)
            result = {'status': 'ASSIGNED_OUTCOMES_RECORDED_NOT_SCIENTIFIC_ACCEPTANCE', 'run_id': root.name}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

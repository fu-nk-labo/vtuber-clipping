import argparse
import sys
from importlib.resources import files
from .flow_html import render_flow
from .render import render, synthetic_source
from .engine import (ValidationError, read_json, parse_time, load_captions,
    make_proposal, validate_proposal, validate_plan, apply_review, export_plan,
    reconcile, write_new, json_text)
from .review_html import render_review


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local clipping beta: validated edit plans, loopback AI review app and FFmpeg preview.')
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('serve', help='Start standalone local AI/review/render app on loopback')
    s.add_argument('--workspace', default='clipbeta-work')
    s.add_argument('--port', type=int, default=0)
    s.add_argument('--no-browser', action='store_true')
    s = sub.add_parser('suggest', help='Create rule-based proposal from SRT/JSON')
    s.add_argument('input'); s.add_argument('--start', required=True); s.add_argument('--end', required=True)
    s.add_argument('--duration', required=True, help='Declared source media duration (seconds or HH:MM:SS.mmm)')
    s.add_argument('--out', required=True)
    s = sub.add_parser('html', help='Create offline editable review page')
    s.add_argument('proposal'); s.add_argument('--out', required=True)
    s = sub.add_parser('review', help='Merge explicit review decisions into proposal')
    s.add_argument('proposal'); s.add_argument('decisions'); s.add_argument('--out', required=True)
    s = sub.add_parser('export', help='Export fully reviewed edit plan')
    s.add_argument('proposal'); s.add_argument('--out', required=True)
    s = sub.add_parser('intake-demo', help='Generate offline MOCK Twitch URL intake; no network')
    s.add_argument('--out', required=True)
    s = sub.add_parser('synthetic-media', help='Generate a local synthetic 15-second video')
    s.add_argument('--out', required=True)
    s = sub.add_parser('render', help='Render validated plan onto local authorized media with FFmpeg')
    s.add_argument('plan'); s.add_argument('media'); s.add_argument('--out-dir', required=True)
    s = sub.add_parser('validate'); s.add_argument('input')
    s = sub.add_parser('reconcile', help='Preview adapter ID changes only; does not apply edits')
    s.add_argument('plan'); s.add_argument('--existing'); s.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'serve':
            from .standalone import serve
            serve(args.workspace, args.port, not args.no_browser); return 0
        elif args.command == 'synthetic-media':
            print(f'Created synthetic source: {synthetic_source(args.out)}'); return 0
        elif args.command == 'render':
            print(f'Rendered local preview: {render(read_json(args.plan), args.media, args.out_dir)}'); return 0
        elif args.command == 'intake-demo':
            captions = load_captions(files('clipbeta').joinpath('data/synthetic.srt'))
            write_new(args.out, render_flow(captions))
            print(f'Created MOCK intake demo: {args.out}')
            return 0
        elif args.command == 'suggest':
            output = make_proposal(load_captions(args.input), parse_time(args.start), parse_time(args.end), parse_time(args.duration))
        elif args.command == 'html':
            write_new(args.out, render_review(read_json(args.proposal)))
            print(f'Created offline review: {args.out}')
            return 0
        elif args.command == 'review':
            output = apply_review(read_json(args.proposal), read_json(args.decisions))
        elif args.command == 'export':
            output = export_plan(read_json(args.proposal))
        elif args.command == 'validate':
            obj = read_json(args.input)
            if isinstance(obj, dict) and obj.get('kind') == 'edit-plan': validate_plan(obj)
            else: validate_proposal(obj)
            print('Valid ' + obj['kind']); return 0
        else:
            output = reconcile(read_json(args.plan), read_json(args.existing) if args.existing else None)
        write_new(args.out, json_text(output))
        print(f'Created: {args.out}')
        return 0
    except (ValidationError, OSError, UnicodeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

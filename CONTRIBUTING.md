# Contributing

This project uses the MIT License. Contributions should be original work or clearly identify compatible third-party material and its required notices.

## Development

1. Use Python 3.10+ and a virtual environment.
2. Install the project locally; use the `render` extra only when renderer tests are needed.
3. Run the Python and Node commands in README before proposing changes.
4. Add tests for validation errors, Unicode boundaries, repeated actions, cancellation, stale results, no-op effects, and output overwrite protection.

Use synthetic fixtures only. Do not commit API keys, tokens, browser cookies, personal paths, real stream transcripts, private media, generated videos, or machine-specific manifests.

## Design boundaries

- Keep edit-plan generation separate from model providers and editor adapters.
- Do not quietly introduce network requests, downloads, telemetry, charges, or credential storage.
- Treat subtitle text as data, never executable HTML or FFmpeg expressions.
- Preserve old supported plans or document and test an explicit schema migration.
- Keep a review step before final export; do not automatically adopt model or rule suggestions.
- Tests must not depend on a live Twitch account or private content.

## Reporting problems

Include a minimal synthetic input, expected behavior, command, OS, Python/FFmpeg versions, and redacted error details. Do not post secrets or private video. Security-report contact and repository links will be added after the owner chooses the publication destination.

## Maintainer release gate

Confirm the release scope, version, MIT license/copyright attribution, and third-party notices. Run the staged-tree scan, packaging smoke test, and complete test suite. Verify CI on the exact published commit before calling a release verified.

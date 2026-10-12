# Twitch URL acquisition: implementation and verification status

Updated 2026-10-12. URL acquisition is an **optional experimental adapter**, not a claim of proven real-Twitch end-to-end success.

## Current route

`clipbeta/twitch_download.py` implements public Twitch channel/VOD metadata lookup and completed VOD acquisition using the pinned optional dependency `yt-dlp==2026.8.19`. Users install the dependency explicitly; the app does not install it automatically. Ordinary public playback through this reputable downloader is within scope. An undocumented playback API alone is not treated as a prohibition or proof of impossibility.

The adapter uses yt-dlp's Twitch extractors to resolve metadata and ordinary public playback, but replaces its HTTP entrypoint with the guarded transport described below. It downloads unencrypted MPEG-TS HLS segments itself and remuxes the local TS file to MP4 with FFmpeg. It never calls yt-dlp's media downloader, external network downloader, or browser-cookie extraction.

- `list_archives(raw_url, progress=None, cancelled=None)` returns `target` and up to 20 archive rows. Direct VOD lookup uses an upstream metadata-only hook and does not request playback tokens. Rows contain ID, canonical viewing URL, title, duration, and `access_verified: false`.
- `acquire(raw_vod_url, out_dir, progress=None, cancelled=None)` creates a new directory and returns `path` plus sanitized `metadata`. Existing directories are rejected. Failure/cancellation removes only the newly created directory.
- Optional developer smoke bounds `max_media_seconds` (at most 60 seconds) and `max_bytes` select complete initial HLS segments within the limit. Metadata distinguishes `original_duration_ms`, ffprobe-verified `duration_ms` / `downloaded_duration_ms`, playlist `manifest_duration_ms`, `source_start_ms` / `source_offset_ms` (zero), and `partial`.

Downloaded media continues through the existing local ASR → local LLM → human review → MP4 workflow. Metadata listing alone is never media acquisition. UI integration and its tests are separate from this adapter note.

## Network and resource boundaries

Every metadata request, playlist, segment and redirect passes the same HTTPS-only transport. It allows Twitch and CDN host families (`twitch.tv`, `ttvnw.net`, `twitchcdn.net`, `jtvnw.net`, `cloudfront.net`), rejects credentials in URLs and authentication/cookie headers, resolves each destination, rejects any non-public address, and pins the connection to a validated address while validating TLS for the original hostname. CloudFront is shared hosting; this allowlist is not a claim that every CloudFront host belongs to Twitch. CDN destinations come from the selected Twitch extractor and all still undergo public-address validation.

Redirects repeat validation and DNS checks; API POST bodies are not forwarded across hosts. Environment proxies are not used. yt-dlp plugins, optional JavaScript runtimes, cache and account credential sources are disabled. A process that has already loaded yt-dlp plugins is rejected. Only the two needed Twitch extractors are registered.

Default acquisition budget is 2 GiB of network response bodies and 600 seconds; reliable supplied size/bitrate estimates can fail early. Metadata has a smaller 32 MiB/60-second budget. Individual responses, segment count, network timeouts, free disk and cancellation are bounded. Remuxing uses forced MPEG-TS input with FFmpeg's protocol allowlist restricted to `file,pipe`; no remote playlist reaches FFmpeg. A deadline/cancellation watchdog actively shuts down the captured socket during blocked header/body/TLS reads. DNS and connection establishment are separately timeout-bounded. The budget is rechecked after reads/EOF and after remuxing.

Only complete, unencrypted TS HLS is supported. Playlist lines are normalized before exact directive checks. Final local ffprobe requires finite positive duration and both audio/video streams; short smoke files may exceed the requested media duration by at most 0.5 seconds for packet/container rounding. Live/incomplete manifests, encrypted media, fMP4 initialization maps, byte-range playlists, unsupported payloads and excessive segment counts fail closed. This may reject otherwise playable videos rather than weakening guards. The 2 GiB/600-second defaults may exclude long/high-bitrate archives. There is no automatic smaller-quality retry after an access denial.

No subscriber/private/deleted-video bypass, cookie or account-token extraction, client-integrity workaround, silent authentication, republishing, or external AI processing is included. HTTP 401/403 immediately stops acquisition. Playback metadata cannot prove reuse rights; the user must be authorized to process their chosen material.

## What was actually verified

- 22 focused tests passed with the optional pinned yt-dlp installed from PyPI. They cover host restrictions, private/mixed DNS answers, redirect revalidation, pinned TLS, credential refusal, denial without retry, resource limits, cancellation, no-overwrite, safe cleanup, HLS restrictions, metadata minimization and dependency/API construction, active watchdog cancellation, slow-EOF deadline checks, exact playlist completion/encryption directives and actual-duration validation.
- A synthetic MPEG-TS fixture, mocked network responses and **real FFmpeg** exercised the acquisition-to-MP4 path and partial-duration provenance. This is not a Twitch media test.
- An authorized real public VOD's metadata lookup was attempted. Both default and escalated execution failed at DNS resolution for `gql.twitch.tv`, before a playback/media request. No real Twitch video was downloaded, transcribed or rendered in this environment. This is an environment network blocker, not evidence the VOD is unavailable or Twitch denied access.
- A browser's ability to display a public VOD does not verify this adapter's transport or download. Real source → downloaded bytes → ASR remains unverified until the same guarded adapter is run in an environment with working direct DNS/HTTPS access.

## Official API distinction and fallback

Twitch's Get Videos interface returns metadata and viewing URLs, not a full-VOD file URL. It takes an app/user token; channels can be resolved with Get Users and videos listed using `user_id` with `type=archive`. This is distinct from the downloader's public website playback implementation. [Official video guide](https://dev.twitch.tv/docs/api/videos)

Get Clips Download supplies temporary clip-media links with broadcaster/editor authorization. Create Clip From VOD creates a short 5–60-second Twitch clip for an already chosen interval and changes Twitch state. Neither supplies the full archive needed for whole-VOD candidate discovery. [Clips download](https://dev.twitch.tv/docs/api/reference/#get-clips-download), [Create Clip From VOD](https://dev.twitch.tv/docs/api/reference/#create-clip-from-vod)

A broadcaster can use Creator Dashboard → Content → Video Producer → a video's menu → Download, then import the file locally. Downloading does not restore muted audio. Export transfers to a connected YouTube account and does not provide a local file to this app; no account linking/export is silently attempted. [Official VOD help](https://help.twitch.tv/s/article/video-on-demand)

## Upstream implementation references

Current yt-dlp Twitch source obtains ordinary public playback tokens through Twitch's GraphQL service and resolves HLS. Its metadata-only methods and `YoutubeDL.urlopen` are upstream implementation hooks rather than a stable application contract, so this project pins the dependency and requires tests before upgrades. [Twitch extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/twitch.py), [YoutubeDL](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py), [Response adapter](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/networking/common.py)

Streamlink is another popular tool, but its documented client-integrity and website authentication paths are unnecessary here and have not been enabled. [Streamlink documentation](https://streamlink.github.io/cli/plugins/twitch.html)

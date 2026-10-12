# Twitch integration boundaries

Checked against official documentation on 2026-10-09.

The current URL interface is entirely offline. It validates a limited Twitch URL syntax and displays synthetic archive/candidate data. It neither confirms a VOD exists nor analyzes its contents.

For a real metadata integration, resolve channel login with Get Users, then call Get Videos with `user_id` and `type=archive`. A direct VOD uses `id`. These endpoints accept app/user access tokens and require matching Client-Id headers. [Get Users](https://dev.twitch.tv/docs/api/reference/#get-users), [Get Videos](https://dev.twitch.tv/docs/api/reference/#get-videos)

Get Videos documents metadata and a viewing URL, not a full-VOD file-download URL. Listing metadata therefore does not itself supply analysis bytes. Clips Download serves clips and is not a general full-archive downloader. [API reference](https://dev.twitch.tv/docs/api/reference/#get-clips-download)

A broadcaster can download their own available video through Video Producer's video menu. Muted audio remains muted. Export is a separate Twitch-to-YouTube upload after account linking. [Official VOD help](https://help.twitch.tv/s/article/video-on-demand)

An owner can instead provide an authorized local recording, such as an OBS recording. [Official local recording help](https://help.twitch.tv/s/article/local-recording?language=en_US)

The next safe media-input path is owner-provided local media plus matching subtitles. A public viewing URL is not permission to copy or republish content. No credentials, undocumented media endpoints, access bypass, token scraping, external processing, or account linking are implemented.

# 0.5 verification record

Verified on a Linux CPU environment with Python3.12.14, FFmpeg7.1.5, Pillow12.3.0. Windows execution, CUDA inference and Ollama inference have not been verified here. Tests use synthetic fixtures unless explicitly described otherwise.

## Automated checks

- 168 Python tests passed, including22 guarded downloader tests with the optional pinned extractor installed
- 14 existing URL/state tests and22 standalone UI lifecycle tests passed, plus three existing review/intake DOM-double suites
- Compile checks, whitespace checks and the proposed public-tree heuristic scan passed
- A wheel was built without network dependency resolution, installed into a clean virtual environment, and served its packaged local UI successfully outside the source checkout
- Independent review found and verified fixes for stalled network cancellation, strict HLS completion/encryption parsing and actual media-duration checks

## Real local AI → video

An18.74-second synthetic English spoken video was processed with:

- faster-whisper1.2.1, CTranslate2 4.8.2, PyAV16.0.1, local Systran tiny weights
- llama.cpp b11564 CPU runtime and official Qwen3-0.6B Q8_0 weights
- Explicit local paths, CPU-only inference, bounded context/threads/timeouts
- Standard non-VAD Whisper path; an import trap and module assertion confirmed ONNX Runtime was not loaded

The pipeline produced10 word-timed caption cues and one structurally validated candidate spanning0–9.2seconds. Explicit test adoption/export produced a9.2-second H.264640×36030fps MP4 with AAC48kHz audio. Analysis took about23.4seconds and rendering1.2seconds on this cloud test machine. These timings are not predictions for other hardware or long Japanese recordings.

The same real models were then exercised through the production local HTTP app routes: local file import201, analyze202, completed polling, reviewHTML200, render202, authenticated MP4download200 and byte-range206. Unauthenticated download was rejected. Analysis took22.5seconds and rendering1.1seconds. The downloaded video matched the prior output byte-for-byte. Both inference and application servers stopped cleanly.

**Editorial quality is not established.** The tiny model selected an endpoint in the middle of a sentence and its explanation referred to later context. Exact source quotation and valid timing prevent fabricated evidence but do not prove a good cut or logical explanation. Human playback/review and manual range correction are essential. Larger local models and representative Japanese evaluation remain future validation work.

## Dependency/privacy issue found and fixed

The initial optional VAD path loaded ONNX Runtime1.31, whose initialization attempted external telemetry. That run was stopped. The app now uses supported `WhisperModel.transcribe(vad_filter=False)` without importing ONNX; real tests verify this component remains unloaded. No private user media was used in that failing test. PyAV19 also caused a decoder API incompatibility, so the optional dependency is pinned to verified16.0.1.

## UI verification boundary

HTTP integration and dependency-free DOM-double tests cover repeated clicks, cancellation, reload/reconnect, safe text, iframe message origin/source checks, range changes and stale results. Real Chromium visual QA could not start: the environment rejected Chromium's process-singleton socket creation with `Operation not permitted`. No visual-browser pass or Windows UI pass is claimed.

The source includes additional Linux and Windows CI jobs, but this prepared change has not been pushed, and its remote CI has not run. Model weights, test environments, downloaded media and local reports are excluded from the source package.

## Twitch acquisition

The optional public downloader is implemented and tested against controlled network fixtures plus real local remuxing. A user-authorized public VOD was separately confirmed playable in a browser, but the guarded acquisition runtime could not resolve gql.twitch.tv, including in its approved escalated execution. No Twitch media bytes were acquired and no real Twitch ASR/render pass is claimed. Access-restricted VODs were not downloaded. No proxy or weakened transport workaround was used.

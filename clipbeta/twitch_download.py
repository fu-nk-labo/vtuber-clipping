"""Opt-in public Twitch acquisition. All networking goes through pinned HTTPS.

No cookies, login, generic extractors, external downloader, or remote FFmpeg input.
Only completed, unencrypted MPEG-TS HLS is supported. Fail closed otherwise.
"""
from __future__ import annotations
import http.client
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import urljoin, urlsplit
from .engine import ValidationError
from .twitch_intake import parse_twitch_url

YTDLP_VERSION = '2026.08.19'
MAX_BYTES = 2 * 1024**3
MAX_SECONDS = 600
MAX_ARCHIVES = 20
# CloudFront is a shared CDN: allowed only as a media destination obtained from
# the Twitch extractor. Every resolved address is still required to be public.
HOSTS = ('twitch.tv', 'ttvnw.net', 'twitchcdn.net', 'jtvnw.net', 'cloudfront.net')

class TwitchDownloadError(ValidationError):
    pass

class Budget:
    def __init__(self, cancelled=None, max_bytes=MAX_BYTES, max_seconds=MAX_SECONDS):
        self.cancelled = cancelled or (lambda: False)
        self.deadline = time.monotonic() + max_seconds
        self.max_bytes = max_bytes
        self.received = 0

    def check(self):
        if self.cancelled():
            raise TwitchDownloadError('Twitch取得をキャンセルしました')
        if time.monotonic() >= self.deadline:
            raise TwitchDownloadError('Twitch取得の制限時間を超えました')

    def consume(self, count):
        self.check()
        self.received += count
        if self.received > self.max_bytes:
            raise TwitchDownloadError('Twitch取得の容量上限を超えました')

    def timeout(self):
        self.check()
        return max(0.1, min(10, self.deadline - time.monotonic()))


def validate_media_url(url):
    if not isinstance(url, str) or len(url) > 16384 or re.search(r'[\x00-\x20\x7f\\]', url):
        raise TwitchDownloadError('不正な取得URLです')
    try:
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        valid = (p.scheme == 'https' and p.port in (None, 443) and not p.username
                 and not p.password and not p.fragment and host.isascii()
                 and any(host == h or host.endswith('.' + h) for h in HOSTS))
    except ValueError:
        valid = False
    if not valid:
        raise TwitchDownloadError('許可されていない取得先です')
    return p


def public_addresses(host, budget):
    # Daemon resolver bounds caller wait even when the platform resolver stalls.
    result = []
    done = threading.Event()
    def resolve():
        try:
            result.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception:
            result.append(None)
        finally:
            done.set()
    threading.Thread(target=resolve, daemon=True).start()
    end = time.monotonic() + budget.timeout()
    while not done.wait(.05):
        budget.check()
        if time.monotonic() > end:
            raise TwitchDownloadError('DNS名前解決がタイムアウトしました')
    if not result or not result[0]:
        raise TwitchDownloadError('取得先を名前解決できません')
    addresses = [entry[4][0] for entry in result[0]]
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or ip.is_unspecified:
            raise TwitchDownloadError('非公開ネットワークへの接続を拒否しました')
    return addresses

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.active_socket = None

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        self.active_socket = raw
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.active_socket = self.sock
            self.sock.do_handshake()
        except BaseException:
            raw.close()
            raise

class Watchdog:
    """Interrupt blocking header/body/TLS reads at the operation deadline."""
    def __init__(self, connection, budget):
        self.connection, self.budget = connection, budget
        self.finished = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.finished.wait(.05):
            try:
                self.budget.check()
            except TwitchDownloadError as exc:
                self.failure = exc
                active = getattr(self.connection, 'active_socket', None)
                if active is not None:
                    try: active.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    try: active.close()
                    except OSError: pass
                return

    def start(self):
        self.thread.start()

    def stop(self):
        self.finished.set()
        self.thread.join(timeout=.2)

class Transport:
    def __init__(self, budget):
        self.budget = budget

    def fetch(self, url, *, method='GET', data=None, headers=None, limit=8*1024**2):
        if method not in ('GET', 'POST', 'HEAD') or (data is not None and not isinstance(data, bytes)):
            raise TwitchDownloadError('許可されていないHTTP要求です')
        clean = {}
        for name, value in (headers or {}).items():
            if name.lower() in ('authorization', 'cookie', 'proxy-authorization'):
                raise TwitchDownloadError('アカウント認証が必要な取得は対応していません')
            if name.lower() not in ('host', 'accept-encoding', 'connection', 'content-length'):
                clean[name] = value
        clean['Accept-Encoding'] = 'identity'
        clean['Connection'] = 'close'
        for hop in range(6):
            self.budget.check()
            p = validate_media_url(url)
            addresses = public_addresses(p.hostname, self.budget)
            connection = PinnedHTTPS(p.hostname, addresses[0], self.budget.timeout())
            watchdog = Watchdog(connection, self.budget)
            watchdog.start()
            try:
                connection.request(method, p.path + ('?' + p.query if p.query else '') or '/', body=data, headers=clean)
                response = connection.getresponse()
                self.budget.check()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location:
                        raise TwitchDownloadError('不正なリダイレクトです')
                    next_url = urljoin(url, location)
                    validate_media_url(next_url)
                    # Never forward public API bodies or client headers to another host.
                    if urlsplit(next_url).hostname != p.hostname:
                        clean = {'Accept-Encoding': 'identity', 'Connection': 'close'}
                        if method == 'POST':
                            raise TwitchDownloadError('API要求の別ホスト転送を拒否しました')
                    if response.status == 303 or (response.status in (301,302) and method == 'POST'):
                        method, data = 'GET', None
                    url = next_url
                    continue
                if response.status in (401,403):
                    raise TwitchDownloadError('Twitchがアクセスを拒否しました。ログイン・限定公開動画には対応しません')
                if response.status >= 400:
                    raise TwitchDownloadError(f'Twitch取得に失敗しました (HTTP {response.status})')
                encoding = response.getheader('Content-Encoding', 'identity').lower()
                if encoding not in ('identity', ''):
                    raise TwitchDownloadError('未対応のHTTP圧縮です')
                length = response.getheader('Content-Length')
                if length and (not length.isdigit() or int(length) > limit):
                    raise TwitchDownloadError('取得データが容量上限を超えます')
                chunks, count = [], 0
                while True:
                    self.budget.check()
                    block = response.read(64*1024)
                    self.budget.check()
                    if not block:
                        break
                    count += len(block)
                    self.budget.consume(len(block))
                    if count > limit:
                        raise TwitchDownloadError('取得データが容量上限を超えます')
                    chunks.append(block)
                return b''.join(chunks), url, dict(response.getheaders())
            except (OSError, http.client.HTTPException) as exc:
                if watchdog.failure is not None:
                    raise watchdog.failure from exc
                self.budget.check()
                raise TwitchDownloadError('Twitchへのネットワーク接続に失敗しました') from exc
            finally:
                watchdog.stop()
                connection.close()
        raise TwitchDownloadError('リダイレクト回数が上限を超えました')

class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def make_downloader(transport):
    try:
        os.environ['YTDLP_NO_PLUGINS'] = '1'
        import yt_dlp
        from yt_dlp.globals import plugin_ies, plugin_pps, plugin_dirs
        if plugin_ies.value or plugin_pps.value:
            raise TwitchDownloadError('yt-dlpプラグインが読み込まれたプロセスでは取得できません')
        plugin_dirs.value = []
        from yt_dlp.version import __version__
        from yt_dlp.networking.common import Response
        from yt_dlp.extractor.twitch import TwitchVideosIE, TwitchVodIE
    except ImportError as exc:
        raise TwitchDownloadError('Twitch取得には追加パッケージ yt-dlp==2026.8.19 が必要です') from exc
    if __version__ != YTDLP_VERSION:
        raise TwitchDownloadError('検証済み yt-dlp==2026.8.19 を使用してください')
    class Guarded(yt_dlp.YoutubeDL):
        def urlopen(self, request):
            url = request if isinstance(request, str) else request.url
            body, final_url, headers = transport.fetch(
                url, method=getattr(request, 'method', 'GET'),
                data=getattr(request, 'data', None), headers=getattr(request, 'headers', None))
            return Response(io.BytesIO(body), final_url, headers)
    ydl = Guarded({'quiet': True, 'logger': QuietLogger(), 'cachedir': False,
                   'retries': 0, 'extractor_retries': 0, 'socket_timeout': 10,
                   'skip_download': True, 'extract_flat': True, 'playlistend': MAX_ARCHIVES,
                   'noplaylist': True, 'usenetrc': False, 'cookiefile': None,
                   'cookiesfrombrowser': None, 'proxy': '', 'check_formats': False,
                   'js_runtimes': {}, 'remote_components': set()}, auto_init=False)
    ydl.add_info_extractor(TwitchVideosIE())
    vod = TwitchVodIE()
    # Storyboard imagery is irrelevant to acquisition; suppress before any request.
    vod._extract_storyboard = lambda *args, **kwargs: iter(())
    ydl.add_info_extractor(vod)
    return ydl


def notify(progress, stage, fraction, message):
    if progress:
        progress({'stage': stage, 'progress': min(1, max(0, fraction)), 'message': message})


def row(info):
    ident = str(info.get('id', '')).removeprefix('v')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', ident):
        raise TwitchDownloadError('Twitchから不正な動画IDが返されました')
    duration = info.get('duration')
    if not isinstance(duration, (float,int)) or isinstance(duration,bool) or not math.isfinite(duration) or duration <= 0:
        raise TwitchDownloadError('動画の長さを確認できません')
    return {'id': ident, 'url': 'https://www.twitch.tv/videos/' + ident,
            'title': str(info.get('title') or 'Twitch VOD')[:500],
            'duration_ms': round(duration*1000), 'is_live': info.get('is_live') is True,
            'access_verified': False}


def list_archives(raw_url, progress=None, cancelled=None):
    target = parse_twitch_url(raw_url)
    budget = Budget(cancelled, max_bytes=32*1024**2, max_seconds=60)
    notify(progress, 'twitch_listing', 0, '公開アーカイブ一覧を確認しています')
    try:
        with make_downloader(Transport(budget)) as ydl:
            if target['kind'] == 'vod':
                extractor = ydl.get_info_extractor('TwitchVod')
                extractor.initialize()
                # Pinned upstream hook: metadata only, never requests a playback token.
                info = extractor._download_info(target['id'])
                rows = [row(extractor._extract_info_gql(info, target['id']))]
            else:
                info = ydl.extract_info(target['canonical'] + '/videos?filter=archives&sort=time', download=False)
                rows = []
                for entry in info.get('entries', []):
                    budget.check()
                    rows.append(row(entry))
                    if len(rows) >= MAX_ARCHIVES: break
    except TwitchDownloadError:
        raise
    except Exception as exc:
        raise TwitchDownloadError('公開アーカイブ情報を取得できませんでした。アクセス制限やサイト変更の可能性があります') from exc
    notify(progress, 'twitch_listing', 1, '一覧を取得しました。再生・取得可否は未確認です')
    return {'target': target, 'archives': rows}


def parse_segments(text, base, max_media_seconds=None):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != '#EXTM3U' or '#EXT-X-ENDLIST' not in lines:
        raise TwitchDownloadError('完了済みVODのHLSのみ取得できます')
    for line in lines:
        if line.startswith('#EXT-X-KEY:') and line.strip() != '#EXT-X-KEY:METHOD=NONE':
            raise TwitchDownloadError('暗号化HLSには対応していません')
    forbidden = ('#EXT-X-MAP', '#EXT-X-BYTERANGE', '#EXT-X-STREAM-INF', '#EXT-X-I-FRAMES-ONLY')
    if any(line.split(':',1)[0] in forbidden for line in lines):
        raise TwitchDownloadError('暗号化・分割MP4などの未対応HLSです')
    if lines[-1] != '#EXT-X-ENDLIST':
        raise TwitchDownloadError('HLS終了マーカー以降の内容を拒否しました')
    segments = []
    elapsed = 0.0
    pending_duration = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF:'):
            try:
                pending_duration = float(line.split(':',1)[1].split(',',1)[0])
            except ValueError as exc:
                raise TwitchDownloadError('不正なHLS区間長です') from exc
            if not math.isfinite(pending_duration) or pending_duration <= 0:
                raise TwitchDownloadError('不正なHLS区間長です')
        if line and not line.startswith('#'):
            if pending_duration is None:
                raise TwitchDownloadError('HLS区間長がありません')
            if max_media_seconds is not None and elapsed + pending_duration > max_media_seconds:
                break
            elapsed += pending_duration
            pending_duration = None
            url = urljoin(base, line)
            validate_media_url(url)
            segments.append(url)
    if not segments or len(segments) > 20000:
        raise TwitchDownloadError('HLS区間数が不正です')
    return segments, round(elapsed*1000)


def remux(source, destination, budget):
    executable = os.environ.get('CLIPBETA_FFMPEG_PATH') or shutil.which('ffmpeg')
    if not executable:
        raise TwitchDownloadError('MP4作成にはFFmpegが必要です')
    cmd = [executable, '-nostdin', '-v', 'error', '-n', '-protocol_whitelist', 'file,pipe',
           '-f', 'mpegts', '-i', str(source), '-map', '0:v:0', '-map', '0:a:0',
           '-c', 'copy', '-movflags', '+faststart', str(destination)]
    with (source.parent / 'remux.log').open('xb') as log:
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        try:
            while process.poll() is None:
                budget.check()
                if destination.exists() and destination.stat().st_size > budget.max_bytes:
                    raise TwitchDownloadError('MP4容量上限を超えました')
                time.sleep(.1)
            if process.returncode:
                raise TwitchDownloadError('取得映像をMP4へ変換できませんでした')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()



def probe_download(path, budget, max_media_seconds=None):
    """Verify the resulting local file, including bounds independent of EXTINF."""
    executable = os.environ.get('CLIPBETA_FFPROBE_PATH') or shutil.which('ffprobe')
    if not executable:
        raise TwitchDownloadError('取得動画の確認にはffprobeが必要です')
    output = path.parent / 'probe.json'
    cmd = [executable, '-v', 'error', '-protocol_whitelist', 'file,pipe',
           '-f', 'mov', '-show_entries', 'format=duration:stream=codec_type,duration',
           '-of', 'json', str(path)]
    with output.open('xb') as file:
        process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=file, stderr=subprocess.DEVNULL)
        try:
            while process.poll() is None:
                budget.check()
                if output.stat().st_size > 1024**2:
                    raise TwitchDownloadError('動画検査結果が容量上限を超えました')
                time.sleep(.05)
            budget.check()
            if process.returncode or output.stat().st_size > 1024**2:
                raise TwitchDownloadError('取得動画を検査できませんでした')
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
    try:
        data = json.loads(output.read_text())
        streams = data.get('streams', [])
        if not {'video', 'audio'}.issubset({v.get('codec_type') for v in streams}):
            raise ValueError('missing streams')
        durations = [float(data['format']['duration'])]
        durations += [float(v['duration']) for v in streams if v.get('duration') not in (None, 'N/A')]
        if any(not math.isfinite(v) or v <= 0 for v in durations):
            raise ValueError('invalid duration')
        duration = max(durations)
        # Container/audio packet rounding tolerance; never trust playlist duration alone.
        if max_media_seconds is not None and duration > max_media_seconds + .5:
            raise TwitchDownloadError('実動画が許可された検証区間を超えました')
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, TwitchDownloadError): raise
        raise TwitchDownloadError('取得動画の長さ・音声・映像を確認できません') from exc
    output.unlink()
    return round(duration*1000)


def acquire(raw_vod_url, out_dir, progress=None, cancelled=None, *,
            max_bytes=MAX_BYTES, max_media_seconds=None):
    target = parse_twitch_url(raw_vod_url)
    if target['kind'] != 'vod':
        raise TwitchDownloadError('一覧からVODを選択してください')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        raise TwitchDownloadError('容量上限が不正です')
    if max_media_seconds is not None and (not isinstance(max_media_seconds, (int,float)) or not 0 < max_media_seconds <= 60):
        raise TwitchDownloadError('検証区間は60秒以内です')
    out_dir = Path(out_dir)
    # Caller chooses parent; this function exclusively creates the final directory.
    try:
        out_dir.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise TwitchDownloadError('新しい取得フォルダーを作成できません。既存フォルダーは使用しません') from exc
    budget = Budget(cancelled, max_bytes=max_bytes)
    transport = Transport(budget)
    try:
        if shutil.disk_usage(out_dir).free < max_bytes*2 + 128*1024**2:
            raise TwitchDownloadError(f'取得とMP4変換には{(max_bytes*2 + 128*1024**2)/1024**3:.2f}GiB以上の空き容量が必要です')
        notify(progress, 'twitch_acquire', 0, '公開VODの取得可否を確認しています')
        with make_downloader(transport) as ydl:
            info = ydl.extract_info(target['canonical'], download=False, process=False)
        metadata = row(info)
        if metadata['id'] != target['id']:
            raise TwitchDownloadError('要求したVODと取得情報のIDが一致しません')
        if info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming','post_live'):
            raise TwitchDownloadError('進行中の配信は対応していません')
        formats = [f for f in info.get('formats', []) if f.get('protocol') in ('m3u8','m3u8_native')
                   and f.get('vcodec') != 'none' and f.get('acodec') != 'none'
                   and f.get('url') and not f.get('has_drm')]
        if not formats:
            raise TwitchDownloadError('対応する公開HLS動画を取得できません')
        # Prefer <=720p to bound ordinary VOD size; no quality/download fallback on denial.
        candidates = [f for f in formats if (f.get('height') or 10000) <= 720] or formats
        chosen = max(candidates, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
        estimate = chosen.get('filesize') or chosen.get('filesize_approx')
        duration_s = min(metadata['duration_ms']/1000, max_media_seconds or float('inf'))
        if max_media_seconds is not None and isinstance(estimate, (int,float)):
            estimate = estimate * duration_s / (metadata['duration_ms']/1000)
        if not estimate and isinstance(chosen.get('tbr'), (int,float)):
            estimate = chosen['tbr'] * 1000 / 8 * duration_s
        if isinstance(estimate, (int,float)) and math.isfinite(estimate) and estimate > max_bytes:
            raise TwitchDownloadError('推定動画サイズが取得容量上限を超えます。短いVODまたはローカル動画を使用してください')
        body, final, _ = transport.fetch(chosen['url'])
        segments, acquired_duration_ms = parse_segments(body.decode('utf-8'), final, max_media_seconds)
        source = out_dir / 'source.ts'
        with source.open('xb') as file:
            for index, url in enumerate(segments):
                budget.check()
                if shutil.disk_usage(out_dir).free < 128*1024**2:
                    raise TwitchDownloadError('空き容量が不足しています')
                body, _, _ = transport.fetch(url, limit=64*1024**2)
                # Force TS-only demuxing later; reject HTML/error/encrypted segment payloads.
                if len(body) < 188 or body[0] != 0x47 or (len(body)>188 and body[188] != 0x47):
                    raise TwitchDownloadError('未対応の映像セグメントです')
                file.write(body)
                notify(progress, 'twitch_acquire', .9*(index+1)/len(segments), '公開VODを取得しています')
        destination = out_dir / 'source.mp4'
        remux(source, destination, budget)
        budget.check()
        if not destination.is_file() or destination.stat().st_size == 0:
            raise TwitchDownloadError('MP4ファイルが作成されませんでした')
        if destination.stat().st_size > max_bytes:
            raise TwitchDownloadError('MP4容量上限を超えました')
        actual_duration_ms = probe_download(destination, budget, max_media_seconds)
        source.unlink()
        (out_dir/'remux.log').unlink(missing_ok=True)
        metadata['access_verified'] = True
        metadata['partial'] = max_media_seconds is not None
        metadata['source_offset_ms'] = 0
        metadata['source_start_ms'] = 0
        metadata['downloaded_duration_ms'] = actual_duration_ms
        metadata['manifest_duration_ms'] = acquired_duration_ms
        metadata['original_duration_ms'] = metadata['duration_ms']
        metadata['duration_ms'] = actual_duration_ms
        budget.check()
        notify(progress, 'twitch_acquire', 1, '動画取得が完了しました')
        return {'path': destination, 'metadata': metadata}
    except BaseException as exc:
        # Only this freshly created directory is owned by the adapter.
        shutil.rmtree(out_dir)
        if isinstance(exc, (TwitchDownloadError, KeyboardInterrupt, SystemExit)):
            raise
        raise TwitchDownloadError('公開VODの取得に失敗しました。限定公開・削除済み動画やアクセス拒否は回避しません') from exc

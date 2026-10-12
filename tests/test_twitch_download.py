import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from clipbeta import twitch_download as t

class Reply:
    def __init__(self, body=b'ok', status=200, headers=None):
        self.body=io.BytesIO(body); self.status=status; self.headers=headers or {}
    def getheader(self,key,default=None):return self.headers.get(key,default)
    def getheaders(self):return list(self.headers.items())
    def read(self,size):return self.body.read(size)
class Connection:
    responses=[]; destinations=[]
    def __init__(self,host,address,timeout):self.destinations.append((host,address))
    def request(self,*args,**kwargs):pass
    def getresponse(self):return self.responses.pop(0)
    def close(self):pass

class TwitchDownloadTests(unittest.TestCase):
    def test_allowlist(self):
        for url in ['http://gql.twitch.tv/a','https://twitch.tv.evil/a','https://evilcloudfront.net/a',
                    'https://gql.twitch.tv:444/a','https://x@gql.twitch.tv/a','https://127.0.0.1/a',
                    'https://gql.twitch.tv/a\n','https://gql.twitch.tv/a#foo']:
            with self.subTest(url=url),self.assertRaises(t.TwitchDownloadError):t.validate_media_url(url)
        for url in ['https://gql.twitch.tv/gql','https://video-weaver.test.hls.ttvnw.net/a', 'https://distribution.cloudfront.net/a?token=x']:
            t.validate_media_url(url)
    def test_rejects_nonpublic_dns(self):
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:127.0.0.1','224.0.0.1','100.64.0.1']:
            with self.subTest(address=address),patch.object(socket,'getaddrinfo',return_value=[(2,1,6,'',(address,443))]):
                with self.assertRaises(t.TwitchDownloadError):t.public_addresses('gql.twitch.tv',t.Budget())
    def test_mixed_dns_rejected(self):
        with patch.object(socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(t.TwitchDownloadError):t.public_addresses('gql.twitch.tv',t.Budget())
    def test_redirect_guard(self):
        Connection.responses=[Reply(status=302,headers={'Location':'https://127.0.0.1/a'})]
        Connection.destinations=[]
        with patch.object(t,'PinnedHTTPS',Connection),patch.object(t,'public_addresses',return_value=['8.8.8.8']):
            with self.assertRaises(t.TwitchDownloadError):t.Transport(t.Budget()).fetch('https://gql.twitch.tv/a')
        self.assertEqual(len(Connection.destinations),1)
    def test_redirect_resolves_again(self):
        Connection.responses=[Reply(status=302,headers={'Location':'/b'}),Reply()]
        with patch.object(t,'PinnedHTTPS',Connection),patch.object(t,'public_addresses',side_effect=[['8.8.8.8'],t.TwitchDownloadError('private')]) as dns:
            with self.assertRaises(t.TwitchDownloadError):t.Transport(t.Budget()).fetch('https://gql.twitch.tv/a')
        self.assertEqual(dns.call_count,2)
    def test_no_credentials_or_denial_retry(self):
        with self.assertRaises(t.TwitchDownloadError):t.Transport(t.Budget()).fetch('https://gql.twitch.tv/a',headers={'Cookie':'x'})
        Connection.responses=[Reply(status=403)]
        Connection.destinations=[]
        with patch.object(t,'PinnedHTTPS',Connection),patch.object(t,'public_addresses',return_value=['8.8.8.8']):
            with self.assertRaises(t.TwitchDownloadError):t.Transport(t.Budget()).fetch('https://gql.twitch.tv/a')
        self.assertEqual(len(Connection.destinations),1)
    def test_body_limit(self):
        Connection.responses=[Reply(body=b'12345')]
        with patch.object(t,'PinnedHTTPS',Connection),patch.object(t,'public_addresses',return_value=['8.8.8.8']):
            with self.assertRaises(t.TwitchDownloadError):t.Transport(t.Budget(max_bytes=4)).fetch('https://gql.twitch.tv/a')
    def test_cancel_timeout(self):
        with self.assertRaises(t.TwitchDownloadError):t.Budget(lambda:True).check()
        with self.assertRaises(t.TwitchDownloadError):t.Budget(max_seconds=-1).check()
    def test_hls_bounds(self):
        base='https://cdn.ttvnw.net/test/index.m3u8'
        manifest='#EXTM3U\n#EXTINF:6,\na.ts\n#EXTINF:6,\nb.ts\n#EXT-X-ENDLIST\n'
        segments,duration=t.parse_segments(manifest,base,6)
        self.assertEqual(segments,['https://cdn.ttvnw.net/test/a.ts']);self.assertEqual(duration,6000)
        for bad in [manifest.replace('#EXT-X-ENDLIST',''),manifest.replace('a.ts','https://localhost/a'),
                    manifest.replace('#EXTINF:6,','#EXT-X-KEY:METHOD=AES-128\n#EXTINF:6,'),
                    manifest.replace('#EXTINF:6,','#EXT-X-MAP:URI="x"\n#EXTINF:6,'),
                    manifest.replace('#EXTINF:6,','#EXTINF:nan,')]:
            with self.assertRaises(t.TwitchDownloadError):t.parse_segments(bad,base)
        t.parse_segments(manifest.replace('#EXTM3U','#EXTM3U\n#EXT-X-KEY:METHOD=NONE'),base)
    def test_row_redacts_metadata(self):
        value=t.row({'id':'v123','duration':6,'title':'Title','formats':[{'url':'secret'}]})
        self.assertNotIn('formats',value);self.assertFalse(value['access_verified'])
    def test_no_overwrite_and_invalid_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'existing';p.mkdir();(p/'keep').write_text('ok')
            with self.assertRaises(t.TwitchDownloadError):t.acquire('https://www.twitch.tv/videos/123',p)
            self.assertEqual((p/'keep').read_text(),'ok')
            with self.assertRaises(t.TwitchDownloadError):t.acquire('https://www.twitch.tv/channel',Path(tmp)/'new')
    def test_failure_cleans_only_own_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'new'
            with patch.object(t,'make_downloader',side_effect=t.TwitchDownloadError('stop')):
                with self.assertRaises(t.TwitchDownloadError):t.acquire('https://www.twitch.tv/videos/123',p,max_bytes=1024)
            self.assertFalse(p.exists());self.assertTrue(Path(tmp).is_dir())
    def test_pinned_tls(self):
        fake=unittest.mock.Mock()
        with patch.object(t.socket,'create_connection',return_value=fake) as connect:
            conn=t.PinnedHTTPS('gql.twitch.tv','8.8.8.8',2)
            conn._context=unittest.mock.Mock();conn.connect()
            connect.assert_called_once_with(('8.8.8.8',443),timeout=2)
            conn._context.wrap_socket.assert_called_once_with(fake,server_hostname='gql.twitch.tv',do_handshake_on_connect=False)
    def test_optional_yt_dlp_guard(self):
        try:import yt_dlp
        except ImportError:self.skipTest('optional dependency not installed')
        transport=unittest.mock.Mock();transport.fetch.return_value=(b'{}','https://gql.twitch.tv/gql',{})
        with t.make_downloader(transport) as ydl:
            from yt_dlp.networking import Request
            result=ydl.urlopen(Request('https://gql.twitch.tv/gql'))
            self.assertEqual(result.read(),b'{}')
            self.assertEqual(set(ydl._ies),{'TwitchVideos','TwitchVod'})
            self.assertEqual(list(ydl.get_info_extractor('TwitchVod')._extract_storyboard('123','https://bad.test',1)),[])
        transport.fetch.assert_called_once()

    def test_reject_bool_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(t.TwitchDownloadError):t.acquire('https://www.twitch.tv/videos/123',Path(tmp)/'new',max_bytes=True)
    def test_acquire_mocked_network_real_ffmpeg(self):
        import shutil,subprocess
        if not shutil.which('ffmpeg'):self.skipTest('ffmpeg unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);segment=tmp/'fixture.ts'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=s=160x90:r=10:d=1',
                            '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac',
                            '-f','mpegts',str(segment)],check=True)
            info={'id':'123','title':'Synthetic only','duration':100,'formats':[{
                'protocol':'m3u8_native','url':'https://cdn.ttvnw.net/a.m3u8','height':360,'tbr':20}]}
            ydl=unittest.mock.MagicMock();ydl.__enter__.return_value=ydl;ydl.extract_info.return_value=info
            response=[(b'#EXTM3U\n#EXTINF:1,\na.ts\n#EXT-X-ENDLIST\n','https://cdn.ttvnw.net/a.m3u8',{}),
                      (segment.read_bytes(),'https://cdn.ttvnw.net/a.ts',{})]
            with patch.object(t,'make_downloader',return_value=ydl),patch.object(t.Transport,'fetch',side_effect=response):
                value=t.acquire('https://www.twitch.tv/videos/123',tmp/'output',max_media_seconds=1,max_bytes=1024**2)
            self.assertTrue(value['path'].is_file());self.assertLessEqual(abs(value['metadata']['duration_ms']-1000),500)
            self.assertEqual(value['metadata']['original_duration_ms'],100000)
            self.assertLessEqual(abs(value['metadata']['downloaded_duration_ms']-1000),500)
            self.assertEqual(value['metadata']['manifest_duration_ms'],1000)
            self.assertEqual(value['metadata']['source_start_ms'],0)
            self.assertNotIn('formats',value['metadata'])
    def test_size_estimate_fails_before_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            info={'id':'123','duration':3600,'formats':[{'protocol':'m3u8_native','url':'https://cdn.ttvnw.net/a',
                  'height':720,'tbr':10000}]}
            ydl=unittest.mock.MagicMock();ydl.__enter__.return_value=ydl;ydl.extract_info.return_value=info
            with patch.object(t,'make_downloader',return_value=ydl),patch.object(t.Transport,'fetch') as fetch:
                with self.assertRaises(t.TwitchDownloadError):t.acquire('https://www.twitch.tv/videos/123',Path(tmp)/'out',max_bytes=1024**2)
                fetch.assert_not_called()

    def test_watchdog_interrupts_stalled_socket(self):
        import threading,time
        interrupted=threading.Event()
        active=unittest.mock.Mock();active.shutdown.side_effect=lambda how:interrupted.set()
        connection=unittest.mock.Mock(active_socket=active)
        watchdog=t.Watchdog(connection,t.Budget(max_seconds=.03));watchdog.start()
        self.assertTrue(interrupted.wait(.3));watchdog.stop()
        self.assertIsInstance(watchdog.failure,t.TwitchDownloadError)
        active.close.assert_called_once()
    def test_eof_rechecks_deadline(self):
        import time
        class SlowReply(Reply):
            def read(self,size):time.sleep(.06);return b''
        Connection.responses=[SlowReply()]
        with patch.object(t,'PinnedHTTPS',Connection),patch.object(t,'public_addresses',return_value=['8.8.8.8']):
            with self.assertRaises(t.TwitchDownloadError):t.Transport(t.Budget(max_seconds=.02)).fetch('https://gql.twitch.tv/a')
    def test_watchdog_cancellation(self):
        import threading
        cancel=threading.Event();interrupted=threading.Event()
        active=unittest.mock.Mock();active.shutdown.side_effect=lambda how:interrupted.set()
        watchdog=t.Watchdog(unittest.mock.Mock(active_socket=active),t.Budget(cancel.is_set));watchdog.start()
        cancel.set();self.assertTrue(interrupted.wait(.3));watchdog.stop()

    def test_hls_exact_completion_and_whitespace_encryption(self):
        base='https://cdn.ttvnw.net/a.m3u8'
        for text in ['#EXTM3U\n#EXTINF:1,\na.ts\n#comment=#EXT-X-ENDLIST',
                     '#EXTM3U\n  #EXT-X-KEY:METHOD=AES-128\n#EXTINF:1,\na.ts\n#EXT-X-ENDLIST',
                     '#EXTM3U\n#EXT-X-ENDLIST\n#EXTINF:1,\na.ts']:
            with self.assertRaises(t.TwitchDownloadError):t.parse_segments(text,base)
    def test_probe_enforces_actual_duration(self):
        import shutil,subprocess
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('FFmpeg tools unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'long.mp4'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=s=160x90:r=10:d=2',
                            '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac',str(path)],check=True)
            with self.assertRaises(t.TwitchDownloadError):t.probe_download(path,t.Budget(),max_media_seconds=.5)

if __name__=='__main__':unittest.main()

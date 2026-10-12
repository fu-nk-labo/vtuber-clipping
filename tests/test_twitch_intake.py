import unittest
from clipbeta.twitch_intake import parse_twitch_url
from clipbeta.engine import ValidationError
class TwitchIntakeTests(unittest.TestCase):
    def test_channel_and_vod(self):
        self.assertEqual(parse_twitch_url('https://m.twitch.tv/Example/videos?filter=archives')['login'],'example')
        self.assertEqual(parse_twitch_url('https://www.twitch.tv/videos/123?t=1h')['id'],'123')
    def test_dangerous_urls_never_fetch(self):
        for value in ['http://twitch.tv/a','https://twitch.tv.evil.test/a','https://twitch.tv@127.0.0.1/a','https://127.0.0.1/videos/1','https://twitch.tv:443/a','https://twitch.tv/%2f%2fexample.com','https://twitch.tv/a\\x','https://twitch.tv/videos/01','https://twitch.tv/settings','https://twitch.tv/a/b','https://twitch.tv/a\n']:
            with self.subTest(value=value),self.assertRaises(ValidationError):parse_twitch_url(value)
    def test_parsing_returns_only_unverified_target(self):
        value=parse_twitch_url('https://twitch.tv/videos/123')
        self.assertEqual(set(value),{'kind','id','canonical'})
        self.assertEqual(value['canonical'],'https://www.twitch.tv/videos/123')

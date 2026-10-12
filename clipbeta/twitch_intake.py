"""Twitch URL boundary: validates intent without pretending metadata is media.

No URL fetch or credential handling exists here. Optional acquisition lives in
twitch_download; see docs/TWITCH_URL_STATUS.md for its verification boundary.
"""
import re
from .engine import require

RESERVED=set('videos directory downloads settings subscriptions inventory wallet login signup search p jobs turbo products store'.split())

def parse_twitch_url(raw):
    require(isinstance(raw,str) and 0<len(raw)<=2048 and not re.search(r'[\x00-\x20\x7f\\%]',raw), 'HTTPSのTwitch URLを入力してください')
    m=re.fullmatch(r'https://(?:(?:www|m)\.)?twitch\.tv(?P<path>/[^?#]*)?(?:\?[^#]*)?(?:#.*)?',raw,re.I)
    require(m is not None,'許可されるホストは twitch.tv / www.twitch.tv / m.twitch.tv のみです')
    path=m.group('path') or '/'
    vod=re.fullmatch(r'/videos/([1-9][0-9]{0,19})/?',path)
    if vod:return {'kind':'vod','id':vod[1],'canonical':'https://www.twitch.tv/videos/'+vod[1]}
    channel=re.fullmatch(r'/([a-zA-Z0-9_]{1,25})(?:/videos)?/?',path)
    require(channel is not None and channel[1].lower() not in RESERVED,'チャンネルまたは /videos/数字ID のURLを入力してください')
    login=channel[1].lower()
    return {'kind':'channel','login':login,'canonical':'https://www.twitch.tv/'+login}

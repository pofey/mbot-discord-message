import logging
import typing

import httpx
from httpx import HTTPError
from moviebotapi.core.models import MediaType
from moviebotapi.ext import MediaMetaSelect
from tenacity import retry, stop_after_attempt, wait_fixed

from mbot.common.dictutils import DictWrapper
from mbot.common.mediaformatutils import MediaFormatUtils
from mbot.common.stringutils import StringUtils
from mbot.openapi import mbot_api
from mbot.core.event.models import EventType

_LOGGER = logging.getLogger(__name__)


class DiscordMessage:
    def __init__(self):

        self.webhook = None
        self.proxy = None

    def set_config(self, webhook: str, proxy: typing.Optional[str] = None):

        self.webhook = webhook
        self.proxy = proxy

    @retry(wait=wait_fixed(3), stop=stop_after_attempt(5))
    def send_discord_message(self, json_data):
        res = httpx.post(
            self.webhook,
            json=json_data,
            proxies=self.proxy if self.proxy else None
        )
        try:
            res.raise_for_status()
            return True
        except HTTPError as err:
            _LOGGER.error(f'发送消息失败{err}')
            return False

    def send_card_message(self, author, data):
        """
        发送带样式的卡片消息
        如果想自己调整消息格式样式，可以修改这个地方的代码
        可以去这个网站，在线测试样式效果，拿到json结构：https://discohook.org/
        """
        if data.get('media_type') == 'Movie':
            title = f"{data.get('title')}({data.get('year')})"
        else:
            title = f"{data.get('title')}({data.get('year')})"
            if data.get('season_number'):
                title += f" - S{data.get('season_number')}"
            if data.get('episodes'):
                title += f"E{data.get('episodes')}"
        fields = []
        if data.get('media_stream'):
            value = ''
            if data['media_stream'].get('media_source'):
                value += data['media_stream'].get('media_source')
            if data['media_stream'].get('resolution'):
                if value:
                    value += ' · '
                value += f"{data['media_stream'].get('resolution')}"
            if data['media_stream'].get('file_size'):
                if value:
                    value += ' · '
                value += f"{data['media_stream'].get('file_size')}"
            if data['media_stream'].get('release_team'):
                if value:
                    value += ' · '
                value += f"{data['media_stream'].get('release_team')}"
            if data.get('file_size'):
                if value:
                    value += ' · '
                value += f"{data['file_size']}"
            fields.append({
                "name": "品质",
                "value": value
            })
        else:
            if data.get('file_size'):
                fields.append({
                    "name": "品质",
                    "value": f"{data['file_size']}"
                })
        if data.get('genres'):
            value = ''
            if data.get('country'):
                value += ' · '.join(data.get('country'))
            if data.get('genres'):
                value += ' ' + ' · '.join(data.get('genres'))
            if value:
                fields.append({
                    "name": "风格",
                    "value": value,
                    "inline": True
                })
        self.send_discord_message({
            "content": None,
            "embeds": [
                {
                    "title": title,
                    "description": data.get('intro').strip() if data.get('intro') else '',
                    "url": data.get('link_url'),
                    "color": 5814783,
                    "fields": fields,
                    "author": {
                        "name": author
                    },
                    "image": {
                        "url": data.get('pic_url')
                    }
                }
            ],
            "attachments": []
        })

    def notify_download_completed(self, data):
        if not data.get('tmdb_id') and not data.get('douban_id'):
            # 未识别的下载记录，或者AV等特殊分类
            return
        # 把接收到的一些事件处理，处理为通知用
        data.update({
            'season_number': str(data.get('season_number')).zfill(2) if data.get('season_number') else None,
            'episodes': MediaFormatUtils.episode_format(data.get('episodes'))
        })
        if data.get('site_name'):
            author = f"来自{data.get('nickname')}的资源下载完成"
        else:
            author = '下载完成'
        self.send_card_message(author, data)

    def notify_download_start(self, data):
        if not data.get('tmdb_id') and not data.get('douban_id'):
            return
        # 把接收到的一些事件处理，处理为通知用
        data.update({
            'season_number': str(data.get('season_number')).zfill(2) if data.get('season_number') else None,
            'episodes': MediaFormatUtils.episode_format(data.get('episodes'))
        })
        if data.get('site_name'):
            author = f"来自{data.get('nickname')}的资源开始下载"
        else:
            author = '来自手动下载'
        self.send_card_message(author, data)

    @staticmethod
    def _get_media_type(data):
        media_type = data.get('media_type')
        if not media_type:
            # 订阅来源的数据结构是type
            media_type = data.get('type')
        return MediaType(media_type) if media_type else None

    def _get_tmdb_meta(self, data):
        if 'tmdb_meta' in data:
            return data.get('tmdb_meta')
        if not data.get('tmdb_id'):
            return
        return mbot_api.tmdb.get(self._get_media_type(data), data.get_int('tmdb_id'))

    @staticmethod
    def _get_douban_meta(data):
        if 'douban_meta' in data:
            return data.get('douban_meta')
        if not data.get('douban_id'):
            return
        return mbot_api.douban.get(data.get_int('douban_id'))

    def send_by_event(self, event_type: str, data: typing.Dict):
        data = DictWrapper(data)
        media_type = self._get_media_type(data)
        tmdb_meta = self._get_tmdb_meta(data)
        douban_meta = self._get_douban_meta(data)
        # x_meta是自建影视数据，可能有，可能没有，需要做特殊处理
        x_meta = data.get('x_meta')
        if x_meta:
            background_url = None
            media_image = mbot_api.scraper.get_images(media_type, int(x_meta.get('tmdbId')),
                                                      season_number=data.get('season_number'))
            if media_image:
                background_url = media_image.main_background
            data.update({
                'title': x_meta.get('title'),
                'rating': x_meta.get('rating'),
                'link_url': 'https://movie.douban.com/subject/%s/' % x_meta.get('doubanId'),
                'pic_url': background_url,
                'genres': x_meta.get('genres'),
                'country': x_meta.get('country'),
                'year': x_meta.get('releaseYear'),
                'intro': x_meta.get('intro'),
                'release_date': x_meta.get('premiereDate')
            })
        else:
            if tmdb_meta or douban_meta:
                background_url = None
                if tmdb_meta:
                    # 这里如果只有豆瓣，怎么通知
                    media_image = mbot_api.scraper.get_images(media_type, tmdb_meta.id,
                                                              season_number=data.get('season_number'))
                    if media_image:
                        background_url = media_image.main_background
                if not background_url and douban_meta:
                    background_url = douban_meta.cover_image
                best_meta = MediaMetaSelect(douban_meta, tmdb_meta)
                data.update({
                    'title': best_meta.title,
                    'rating': best_meta.rating,
                    'link_url': best_meta.url,
                    'pic_url': background_url,
                    'genres': best_meta.genres,
                    'country': best_meta.country,
                    'year': best_meta.release_year,
                    'intro': best_meta.intro,
                    'release_date': best_meta.release_date
                })
        if data.get('uid') and not data.get('nickname'):
            user = mbot_api.user.get(data.get_int('uid'))
            if user:
                data.update({
                    'nickname': user.nickname
                })
        if not data.get('nickname'):
            data['nickname'] = '未知用户'
        # 根据不同的事件类型，对可用数据做些转化，再发送消息
        if event_type == EventType.DownloadCompleted.name:
            self.notify_download_completed(data)
        elif event_type == EventType.SubMedia.name:
            author = f"新增来自{data.get('nickname')}的订阅"
            self.send_card_message(author, data)
        elif event_type == EventType.DownloadStart.name:
            self.notify_download_start(data)
        elif event_type == EventType.SiteError.name:
            self.send_discord_message({
                "content": StringUtils.render_text('访问{{ site_name }}异常，错误原因：{{ reason }}', **data),
                "embeds": None,
                "attachments": []
            })

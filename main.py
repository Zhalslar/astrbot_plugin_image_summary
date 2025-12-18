import json
import random
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


class ImageSummaryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.session = None
        self.local_quotes: list[str] = self._load_quotes(
            Path(__file__).resolve().parent / "local_quotes.json"
        )

    @filter.on_decorating_result(priority=5)
    async def on_image_summary(self, event: AiocqhttpMessageEvent):
        """监听消息进行图片外显"""
        # 白名单群
        group_id = event.get_group_id()
        if (
            self.config["group_whitelist"]
            and group_id not in self.config["group_whitelist"]
        ):
            return

        result = event.get_result()
        if not result:
            return

        chain = result.chain

        # 仅考虑单张图片消息
        if chain and len(chain) == 1 and isinstance(chain[0], Image):
            # 注入summary
            obmsg: list[dict] = await event._parse_onebot_json(MessageChain(chain))
            obmsg[0]["data"]["summary"] = await self.get_quote()
            # 发送消息
            await event.bot.send(event.message_obj.raw_message, obmsg)  # type: ignore
            # 清空原消息链
            chain.clear()

    async def get_quote(self, max_len=20):
        """获取外显文本, 过长则截断"""
        quote = None
        source = self.config["yiyan_source"].split(":")[0]
        if source == "local" and self.local_quotes:
            quote = random.choice(self.local_quotes)
        elif source == "config":
            quote = random.choice(self.config["config_quotes"])
        else:
            res = await self._make_request(urls=self.config["api_quotes"])
            if isinstance(res, str):
                quote = res[:max_len]
        if not quote:
            quote = quote = random.choice(self.local_quotes)
        logger.debug(f"图片外显: {quote}")
        return quote

    def _load_quotes(self, path: Path) -> list[str]:
        """
        加载json，返回字符串列表。
        """
        try:
            if not path.exists():
                # 自动创建目录和空文件
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
                logger.info(f"文件不存在，已自动创建: {path}")
                return []
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # 支持 {"quotes":[...]} 或直接 [...]
            if isinstance(data, dict):
                quotes = data.get("quotes", [])
            else:
                quotes = data

            if isinstance(quotes, list) and all(isinstance(q, str) for q in quotes):
                logger.info(f"已加载 {len(quotes)} 条句子")
                return quotes
            else:
                logger.warning(f"{path} 中的格式不正确，需为字符串列表")
                return []
        except Exception as e:
            logger.error(f"读取 {path} 失败: {e}")
            return []

    async def _make_request(self, urls: list[str]) -> str | None:
        """
        随机顺序尝试所有 URL，直到拿到「可当作文本」的内容。
        如果返回 JSON，尝试取其中 'content' 或 'text' 字段；若拿不到，继续换下一个 URL。
        如果返回纯文本，直接返回。
        其余情况视为失败，继续重试。
        """
        if not self.session:
            self.session = aiohttp.ClientSession()
        if not urls:
            return None
        # 随机打乱顺序，避免每次都打到第一个
        for url in random.sample(urls, k=len(urls)):
            try:
                async with self.session.get(url, timeout=30) as resp:
                    resp.raise_for_status()
                    ctype = resp.headers.get("Content-Type", "").lower()

                    if "application/json" in ctype:
                        data = await resp.json()
                        # 兼容常见字段
                        text = (
                            data.get("content") or data.get("text") or data.get("msg")
                        )
                        if text and isinstance(text, str):
                            return text.strip()

                    elif "text/html" in ctype or "text/plain" in ctype:
                        return (await resp.text()).strip()

                    # 其余类型直接跳过
                    logger.warning(f"{url} 返回非文本类型，跳过")
                    continue

            except Exception as e:
                logger.warning(f"请求 URL 失败: {url}, 错误: {e}")
                continue

        logger.error("所有 yiyan_urls 均未能获取到可用文本")
        return None

    async def terminate(self):
        if self.session:
            await self.session.close()
            logger.info("已关闭astrbot_plugin_image_summary的网络连接")

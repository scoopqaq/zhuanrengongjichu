# -*- coding: utf-8 -*-
"""
支持「插件设置」界面配置的转人工插件
文件名：plugins/transfer_to_agent_with_config.py
"""
import httpx
import time
import logging
from datetime import datetime, time as dt_time

from pkg.plugin.context import register, handler, BasePlugin, EventContext
from pkg.plugin.events import PersonNormalMessageReceived
from pkg.platform.types import MessageChain, Plain

# ---------- 插件默认配置（第一次加载时自动写入数据库） ----------
config_default = {
    "open_kfid": "wk7m0ECAAAJIe_OYgcBEt5hGxXFrbqUA",      # 客服账号 ID
    "wecom_corp_id": "ww490150746d039eda",                # 企业 ID
    "wecom_secret": "iYNQBMi9vjFQsN6YM3opk1yCVdKfr_pGK_NVHkaBLJE"  # 应用 Secret
}
# --------------------------------------------------------------

# ---------- AccessToken 缓存 ----------
_access_cache = {"token": None, "expires_at": 0}

async def _get_access_token(corp_id: str, secret: str) -> str | None:
    now = int(time.time())
    if _access_cache["token"] and _access_cache["expires_at"] > now:
        return _access_cache["token"]

    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}"
    try:
        async with httpx.AsyncClient() as cli:
            res = await cli.get(url)
            res.raise_for_status()
            data = res.json()
        if data.get("errcode") == 0:
            _access_cache["token"] = data["access_token"]
            _access_cache["expires_at"] = now + 7000
            return _access_cache["token"]
        else:
            logging.error(f"gettoken failed: {data}")
    except Exception as e:
        logging.error(f"gettoken exception: {e}")
    return None
# --------------------------------------

# -------------- 工具函数 --------------
def _is_night() -> bool:
    now = datetime.now().time()
    return now < dt_time(8, 30)

def _format_uid(raw: str) -> str | None:
    idx = raw.find("wm")
    return raw[idx:].rstrip("!") if idx != -1 else None
# --------------------------------------

# -------------- 插件主体 --------------
@register(
    name="TransferToAgentConfig",
    description="支持界面配置的企微转人工插件（含夜间提示、图片识别）",
    version="3.1",
    author="YourName",
    config_default=config_default          # 关键：把默认配置挂到插件
)
class TransferToAgentPlugin(BasePlugin):

    # 读取用户界面的配置
    def __init__(self, host):
        super().__init__(host)
        cfg = self.config                      # 类型: dict
        self._open_kfid   = cfg["open_kfid"]
        self._corp_id     = cfg["wecom_corp_id"]
        self._secret      = cfg["wecom_secret"]

    # 插件生命周期
    async def initialize(self): pass
    def __del__(self): pass

    # ---- 主要事件 ----
    @handler(PersonNormalMessageReceived)
    async def handle_message(self, ctx: EventContext):
        msg = ctx.event.text_message or ""
        uid = _format_uid(ctx.event.sender_id)
        if not uid:
            return

        # 1. 图片消息
        if msg == "[图片]":
            if _is_night():
                text = ("智能客服暂不支持处理文字外的信息，且人工客服暂时未在线哦～\n"
                        "人工客服在线时间为 每周一至周日 08:30-23:59，若有使用问题，"
                        "您可以先留言，我们上线后会第一时间为您解答！")
            else:
                text = "智能客服无法处理文字以外的信息，已帮您转入人工服务，请稍等。"
            await ctx.reply(MessageChain([Plain(text)]))
            await self._transfer(ctx, uid)
            return

        # 2. 夜间关键字
        if ("转人工" in msg or "找客服" in msg) and _is_night():
            text = ("人工客服在线时间为 每周一至周日 08:30-23:59，若有使用问题，"
                    "您可以先留言，我们上线后会第一时间为您解答！")
            await ctx.reply(MessageChain([Plain(text)]))
            await self._transfer(ctx, uid)
            return

        # 3. 白天关键字 & 状态判断（沿用你原来逻辑）
        if "转人工" in msg or "找客服" in msg:
            await self._transfer(ctx, uid)

    # ---- 转人工实现 ----
    async def _transfer(self, ctx: EventContext, user_id: str):
        token = await _get_access_token(self._corp_id, self._secret)
        if not token:
            await ctx.reply(MessageChain([Plain("系统繁忙，转接失败，请稍后再试")]))
            ctx.prevent_default()
            return

        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans?access_token={token}"
        payload = {"open_kfid": self._open_kfid, "external_userid": user_id, "service_state": 2}
        try:
            async with httpx.AsyncClient() as cli:
                r = await cli.post(url, json=payload)
                r.raise_for_status()
                if r.json().get("errcode") == 0:
                    self.ap.logger.info(f"转人工成功：{user_id}")
                else:
                    self.ap.logger.error(f"转人工失败：{r.json()}")
                    await ctx.reply(MessageChain([Plain("转接失败，请稍后重试")]))
        except Exception as e:
            self.ap.logger.error(f"转人工异常：{e}")
            await ctx.reply(MessageChain([Plain("网络异常，请稍后重试")]))
        finally:
            ctx.prevent_default()

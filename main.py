from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_minecraft_join_verify"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
UUID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
ANSWER_MARKERS = ("答案：", "答案:")
DEFAULT_REJECT_REASON = "未查询到您的 ID，请确保只填写正版 ID 或 UUID 后重试。"
DEFAULT_UUID_LOOKUP_URL_TEMPLATE = (
    "https://api.minecraftservices.com/minecraft/profile/lookup/{uuid}"
)


class LookupState(str, Enum):
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(slots=True)
class LookupResult:
    state: LookupState
    username: str
    status_code: int | None = None
    detail: str = ""


def _raw_get(raw_message: Any, key: str, default: Any = None) -> Any:
    getter = getattr(raw_message, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def _is_group_add_request(event: AstrMessageEvent) -> bool:
    if event.get_platform_name() != "aiocqhttp":
        return False

    raw_message = getattr(event.message_obj, "raw_message", None)
    return (
        _raw_get(raw_message, "post_type") == "request"
        and _raw_get(raw_message, "request_type") == "group"
        and _raw_get(raw_message, "sub_type") == "add"
    )


def _is_group_increase_notice(event: AstrMessageEvent) -> bool:
    if event.get_platform_name() != "aiocqhttp":
        return False

    raw_message = getattr(event.message_obj, "raw_message", None)
    return (
        _raw_get(raw_message, "post_type") == "notice"
        and _raw_get(raw_message, "notice_type") == "group_increase"
    )


class GroupAddRequestFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return _is_group_add_request(event)


class GroupIncreaseNoticeFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        return _is_group_increase_notice(event)


@register(
    PLUGIN_NAME,
    "NACloud3",
    "自动校验 Minecraft 正版 ID 并按配置处理 QQ 入群申请",
    "0.5.0",
)
class ApprovePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.lookup_url_template = str(
            self.config.get(
                "lookup_url_template",
                "https://api.mojang.com/users/profiles/minecraft/{username}",
            )
        )
        self.uuid_lookup_url_template = str(
            self.config.get(
                "uuid_lookup_url_template",
                DEFAULT_UUID_LOOKUP_URL_TEMPLATE,
            )
        )
        self.timeout_seconds = float(self.config.get("timeout_seconds", 8.0))
        self.proxy = self._get_optional_str("proxy")
        self.target_group_ids = set(self._get_str_list("target_group_ids"))
        self.auto_approve = bool(self.config.get("auto_approve", True))
        self.auto_reject = bool(self.config.get("auto_reject", False))
        self.delay_seconds = max(0.0, float(self.config.get("delay_seconds", 0)))
        self.dry_run = bool(self.config.get("dry_run", False))
        self.reject_reason = str(
            self.config.get(
                "reject_reason",
                DEFAULT_REJECT_REASON,
            )
        )
        self.enable_set_group_card = bool(
            self.config.get("enable_set_group_card", True)
        )
        self.group_card_template = str(
            self.config.get("group_card_template", "{username}")
        )
        self.card_delay_seconds = max(
            0.0, float(self.config.get("card_delay_seconds", 0))
        )
        self.pending_card_ttl_hours = max(
            0.0, float(self.config.get("pending_card_ttl_hours", 24))
        )

        self._data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._pending_cards_path = self._data_dir / "pending_cards.json"
        self.pending_cards: dict[str, dict[str, Any]] = {}
        self._load_pending_cards()

    def _get_optional_str(self, key: str) -> str | None:
        value = self.config.get(key, "")
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _get_str_list(
        self,
        key: str,
        default: list[str] | None = None,
    ) -> list[str]:
        value = self.config.get(key, default or [])
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, list):
            return list(default or [])
        return [str(item).strip() for item in value if str(item).strip()]

    @filter.custom_filter(GroupAddRequestFilter, priority=100)
    async def handle_group_add_request(self, event: AstrMessageEvent) -> None:
        """Handle OneBot group join requests."""
        raw_message = getattr(event.message_obj, "raw_message", None)
        group_id = str(_raw_get(raw_message, "group_id", "") or "")
        user_id = str(_raw_get(raw_message, "user_id", "") or "")
        flag = str(_raw_get(raw_message, "flag", "") or "")
        sub_type = str(_raw_get(raw_message, "sub_type", "add") or "add")
        comment = str(_raw_get(raw_message, "comment", "") or "")

        if self.target_group_ids and group_id not in self.target_group_ids:
            logger.debug(
                "[入群审批] 忽略非目标群的入群申请: 群=%s 用户=%s",
                group_id,
                user_id,
            )
            return

        identifier = self.extract_identifier(comment)
        if identifier is None:
            logger.info(
                "[入群审批] 未在入群申请中找到 Minecraft 正版 ID 或 UUID: 群=%s 用户=%s 申请信息=%r",
                group_id,
                user_id,
                comment,
            )
            if self.auto_reject:
                await self.reject_request(
                    event,
                    flag=flag,
                    sub_type=sub_type,
                    reason=self.reject_reason,
                    log_context=f"group={group_id} user={user_id} no_username",
                )
            else:
                logger.info(
                    "[入群审批] 自动拒绝已关闭，不处理格式不完整的入群申请: 群=%s 用户=%s",
                    group_id,
                    user_id,
                )
            event.stop_event()
            return

        result = await self.lookup_identifier(identifier)
        if result.state == LookupState.NOT_FOUND:
            if self.auto_reject:
                await self.reject_request(
                    event,
                    flag=flag,
                    sub_type=sub_type,
                    reason=self.reject_reason,
                    log_context=f"group={group_id} user={user_id} identifier={identifier}",
                )
            else:
                logger.info(
                    "[入群审批] 自动拒绝已关闭，不处理未查询到 ID 的入群申请: 群=%s 用户=%s 输入=%s",
                    group_id,
                    user_id,
                    identifier,
                )
        elif result.state == LookupState.EXISTS:
            logger.info(
                "[入群审批] Minecraft 正版 ID 存在: 群=%s 用户=%s ID=%s",
                group_id,
                user_id,
                result.username,
            )
            self.store_pending_card(group_id, user_id, result.username)
            if self.auto_approve:
                await self.approve_request(
                    event,
                    flag=flag,
                    sub_type=sub_type,
                    log_context=f"group={group_id} user={user_id} username={result.username}",
                )
            else:
                logger.info(
                    "[入群审批] 自动同意已关闭，保留给管理员手动处理: 群=%s 用户=%s ID=%s",
                    group_id,
                    user_id,
                    result.username,
                )
        else:
            logger.warning(
                "[入群审批] Minecraft 正版 ID 查询失败，不做处理: 群=%s 用户=%s 输入=%s 状态码=%s 详情=%s",
                group_id,
                user_id,
                identifier,
                result.status_code,
                result.detail,
            )

        event.stop_event()

    @filter.custom_filter(GroupIncreaseNoticeFilter, priority=100)
    async def handle_group_increase_notice(self, event: AstrMessageEvent) -> None:
        """Set group card after a verified user joins the group."""
        if not self.enable_set_group_card:
            return

        raw_message = getattr(event.message_obj, "raw_message", None)
        group_id = str(_raw_get(raw_message, "group_id", "") or "")
        user_id = str(_raw_get(raw_message, "user_id", "") or "")

        if self.target_group_ids and group_id not in self.target_group_ids:
            return

        username = self.pop_pending_card(group_id, user_id)
        if username is None:
            return

        card = self.format_group_card(username, group_id, user_id)
        if self.card_delay_seconds > 0:
            await asyncio.sleep(self.card_delay_seconds)

        await self.set_group_card(
            event,
            group_id=group_id,
            user_id=user_id,
            card=card,
            log_context=f"group={group_id} user={user_id} username={username}",
        )

    def extract_identifier(self, comment: str) -> str | None:
        text = self.extract_answer(comment)
        if USERNAME_RE.fullmatch(text):
            return text
        if self.normalize_uuid(text):
            return text
        return None

    def extract_username(self, comment: str) -> str | None:
        return self.extract_identifier(comment)

    @staticmethod
    def extract_answer(comment: str) -> str:
        text = comment.strip()
        if not text:
            return ""

        for line in reversed(text.splitlines()):
            line = line.strip()
            for marker in ANSWER_MARKERS:
                if line.startswith(marker):
                    return line[len(marker) :].strip()

        return text

    async def lookup_identifier(self, identifier: str) -> LookupResult:
        result = await self.lookup_username(identifier)
        uuid = self.normalize_uuid(identifier)
        if uuid is None:
            return result
        if result.state not in {LookupState.NOT_FOUND, LookupState.ERROR}:
            return result

        uuid_result = await self.lookup_uuid(uuid)
        if uuid_result.state == LookupState.EXISTS:
            logger.info(
                "[入群审批] 已通过 UUID 回退查询到 Minecraft 正版 ID: UUID=%s ID=%s",
                uuid,
                uuid_result.username,
            )
        return uuid_result

    async def lookup_username(self, username: str) -> LookupResult:
        try:
            url = self.lookup_url_template.format(username=username)
        except Exception as exc:
            return LookupResult(
                state=LookupState.ERROR,
                username=username,
                detail=f"查询接口模板无效: {exc}",
            )

        try:
            async with httpx.AsyncClient(
                proxy=self.proxy,
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except Exception as exc:
            return LookupResult(
                state=LookupState.ERROR,
                username=username,
                detail=f"{type(exc).__name__}: {exc}",
            )

        if response.status_code == 200:
            return LookupResult(
                LookupState.EXISTS,
                self.profile_name_from_response(response) or username,
                response.status_code,
            )
        if response.status_code == 204:
            return LookupResult(LookupState.NOT_FOUND, username, response.status_code)
        if response.status_code in {400, 404}:
            return LookupResult(LookupState.NOT_FOUND, username, response.status_code)

        return LookupResult(
            state=LookupState.ERROR,
            username=username,
            status_code=response.status_code,
            detail=response.text[:200],
        )

    async def lookup_uuid(self, uuid: str) -> LookupResult:
        try:
            url = self.uuid_lookup_url_template.format(uuid=uuid)
        except Exception as exc:
            return LookupResult(
                state=LookupState.ERROR,
                username=uuid,
                detail=f"UUID 查询接口模板无效: {exc}",
            )

        try:
            async with httpx.AsyncClient(
                proxy=self.proxy,
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except Exception as exc:
            return LookupResult(
                state=LookupState.ERROR,
                username=uuid,
                detail=f"{type(exc).__name__}: {exc}",
            )

        if response.status_code == 200:
            username = self.profile_name_from_response(response)
            if username:
                return LookupResult(LookupState.EXISTS, username, response.status_code)
            return LookupResult(
                state=LookupState.ERROR,
                username=uuid,
                status_code=response.status_code,
                detail="UUID 查询响应缺少有效玩家名",
            )
        if response.status_code == 204:
            return LookupResult(LookupState.NOT_FOUND, uuid, response.status_code)
        if response.status_code in {400, 404}:
            return LookupResult(LookupState.NOT_FOUND, uuid, response.status_code)

        return LookupResult(
            state=LookupState.ERROR,
            username=uuid,
            status_code=response.status_code,
            detail=response.text[:200],
        )

    @staticmethod
    def normalize_uuid(identifier: str) -> str | None:
        uuid = identifier.replace("-", "")
        return uuid if UUID_RE.fullmatch(uuid) else None

    @staticmethod
    def profile_name_from_response(response: httpx.Response) -> str | None:
        try:
            data = response.json()
        except ValueError:
            return None

        if not isinstance(data, dict):
            return None

        username = data.get("name")
        if isinstance(username, str) and USERNAME_RE.fullmatch(username):
            return username
        return None

    def _load_pending_cards(self) -> None:
        if not self._pending_cards_path.exists():
            return
        try:
            raw_data = json.loads(self._pending_cards_path.read_text("utf-8"))
        except Exception as exc:
            logger.warning("[入群审批] 加载待改群名片记录失败: %s", exc)
            self.pending_cards = {}
            return

        if not isinstance(raw_data, dict):
            self.pending_cards = {}
            return

        self.pending_cards = {
            str(key): value
            for key, value in raw_data.items()
            if isinstance(value, dict) and value.get("username")
        }
        self.prune_expired_pending_cards(save=False)

    def _save_pending_cards(self) -> None:
        try:
            self._pending_cards_path.write_text(
                json.dumps(self.pending_cards, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception as exc:
            logger.warning("[入群审批] 保存待改群名片记录失败: %s", exc)

    def prune_expired_pending_cards(self, *, save: bool = True) -> None:
        if self.pending_card_ttl_hours <= 0:
            return

        now = time.time()
        ttl_seconds = self.pending_card_ttl_hours * 3600
        before_count = len(self.pending_cards)
        valid_cards = {}
        for key, value in self.pending_cards.items():
            try:
                created_at = float(value.get("created_at", 0))
            except (TypeError, ValueError):
                continue
            if now - created_at <= ttl_seconds:
                valid_cards[key] = value

        self.pending_cards = valid_cards
        if save and len(self.pending_cards) != before_count:
            self._save_pending_cards()

    @staticmethod
    def pending_card_key(group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def store_pending_card(self, group_id: str, user_id: str, username: str) -> None:
        if not self.enable_set_group_card:
            return
        if not group_id or not user_id:
            logger.warning(
                "[入群审批] 缺少群号或用户 ID，无法记录待改群名片: 群=%s 用户=%s ID=%s",
                group_id,
                user_id,
                username,
            )
            return

        self.prune_expired_pending_cards(save=False)
        key = self.pending_card_key(group_id, user_id)
        self.pending_cards[key] = {
            "group_id": group_id,
            "user_id": user_id,
            "username": username,
            "created_at": time.time(),
        }
        self._save_pending_cards()
        logger.info(
            "[入群审批] 已记录待改群名片: 群=%s 用户=%s 群名片=%s",
            group_id,
            user_id,
            username,
        )

    def pop_pending_card(self, group_id: str, user_id: str) -> str | None:
        self.prune_expired_pending_cards()
        key = self.pending_card_key(group_id, user_id)
        record = self.pending_cards.pop(key, None)
        if record is None:
            return None

        self._save_pending_cards()
        username = record.get("username")
        return str(username) if username else None

    def format_group_card(self, username: str, group_id: str, user_id: str) -> str:
        try:
            return self.group_card_template.format(
                username=username,
                group_id=group_id,
                user_id=user_id,
            )
        except Exception as exc:
            logger.warning("[入群审批] 群名片模板无效: %s", exc)
            return username

    async def approve_request(
        self,
        event: AstrMessageEvent,
        *,
        flag: str,
        sub_type: str,
        log_context: str,
    ) -> bool:
        if self.dry_run:
            logger.info("[入群审批] 试运行模式，模拟同意申请: %s", log_context)
            return True

        if not flag:
            logger.warning(
                "[入群审批] 缺少申请 flag，无法同意入群申请: %s", log_context
            )
            return False

        bot = getattr(event, "bot", None)
        if bot is None:
            logger.warning("[入群审批] 缺少 aiocqhttp bot 实例，无法同意入群申请")
            return False

        payload = {
            "flag": flag,
            "sub_type": sub_type or "add",
            "approve": True,
        }

        try:
            await self.call_onebot_action(event, "set_group_add_request", payload)
        except Exception as exc:
            logger.error(
                "[入群审批] 同意入群申请失败: %s 错误=%s",
                log_context,
                exc,
            )
            return False

        logger.info("[入群审批] 已同意入群申请: %s", log_context)
        return True

    async def reject_request(
        self,
        event: AstrMessageEvent,
        *,
        flag: str,
        sub_type: str,
        reason: str,
        log_context: str,
    ) -> bool:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.dry_run:
            logger.info(
                "[入群审批] 试运行模式，模拟拒绝申请: %s 理由=%s", log_context, reason
            )
            return True

        if not flag:
            logger.warning(
                "[入群审批] 缺少申请 flag，无法拒绝入群申请: %s", log_context
            )
            return False

        bot = getattr(event, "bot", None)
        if bot is None:
            logger.warning("[入群审批] 缺少 aiocqhttp bot 实例，无法拒绝入群申请")
            return False

        payload = {
            "flag": flag,
            "sub_type": sub_type or "add",
            "approve": False,
            "reason": reason,
        }

        try:
            await self.call_onebot_action(event, "set_group_add_request", payload)
        except Exception as exc:
            logger.error(
                "[入群审批] 拒绝入群申请失败: %s 错误=%s",
                log_context,
                exc,
            )
            return False

        logger.info("[入群审批] 已拒绝入群申请: %s 理由=%s", log_context, reason)
        return True

    async def set_group_card(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        user_id: str,
        card: str,
        log_context: str,
    ) -> bool:
        if self.dry_run:
            logger.info(
                "[入群审批] 试运行模式，模拟修改群名片: %s 群名片=%s",
                log_context,
                card,
            )
            return True

        payload = {
            "group_id": self.normalize_onebot_id(group_id),
            "user_id": self.normalize_onebot_id(user_id),
            "card": card,
        }
        try:
            await self.call_onebot_action(event, "set_group_card", payload)
        except Exception as exc:
            logger.error(
                "[入群审批] 修改群名片失败: %s 群名片=%s 错误=%s",
                log_context,
                card,
                exc,
            )
            return False

        logger.info(
            "[入群审批] 已修改群名片: %s 群名片=%s",
            log_context,
            card,
        )
        return True

    async def call_onebot_action(
        self,
        event: AstrMessageEvent,
        action: str,
        payload: dict[str, Any],
    ) -> Any:
        bot = getattr(event, "bot", None)
        if bot is None:
            raise RuntimeError("缺少 aiocqhttp bot 实例")

        call_action = getattr(bot, "call_action", None)
        if callable(call_action):
            async_call_action = cast(Callable[..., Awaitable[Any]], call_action)
            return await async_call_action(action, **payload)

        api = getattr(bot, "api", None)
        api_call_action = getattr(api, "call_action", None)
        if not callable(api_call_action):
            raise RuntimeError("aiocqhttp bot 未提供 call_action 方法")

        async_api_call_action = cast(Callable[..., Awaitable[Any]], api_call_action)
        return await async_api_call_action(action, **payload)

    @staticmethod
    def normalize_onebot_id(value: str) -> int | str:
        return int(value) if value.isdigit() else value

    async def terminate(self) -> None:
        logger.info("[入群审批] 插件已停用")

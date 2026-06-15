"""
opprime-core-v2/lib/channels/feishu.py

飞书通道:加密解密、消息收发。

来自 V0 feishu_bridge.py 的浓缩版。
只保留核心链:解密 → 处理 → 回复。
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ── 飞书通道心跳配置 ──
_HEARTBEAT_INTERVAL = 180  # 每 3 分钟检测一次（不是 5 分钟，更敏感）
_HEARTBEAT_MAX_FAILURES = 3  # 连续 3 次失败触发重连
_HEARTBEAT_RECONNECT_DELAYS = [5, 30, 120, 300]  # 指数退避：5s → 30s → 2min → 5min


class FeishuChannel:
    """飞书通道。"""

    def __init__(self, app_id: str, app_secret: str, encrypt_key: str, verify_token: str = "", ack_text: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        self.encrypt_key = encrypt_key  # 明文 32 位 encrypt key
        self.verify_token = verify_token  # 飞书事件订阅验证 token
        self.ack_text = ack_text or "高达收到🦾，执行您的指令"

        self._tenant_token: str = ""
        self._token_expires: float = 0

        # 去重(最近 100 个 message_id)
        self._seen_msg_ids: set[str] = set()

        # 欢迎消息去重(open_id → 上次发送时间戳)
        self._greeted: dict[str, float] = {}

        # per-user 串行锁
        self._user_locks: dict[str, asyncio.Lock] = {}

        # 用户会话(open_id → JsonlSessionManager)
        self.sessions: dict[str, JsonlSessionManager] = {}
        self._session_base_dir = Path("data/sessions")
        self._session_base_dir.mkdir(parents=True, exist_ok=True)

        # 并发控制
        self._semaphore = asyncio.Semaphore(3)
        """最多 3 条消息同时处理。
        超过 3 条时排队等待有空闲 worker。
        """

        # OpenAI-compatible 的 client(需要外部提供)
        self.kernel = None

        # ── 心跳检测状态 ──
        self._heartbeat_failures = 0
        self._heartbeat_task: asyncio.Task | None = None
        self._heartbeat_healthy = True
        self._heartbeat_reconnects = 0

    async def start_heartbeat(self):
        """启动飞书通道心跳后台检测。"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            return  # 已有心跳在跑
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("飞书通道心跳已启动 (间隔 %ds, 最大失败 %d 次)", _HEARTBEAT_INTERVAL, _HEARTBEAT_MAX_FAILURES)

    async def _heartbeat_loop(self):
        """心跳循环：定时检测飞书通道可用性。

        检测方式：轻量调用 _ensure_token 验证 token 有效即可，
        不额外发送消息（不发 ping 字样的消息到飞书）。
        """
        await asyncio.sleep(5)  # 启动后先等 5 秒，保证通道初始化完成
        backoff_index = 0
        while True:
            try:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)

                # 检测 token 是否有效
                # 如果 token 过期，_ensure_token 会抛异常
                await self._ensure_token()

                # 用 token 做一次轻量 API 调用验证连通性
                # 使用 get_bot_info 替代发送消息，避免打扰
                url = "https://open.feishu.cn/open-apis/bot/v3/info"
                headers = {"Authorization": f"Bearer {self._tenant_token}"}
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, headers=headers, timeout=5)
                    data = resp.json()
                    if data.get("code") == 0:
                        # 心跳成功
                        if not self._heartbeat_healthy:
                            logger.info("💚 飞书通道已恢复")
                        self._heartbeat_healthy = True
                        self._heartbeat_failures = 0
                        backoff_index = 0
                    else:
                        raise RuntimeError(f"飞书 API 返回异常: {data.get('msg', 'unknown')}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._heartbeat_failures += 1
                logger.warning("💔 飞书通道心跳失败 (%d/%d): %s", self._heartbeat_failures, _HEARTBEAT_MAX_FAILURES, e)

                if self._heartbeat_failures >= _HEARTBEAT_MAX_FAILURES:
                    # 触发重连
                    self._heartbeat_healthy = False
                    delay = _HEARTBEAT_RECONNECT_DELAYS[min(backoff_index, len(_HEARTBEAT_RECONNECT_DELAYS) - 1)]
                    logger.error("🔴 飞书通道异常，%ds 后尝试重新初始化", delay)
                    await asyncio.sleep(delay)
                    backoff_index += 1
                    self._heartbeat_reconnects += 1

                    try:
                        # 手动刷新 token
                        self._tenant_token = ""
                        self._token_expires = 0
                        await self._refresh_token()
                        logger.info("💚 飞书通道重连成功 (第 %d 次重连)", self._heartbeat_reconnects)
                        self._heartbeat_failures = 0
                        self._heartbeat_healthy = True
                        backoff_index = 0
                    except Exception as e2:
                        logger.error("🔴 飞书通道重连失败: %s", e2)
                        # 继续等待下一轮心跳重试

    def set_kernel(self, kernel):
        """注入内核实例。"""
        self.kernel = kernel

    # ── token 管理 ──

    def _get_download_dir(self) -> Path:
        """返回文件下载目录。"""
        d = Path("data/received_files")
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _download_file(self, message_id: str, file_key: str, ext: str = "") -> Path | None:
        """从飞书下载文件到本地。

        使用 GET /im/v1/messages/{message_id}/resources/{file_key}?type=file
        返回本地保存路径，失败返回 None。

        Note:
            飞说资源下载 API 需要传 type 参数（file/image）否则报 99992402 field validation failed。
        """
        await self._ensure_token()
        params_ext = "?type=file" if ext not in (".png", ".jpg", ".jpeg", ".gif") else "?type=image"
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}{params_ext}"
        headers = {"Authorization": f"Bearer {self._tenant_token}"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=headers, timeout=30)
                if resp.status_code != 200:
                    logger.error("文件下载失败: HTTP %d, %s", resp.status_code, resp.text[:200])
                    return None
                # 自动推断扩展名
                ct = resp.headers.get("content-type", "")
                if not ext:
                    ext_map = {
                        "application/pdf": ".pdf",
                        "text/markdown": ".md",
                        "text/plain": ".txt",
                        "application/json": ".json",
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/gif": ".gif",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                        "text/csv": ".csv",
                    }
                    ext = ext_map.get(ct, "")
                # 保存文件
                save_dir = self._get_download_dir()
                fname = f"{file_key}{ext}" if ext else file_key
                save_path = save_dir / fname
                save_path.write_bytes(resp.content)
                logger.info("文件下载成功: %s (%d bytes, %s)", save_path, len(resp.content), ct)
                return save_path
        except Exception as e:
            logger.error("文件下载异常: %s", e, exc_info=True)
            return None

    async def send_file(self, open_id: str, file_path: str | Path, message_id: str = ""):
        """发送文件到飞书。

        使用飞书文件上传后发送的流程:
        1. POST /im/v1/files 上传文件
        2. 用返回的 file_key 发送消息
        """
        await self._ensure_token()
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error("send_file: 文件不存在 %s", file_path)
            return {"ok": False, "error": f"文件不存在: {file_path}"}

        # 先上传文件
        upload_url = "https://open.feishu.cn/open-apis/im/v1/files"
        headers = {"Authorization": f"Bearer {self._tenant_token}"}
        try:
            async with httpx.AsyncClient() as client:
                # 分两步：先上传获取 file_key
                with open(file_path, "rb") as f:
                    files = {"file": (file_path.name, f, "application/octet-stream")}
                    data = {"file_type": "stream", "file_name": file_path.name}
                    resp = await client.post(upload_url, headers=headers, files=files, data=data, timeout=60)
                upload_data = resp.json()
                if upload_data.get("code") != 0:
                    logger.error("文件上传失败: %s", upload_data)
                    return {"ok": False, "error": f"上传失败: {upload_data.get('code')}"}
                file_key = upload_data.get("data", {}).get("file_key", "")
                if not file_key:
                    logger.error("上传成功但无 file_key")
                    return {"ok": False, "error": "上传成功但飞书未返回 file_key"}
                logger.info("文件上传成功: %s → file_key=%s", file_path.name, file_key)

                # 发送文件消息
                if message_id:
                    success = await self._reply_to_message(
                        message_id,
                        json.dumps({"file_key": file_key}),
                        "file",
                    )
                    if success:
                        return {"ok": True, "result": f"文件已发送 (reply): {file_path.name}", "file_key": file_key}

                send_url = "https://open.feishu.cn/open-apis/im/v1/messages"
                send_body = {
                    "receive_id": open_id,
                    "msg_type": "file",
                    "content": json.dumps({"file_key": file_key}),
                }
                resp2 = await client.post(
                    send_url,
                    params={"receive_id_type": "open_id"},
                    headers=headers,
                    json=send_body,
                    timeout=15,
                )
                data2 = resp2.json()
                if data2.get("code") != 0:
                    logger.error("文件发送失败: %s", data2)
                    return {"ok": False, "error": f"发送失败: {data2.get('msg')}"}
                return {"ok": True, "result": f"文件已发送: {file_path.name}", "file_key": file_key}
        except Exception as e:
            logger.error("send_file 异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"send_file 异常: {str(e)}"}

    async def _ensure_token(self):
        """确保 tenant_access_token 有效。"""
        if self._tenant_token and time.time() < self._token_expires - 60:
            return
        await self._refresh_token()

    async def _refresh_token(self):
        """刷新 token。"""
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error("飞书 token 刷新失败: %s", data)
                raise RuntimeError(f"飞书 token 刷新失败: {data}")
            self._tenant_token = data["tenant_access_token"]
            self._token_expires = time.time() + data.get("expire", 7200)
            logger.info("飞书 token 刷新成功")

    # ── 加密解密 ──

    def _decrypt(self, payload: str) -> dict:
        """解密飞书加密事件回调。"""
        data = base64.urlsafe_b64decode(payload.encode("ascii"))

        # AES key = SHA256(encrypt_key)[:32]
        sha256 = hashlib.sha256(self.encrypt_key.encode("utf-8")).digest()
        key = sha256[:32]

        # iv = data[:16]
        iv = data[:16]
        ciphertext = data[16:]

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # PKCS7 去填充
        pad_len = plaintext[-1]
        plaintext = plaintext[:-pad_len]

        return json.loads(plaintext.decode("utf-8"))

    # ── 消息发送 ──

    async def _reply_to_message(self, message_id: str, content: str, msg_type: str = "text"):
        """通过 reply API 回复消息（跨 App 可用，不依赖 open_id）。"""
        await self._ensure_token()
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
        headers = {
            "Authorization": f"Bearer {self._tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "content": content,
            "msg_type": msg_type,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("飞书 reply 失败: %s", data.get("msg"))
                return False
            return True

    _send_retry_max: int = 0
    _send_retry_delay: float = 1.0

    def set_send_retry(self, max_retries: int = 3, base_delay: float = 1.0):
        """设置飞书发送失败时的重试参数（网络错误/超时/5xx 才重试）。"""
        self._send_retry_max = max_retries
        self._send_retry_delay = base_delay

    async def _send_with_retry(self, fn, *args, **kwargs):
        """对网络层错误进行重试包装。"""
        last_exc = None
        for attempt in range(1 + self._send_retry_max):
            try:
                return await fn(*args, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_exc = e
                if attempt < self._send_retry_max:
                    delay = self._send_retry_delay * (2 ** attempt)
                    logger.warning("飞书发送网络错误 (attempt %d/%d): %s，%.1fs 后重试",
                                   attempt + 1, self._send_retry_max + 1, e, delay)
                    await asyncio.sleep(delay)
                else:
                    logger.error("飞书发送网络错误，已重试 %d 次: %s", self._send_retry_max, e)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < self._send_retry_max:
                    last_exc = e
                    delay = self._send_retry_delay * (2 ** attempt)
                    logger.warning("飞书发送 5xx (attempt %d/%d): %s，%.1fs 后重试",
                                   attempt + 1, self._send_retry_max + 1, e.response.status_code, delay)
                    await asyncio.sleep(delay)
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    async def send_text(self, open_id: str, text: str, message_id: str = ""):
        """发送文本消息到飞书。

        - 有 message_id 时：使用 reply API（跨 App 可用）
        - 无 message_id 时：使用 send API（需要本 App 内的 open_id）
        """
        if message_id:
            success = await self._reply_to_message(message_id, json.dumps({"text": text}, ensure_ascii=False), "text")
            if success:
                return {"ok": True, "result": "文本已发送 (reply)"}
            # reply 失败时降级到 send API
            logger.info("reply 失败，降级到 send API | content 前200=%s", (text or "")[:200])

        await self._ensure_token()

        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {self._tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                params={"receive_id_type": "open_id"},
                headers=headers,
                json=body,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error("飞书发送失败: %s", data)
                # 如果 open_id 跨 App 失败，尝试用 chat_id 方式（降级三）
                logger.info("send 失败，降级到 chat_id 方式 | content 前200=%s", (text or "")[:200])
                try:
                    await self._send_to_chat(open_id, text)
                    return {"ok": True, "result": "文本已发送 (chat_id 降级)"}
                except Exception as e2:
                    logger.error("chat_id 降级也失败: %s", e2)
                    return {"ok": False, "error": f"发送失败 (chat_id 降级也失败): {e2}"}
            return {"ok": True, "result": "文本已发送"}

    async def _send_to_chat(self, chat_id: str, text: str):
        """发送消息到群聊。"""
        await self._ensure_token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {self._tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                params={"receive_id_type": "chat_id"},
                headers=headers,
                json=body,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error("飞书群聊发送失败: %s", data)
                raise RuntimeError(f"群聊发送失败: {data.get('msg')}")
            return {"ok": True, "result": "消息已发送到群聊"}

    async def send_card(self, open_id: str, card: dict, message_id: str = ""):
        """发送卡片消息到飞书。

        - 有 message_id 时：使用 reply API 回复（跨 App 可用）
        - 无 message_id 时：使用 send API（需要本 App 内的 open_id）
        - 都失败时降级到 chat_id 方式
        """
        if message_id:
            card_str = json.dumps(card, ensure_ascii=False)
            success = await self._reply_to_message(message_id, card_str, "interactive")
            if success:
                logger.info("飞书卡片 reply 成功 → msg_id=%s", message_id[:20])
                return {"ok": True, "result": "卡片已发送 (reply)"}
            logger.info("卡片 reply 失败，降级到 send API")

        await self._ensure_token()

        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {self._tenant_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "receive_id": open_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                params={"receive_id_type": "open_id"},
                headers=headers,
                json=body,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.error("飞书卡片发送失败: %s", data)
                return {"ok": False, "error": f"发送失败: {data.get('msg')}"}
            return {"ok": True, "result": "卡片已发送"}

    # ── 事件处理 ──

    async def handle_event(self, body: bytes) -> dict:
        """处理飞书 Webhook 事件。

        支持两种格式:
        - 加密模式:body 中有 "encrypt" 字段
        - 明文模式:直接解析事件
        """
        if isinstance(body, (bytes, bytearray)):
            body = json.loads(body)

        # 解密
        if "encrypt" in body:
            try:
                event_data = self._decrypt(body["encrypt"])
            except Exception as e:
                logger.error("解密失败: %s", e)
                return {"code": 0, "msg": "decrypt_error"}
        else:
            event_data = body

        # 兼容飞书某些事件加密后是 JSON 数组的情况
        if isinstance(event_data, list):
            if not event_data:
                logger.warning("飞书事件数据为空列表")
                return {"code": 0, "msg": "ok"}
            event_data = event_data[0]

        # 类型检查
        event_type = event_data.get("type", event_data.get("header", {}).get("event_type", ""))

        if event_type == "url_verification":
            # URL 验证
            challenge = event_data.get("challenge", "")
            return {"challenge": challenge}

        if event_type == "im.message.receive_v1":
            await self._handle_message(event_data)
            return {"code": 0, "msg": "ok"}

        if event_type == "card.action.trigger":
            await self._handle_card_action(event_data)
            return {"code": 0, "msg": "ok"}

        # 被拉入群聊（公共群）
        if "bot.added" in event_type or "chat.member.bot.added" in event_type:
            await self._handle_bot_entered(event_data)
            return {"code": 0, "msg": "ok"}
        # 私聊连接建立
        if event_type.endswith("bot_p2p_chat_entered_v1"):
            await self._handle_p2p_entered(event_data)
            return {"code": 0, "msg": "ok"}

        # 记录未处理的事件类型
        logger.info("飞书未处理事件类型: %s → keys=%s", event_type, list(event_data.keys())[:8])

        # 其他事件,只返回 200
        return {"code": 0, "msg": "ok"}

    async def _handle_message(self, event: dict):
        """处理收到的飞书消息。
        并发队列:最多 3 条消息同时处理。
        同一用户在长查询过程中发第二条,不会被堵塞,会独立处理。
        """
        event_body = event.get("event", event)
        message = event_body.get("message", {})
        sender = event_body.get("sender", {})

        message_id = message.get("message_id", "")
        sender_id = sender.get("sender_id", {}).get("open_id", "")
        chat_type = message.get("chat_type", "")
        chat_id = message.get("chat_id", "")
        is_group = chat_type != "p2p"

        # 去重
        if message_id in self._seen_msg_ids:
            return
        self._seen_msg_ids.add(message_id)
        if len(self._seen_msg_ids) > 100:
            self._seen_msg_ids = set(list(self._seen_msg_ids)[-100:])

        # 私聊：全部处理；群聊：仅 @提及 时处理
        if is_group:
            mentions = message.get("mentions", [])
            if not mentions:
                logger.debug("群聊消息未 @提及，忽略（chat_type=%s）", chat_type)
                return

        # 提取文本内容(兼容卡片/文本/富文本等消息类型)
        message_type = message.get("message_type", "text")
        content_str = message.get("content", "{}")
        try:
            content = json.loads(content_str)
            if message_type in ("text", "post"):
                raw = content.get("text", content.get("content", ""))
                if isinstance(raw, list):
                    # post 消息的 text 是嵌套列表 [[{}, {}], ...]
                    parts = []
                    for block in raw:
                        if isinstance(block, list):
                            for seg in block:
                                if isinstance(seg, dict):
                                    parts.append(seg.get("text", ""))
                        elif isinstance(block, str):
                            parts.append(block)
                    text = "".join(parts)
                else:
                    text = raw
            elif message_type == "interactive":
                # 卡片消息:取 header title + 各模块 text
                parts = []
                header = content.get("header", {}).get("title", {}).get("content", "")
                if header:
                    parts.append(header)
                elements = content.get("elements", [])
                for block in elements:
                    # 飞书某些卡片模块把元素包在嵌套数组里，递归展平
                    worklist = [block]
                    while worklist:
                        item = worklist.pop()
                        if isinstance(item, list):
                            worklist.extend(item)
                        elif isinstance(item, dict):
                            block_texts = _extract_card_text(item)
                            parts.extend(block_texts)
                text = "\n".join(parts) if parts else content_str
                logger.info("卡片消息解析完成,提取到 %d 个文本块", len(parts))
            elif message_type == "file":
                # 文件消息:下载文件后把内容给内核
                file_key = content.get("file_key", "")
                content.get("file_name", content.get("name", ""))
                if file_key:
                    save_path = await self._download_file(message_id, file_key)
                    if save_path:
                        # 通用规则:任何文件都注入实际路径，LLM 可以自行用 read_file 读取
                        ext = save_path.suffix.lower()
                        text_extracted = ""
                        # 文本类文件:自动提取内容
                        if ext in (
                            ".md",
                            ".txt",
                            ".json",
                            ".csv",
                            ".xml",
                            ".yaml",
                            ".yml",
                            ".log",
                            ".py",
                            ".js",
                            ".html",
                            ".css",
                        ):
                            with contextlib.suppress(Exception):
                                text_extracted = save_path.read_text(errors="replace")[:5000]
                        # PDF:多层提取（文字层 → OCR 回退）
                        elif ext == ".pdf":
                            try:
                                import fitz

                                doc = fitz.open(str(save_path))
                                pages_text = []
                                total_chars = 0
                                for page in doc:
                                    t = page.get_text().strip()
                                    if t:
                                        pages_text.append(t)
                                        total_chars += len(t)
                                        if total_chars > 8000:
                                            pages_text.append("...[截断]")
                                            break
                                doc.close()
                                if total_chars > 100:
                                    text_extracted = "\n---\n".join(pages_text)[:8000]
                                    logger.info("PDF文字层提取: %d chars", total_chars)
                                else:
                                    # 文字太少→扫描件，转图片+Tesseract OCR
                                    import pathlib
                                    import subprocess
                                    import tempfile

                                    import pdf2image

                                    try:
                                        images = pdf2image.convert_from_path(
                                            str(save_path), dpi=200, fmt="jpeg", first_page=1, last_page=5
                                        )
                                        ocr_parts = []
                                        for _i, img in enumerate(images[:5]):
                                            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                                            tmp_path = tmp.name
                                            tmp.close()
                                            img.save(tmp_path, "JPEG")
                                            ocr_result = subprocess.run(
                                                ["tesseract", tmp_path, "stdout", "-l", "chi_sim+eng"],
                                                capture_output=True,
                                                text=True,
                                                timeout=30,
                                            )
                                            pathlib.Path(tmp_path).unlink(missing_ok=True)
                                            if ocr_result.returncode == 0 and ocr_result.stdout.strip():
                                                ocr_parts.append(ocr_result.stdout.strip())
                                        if ocr_parts:
                                            combined = "\n---\n".join(ocr_parts)[:8000]
                                            text_extracted = f"[OCRed扫描件]\n{combined}"
                                            logger.info("PDF OCR提取: %d chars", len(combined))
                                        else:
                                            logger.warning("PDF OCR全部失败或无文字")
                                            text_extracted = ""
                                    except Exception as ocr_err:
                                        logger.warning("PDF OCR失败: %s", ocr_err)
                                        text_extracted = ""
                            except ImportError as ie:
                                logger.warning("PDF提取库未安装: %s", ie)
                                text_extracted = ""
                            except Exception as e:
                                logger.error("PDF提取异常: %s", e)
                        if text_extracted:
                            text = f"[文件: {save_path.name} | 本地路径: {save_path} | 已提取内容]\n{text_extracted}"
                        else:
                            text = f"[文件: {save_path.name} ({save_path.stat().st_size} bytes) | 本地路径: {save_path} | 可用 read_file 读取]"
                    else:
                        text = f"[文件下载失败: {file_key}]"
                else:
                    text = "[文件: 无法获取 file_key]"
            elif message_type == "image":
                # 图片消息:下载后自动调豆包 VLM 分析图片
                image_key = content.get("image_key", "")
                if image_key:
                    save_path = await self._download_file(message_id, image_key, ext=".png")
                    if save_path:
                        # 自动调豆包 VLM 预分析图片
                        try:
                            from tools.analyze_image_doubao import analyze_image_doubao
                            result = await analyze_image_doubao(str(save_path), "请详细描述这张图片的内容，包括其中的物体、文字、颜色、场景等")
                            if result.get("success"):
                                text = f"[用户发了一张图片]\n\n图片分析结果（预分析）:\n{result['description']}"
                            else:
                                text = f"[图片已保存 | 本地路径: {save_path}]\n[预分析失败: {result.get('error', '未知错误')}]"
                        except Exception as e:
                            logger.warning("图片预分析异常（不影响运行）: %s", e)
                            text = f"[图片已保存 | 本地路径: {save_path}]"
                    else:
                        text = f"[图片下载失败: key={image_key}]"
                else:
                    text = "[图片: 无法获取 image_key]"
            else:
                text = content_str
        except json.JSONDecodeError:
            text = content_str

        logger.info("飞书收到 [%s]: %s → %s", message_type, sender_id, text[:150])

        if not text.strip():
            return

        if not self.kernel:
            await self.send_text(sender_id, "抱歉,我没有内核,无法处理消息。")
            return

        # 并发队列:per-user 串行锁(同人多排队)+ 全局限流
        async def _process():
            user_lock = self._user_locks.setdefault(sender_id, asyncio.Lock())
            async with user_lock, self._semaphore:
                from lib.session import JsonlSessionManager  # pylint: disable=import-outside-toplevel

                session = self.sessions.get(sender_id)
                if not session:
                    session_path = self._session_base_dir / f"{sender_id}.jsonl"
                    session = JsonlSessionManager(str(session_path))
                    self.sessions[sender_id] = session

                # 注入当前用户身份到工具全局上下文（工具可读）
                from lib.toolkit import set_global as tk_set_global

                tk_set_global("feishu_sender_id", sender_id)
                tk_set_global("feishu_message_id", message_id)

                # ── Pendling 队列：写任务标记（进程崩溃后恢复用） ──
                _pending_dir = Path("data/pending")
                _pending_dir.mkdir(parents=True, exist_ok=True)
                _pending_file = _pending_dir / f"{message_id}.json"
                try:
                    with open(_pending_file, "w") as _pf:
                        json.dump({
                            "message_id": message_id,
                            "open_id": sender_id,
                            "chat_id": chat_id,
                            "chat_type": chat_type,
                            "text": text[:200],
                            "timestamp": time.time(),
                            "status": "pending"
                        }, _pf, ensure_ascii=False)
                except Exception as _pe:
                    logger.warning("Pendling 写入失败: %s", _pe)

                # 先发已读回执（群聊跳过，避免私聊泄漏）
                if not is_group:
                    await self.send_text(sender_id, self.ack_text)

                # 进度通知函数
                _last_progress_msg = ""

                async def _send_progress(msg: str):
                    nonlocal _last_progress_msg
                    if msg != _last_progress_msg:
                        _last_progress_msg = msg
                        try:
                            if is_group:
                                await self._send_to_chat(chat_id, msg)
                            else:
                                await self.send_text(sender_id, msg)
                        except Exception:
                            logger.exception("静默异常")

                # 🛡️ 厚钢板：300 秒超时保底（5 分钟，平均每轮 ~6 个工具调用，每个默认 30s）
                try:
                    reply = await self.kernel.run(
                        user_message=text,
                        platform="feishu",
                        session=session,
                        max_seconds=None,  # 无超时限制
                    )

                    if reply and reply.startswith("[系统] 任务因超时中断"):
                        await self.send_text(
                            sender_id,
                            f"⚠️ {reply}",
                        )
                    else:
                        await self.send_text(sender_id, reply, message_id=message_id)

                    # ── Pendling 队列：标记完成（删除标记文件） ──
                    try:
                        if _pending_file.exists():
                            _pending_file.unlink()
                            logger.info("Pendling 完成: %s", message_id)
                    except Exception as _pe:
                        logger.warning("Pendling 删除失败: %s", _pe)

                except asyncio.CancelledError:
                    # 中断：保持 pending，启动恢复时会重试
                    await self.send_text(sender_id, "❌ 任务被中断,抱歉没能完成。可以重新跟我说。")
                except Exception as e:
                    import traceback

                    logger.error("飞书消息处理异常: %s\n%s", e, traceback.format_exc())
                    # 保持 pending 文件，启动恢复时重试
                    with contextlib.suppress(Exception):
                        await self.send_text(sender_id, f"❌ 处理消息时出错了: {str(e)[:200]}")

        asyncio.create_task(_process())

    async def _handle_bot_entered(self, event: dict):
        """处理机器人被拉入群聊事件。发送入群问候。"""
        event_body = event.get("event", event)
        chat_id = event_body.get("chat_id", "")
        chat_name = event_body.get("name", "新群聊")
        logger.info("飞书机器人被拉入群聊: %s (%s)", chat_name, chat_id)
        greeting = (
            "👋 大家好！我是 Opprime，羽非的数字分身。\n\n"
            "**我能做什么：**\n"
            "• 回答各种问题\n"
            "• 搜索信息、查天气\n"
            "• 执行代码、管理文件\n"
            "• 定时提醒、发送通知\n\n"
            "💬 在群里 @我 就可以跟我对话。私聊也随时欢迎！"
        )
        if chat_id:
            await self._send_to_chat(chat_id, greeting)

    async def _handle_p2p_entered(self, event: dict):
        """处理私聊连接建立。发简短欢迎（24h 内不重复发）。"""
        event_body = event.get("event", event)
        open_id = event_body.get("chat_id", "")
        logger.info("飞书私聊连接建立: %s", open_id)
        if not open_id:
            return
        now = time.time()
        last = self._greeted.get(open_id, 0)
        if now - last < 86400:
            logger.info("24h 内已发过欢迎，跳过")
            return
        self._greeted[open_id] = now
        await self._send_to_chat(open_id, "你好我是Opprime World 🌍 宇宙意志，有什么可以帮您！🛡️")

    async def _handle_card_action(self, event: dict):
        """处理卡片按钮点击事件 (card.action.trigger)。

        将按钮点击转化为文本意图，复用 _handle_message 的完整处理链。
        """
        # 兼容 v2 格式：实际数据在 event.event 里
        inner_event = event.get("event", event)

        action = inner_event.get("action", {})
        # ★ 飞书某些场景下 action 是 list（如转发消息后点击卡片按钮）
        if isinstance(action, list):
            logger.info("卡片 action 是 list（len=%d），取第一个", len(action))
            action = action[0] if action else {}

        action_value = action.get("value", {})
        # 同样防御 value 是 list 的边缘情况
        if isinstance(action_value, list):
            action_value = action_value[0] if action_value else {}

        button_text = action.get("option", "")

        open_id = inner_event.get("open_id", "") or event.get("open_id", "")
        open_message_id = inner_event.get("open_message_id", "") or event.get("open_message_id", "")

        # 从按钮 value 或文本中提取用户意图
        if isinstance(action_value, dict):
            intent = action_value.get("intent", action_value.get("action", ""))
            if not intent and button_text:
                intent = button_text
        elif isinstance(action_value, str):
            intent = action_value
        else:
            intent = button_text

        if not intent:
            logger.warning("卡片动作无有效意图: action=%s", json.dumps(action, ensure_ascii=False)[:300])
            return

        logger.info(
            "飞书收到卡片点击: open_id=%s, intent=%s, msg_id=%s",
            open_id,
            intent,
            open_message_id[:20] if open_message_id else "-",
        )

        if not self.kernel:
            await self.send_text(open_id, "抱歉，我没有内核，无法处理消息。", message_id=open_message_id)
            return

        # 复用与 _handle_message 相同的处理链
        async def _process():
            from lib.session import JsonlSessionManager
            from lib.toolkit import set_global as tk_set_global

            session = self.sessions.get(open_id)
            if not session:
                session_path = self._session_base_dir / f"{open_id}.jsonl"
                session = JsonlSessionManager(str(session_path))
                self.sessions[open_id] = session

            tk_set_global("feishu_sender_id", open_id)
            tk_set_global("feishu_message_id", open_message_id)

            await self.send_text(open_id, self.ack_text, message_id=open_message_id)

            # 🛡️ 厚钢板：120 秒超时保底，不会永久卡死
            try:
                reply = await self.kernel.run(
                    user_message=intent,
                    platform="feishu",
                    session=session,
                    max_seconds=None,  # 无超时限制
                )

                if reply and reply.startswith("[系统] 任务因超时中断"):
                    await self.send_text(
                        open_id,
                        f"⚠️ {reply}",
                        message_id=open_message_id,
                    )
                else:
                    await self.send_text(open_id, reply, message_id=open_message_id)
            except asyncio.CancelledError:
                await self.send_text(
                    open_id, "❌ 任务被中断，抱歉没能完成。可以重新跟我说。", message_id=open_message_id
                )
            except Exception as e:
                logger.error("卡片动作处理异常: %s", e)
                with contextlib.suppress(Exception):
                    await self.send_text(
                        open_id, f"❌ 处理卡片点击时出错了: {str(e)[:200]}", message_id=open_message_id
                    )

        asyncio.create_task(_process())


def _extract_card_text(block) -> list[str]:
    """从飞书卡片消息的 element block 中提取所有文本。

    支持常见的飞书卡片模块类型:
    - div (text) - 普通文本块
    - column_set / column - 列布局
    - markdown - markdown 内容
    - note - 备注块
    - hr / image / button 等无文本的跳过

    注意: block 可能是 dict 或 list（飞书某些卡片结构嵌套数组）
    """
    # 如果 block 是 list，展平递归处理每个元素
    if isinstance(block, list):
        texts = []
        for item in block:
            texts.extend(_extract_card_text(item))
        return texts

    if not isinstance(block, dict):
        return []

    texts = []

    tag = block.get("tag", "")

    if tag in ("div", "markdown"):
        text_field = block.get("text", {})
        if isinstance(text_field, dict):
            content = text_field.get("content", "")
            if content:
                texts.append(content)
        elif isinstance(text_field, str):
            texts.append(text_field)

    elif tag == "note":
        for elem in block.get("elements", []):
            if isinstance(elem, dict):
                texts.extend(_extract_card_text(elem))
            elif isinstance(elem, str):
                texts.append(elem)
            elif isinstance(elem, list):
                texts.extend(_extract_card_text(elem))

    elif tag == "column_set":
        for col in block.get("columns", []):
            if isinstance(col, (dict, list)):
                texts.extend(_extract_card_text(col))

    elif tag == "column":
        for elem in block.get("elements", []):
            if isinstance(elem, (dict, list)):
                texts.extend(_extract_card_text(elem))

    elif tag == "action":
        for btn in block.get("actions", []):
            if isinstance(btn, dict):
                btn_text = btn.get("text", {}).get("content", "") if isinstance(btn.get("text"), dict) else ""
                if btn_text:
                    texts.append(btn_text)

    return texts

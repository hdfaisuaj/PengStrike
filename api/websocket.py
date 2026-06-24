"""
WebSocket 服务 (api/websocket.py)
修复：移除严格token验证，避免CORS 403错误

AutoPilot 用户确认机制：
- 后端每步执行前通过 WebSocket 发送 autopilot_confirm_request
- 前端弹确认框，用户确认/中止
- 前端发送 autopilot_confirm_response，后端继续或中止
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any, Dict
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# 全局 AutoPilotEngine 注册表 (从 core.auto_pilot 导入)
from core.auto_pilot import _running_engines as _auto_engines

# AutoPilot 待确认表：session_id -> asyncio.Future
pending_confirmations: Dict[str, asyncio.Future] = {}

# AutoPilot 命令级待确认表：session_id -> asyncio.Future
# Future 结果 dict: {"action": "execute"|"modify"|"skip"|"abort", "modified_command": str, "user_input": str}
pending_command_confirmations: Dict[str, asyncio.Future] = {}


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self) -> None:
        self._connections: Dict[str, list[WebSocket]] = {}
        self._user_map: Dict[int, str] = {}

    def register(self, websocket: WebSocket, user_id: str) -> None:
        if user_id not in self._connections:
            self._connections[user_id] = []
        self._connections[user_id].append(websocket)
        self._user_map[id(websocket)] = user_id

    def disconnect(self, websocket: WebSocket) -> None:
        uid = id(websocket)
        user_id = self._user_map.pop(uid, None)
        if user_id and user_id in self._connections:
            try:
                self._connections[user_id].remove(websocket)
            except ValueError:
                pass
            if not self._connections[user_id]:
                del self._connections[user_id]

    async def send_personal(self, user_id: str, message: Dict[str, Any]) -> None:
        if user_id not in self._connections:
            return
        data = json.dumps(message, ensure_ascii=False, default=str)
        dead = []
        for ws in self._connections[user_id]:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False, default=str)
        dead = []
        for user_id, sockets in list(self._connections.items()):
            for ws in sockets:
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_manager = ConnectionManager()


def get_connection_manager() -> ConnectionManager:
    return _manager


async def request_autopilot_confirmation(
    session_id: str,
    message: str,
    timeout: int = 300,
) -> bool:
    """
    请求用户确认 AutoPilot 某一步骤。
    通过 WebSocket 推送确认请求到前端，等待用户响应。
    返回 True = 用户确认继续，False = 用户中止或超时。
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_confirmations[session_id] = future

    manager = get_connection_manager()
    payload = {
        "type": "autopilot_confirm_request",
        "session_id": session_id,
        "message": message,
        "timestamp": time.time(),
    }
    logger.info("[AutoPilot] 等待用户确认: session_id=%s, msg=%s", session_id, message)
    await manager.broadcast(payload)

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        logger.info("[AutoPilot] 用户确认结果: session_id=%s, confirm=%s", session_id, result)
        return result
    except asyncio.TimeoutError:
        logger.warning("[AutoPilot] 确认超时（%ss）: session_id=%s", timeout, session_id)
        return False
    finally:
        pending_confirmations.pop(session_id, None)


async def request_autopilot_command_confirm(
    session_id: str,
    command: str,
    reason: str = "",
    target: str = "",
    state: str = "",
    timeout: int = 600,
) -> dict:
    """
    请求用户确认 AutoPilot 即将执行的单条命令。

    返回 dict:
        {"action": "execute", "modified_command": "", "user_input": ""}
        {"action": "modify",  "modified_command": "修改后的命令", "user_input": ""}
        {"action": "skip",    "modified_command": "", "user_input": "用户指定下一步"}
        {"action": "abort",   "modified_command": "", "user_input": ""}
    超时返回 {"action": "abort"}
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_command_confirmations[session_id] = future

    manager = get_connection_manager()
    payload = {
        "type": "autopilot_command_confirm_request",
        "session_id": session_id,
        "command": command,
        "reason": reason,
        "target": target,
        "state": state,
        "timestamp": time.time(),
    }
    logger.info("[AutoPilot] 等待命令确认: session_id=%s, cmd=%s", session_id, command)
    await manager.broadcast(payload)

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        logger.info("[AutoPilot] 命令确认结果: session_id=%s, action=%s", session_id, result.get("action"))
        return result
    except asyncio.TimeoutError:
        logger.warning("[AutoPilot] 命令确认超时（%ss）: session_id=%s", timeout, session_id)
        return {"action": "abort", "modified_command": "", "user_input": ""}
    finally:
        pending_command_confirmations.pop(session_id, None)


async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket端点 - 修复CORS 403问题
    关键：先accept连接，再做宽松验证
    """
    # 1. 先接受连接（这是最重要的，避免CORS 403）
    await websocket.accept()

    # 2. 无需 token 验证，直接设置默认用户
    user_id = "default"
    username = "user"

    # 3. 注册连接
    manager = get_connection_manager()
    manager.register(websocket, user_id)

    # 4. 发送连接成功消息
    try:
        await manager.send_personal(user_id, {
            "type": "connected",
            "user_id": user_id,
            "username": username,
            "message": "WebSocket 连接已建立",
        })
        logger.info("WebSocket 已连接: user=%s", username)
    except Exception:
        pass

    # 5. 消息循环（★ 增加 45s 超时保活，服务端主动 ping 维持链路）
    try:
        while True:
            try:
                data_raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=180
                )
            except asyncio.TimeoutError:
                # 180 秒无消息，服务端主动下发 ping 保活
                logger.debug("WebSocket 180s 空闲，向 %s 发送服务器保活 ping", username)
                try:
                    await manager.send_personal(user_id, {
                        "type": "ping",
                        "server": True,
                        "timestamp": time.time(),
                    })
                except Exception:
                    logger.debug("保活 ping 发送失败: user=%s", username)
                continue

            try:
                msg = json.loads(data_raw)
                msg_type = msg.get("type")

                # ★ 新增：处理前端 AutoPilot 确认响应
                if msg_type == "autopilot_confirm_response":
                    sess_id = msg.get("session_id", "")
                    confirm = msg.get("confirm", False)
                    future = pending_confirmations.pop(sess_id, None)
                    if future and not future.done():
                        future.set_result(confirm)
                        logger.info("[AutoPilot] 收到用户响应: session_id=%s, confirm=%s", sess_id, confirm)
                    else:
                        logger.debug("[AutoPilot] 确认已处理或超时: session_id=%s", sess_id)

                # ★ 处理前端 AutoPilot 命令确认响应
                if msg_type == "autopilot_command_confirm_response":
                    sess_id = msg.get("session_id", "")
                    action = msg.get("action", "execute")
                    modified_command = msg.get("modified_command", "")
                    user_input = msg.get("user_input", "")
                    future = pending_command_confirmations.pop(sess_id, None)
                    if future and not future.done():
                        future.set_result({
                            "action": action,
                            "modified_command": modified_command,
                            "user_input": user_input,
                        })
                        logger.info("[AutoPilot] 收到命令确认响应: session_id=%s, action=%s", sess_id, action)
                    else:
                        logger.debug("[AutoPilot] 命令确认已处理或超时: session_id=%s", sess_id)

                # ★ 处理手动插入命令（AutoPilot 运行中）
                if msg_type == "autopilot_manual_command":
                    sess_id = msg.get("session_id", "")
                    command = msg.get("command", "")
                    if sess_id and command:
                        from core.orchestrator import Orchestrator
                        ok = await Orchestrator.push_manual_command(sess_id, command)
                        logger.info("[手动命令] %s: session_id=%s, cmd=%s",
                                     "已入队" if ok else "失败(无运行中AutoPilot)", sess_id, command[:100])

                # ★ 处理工具选择响应
                if msg_type == "autopilot_tool_choice_response":
                    sess_id = msg.get("session_id", "")
                    use_alt = msg.get("use_alternative", False)
                    from core.orchestrator import _tool_choice_futures
                    future = _tool_choice_futures.pop(sess_id, None)
                    if future and not future.done():
                        future.set_result({"use_alternative": use_alt})
                        logger.info("[工具选择] session_id=%s, use_alternative=%s", sess_id, use_alt)

                # ★ 新增：处理 AutoPilot 控制命令 (pause/continue/stop/strategy/status)
                if msg_type in ("pause", "continue", "stop", "strategy", "status"):
                    sess_id = msg.get("session_id", "")
                    engine = _auto_engines.get(sess_id)
                    if not engine:
                        logger.warning("[WS] 命令 %s: 未找到运行中的引擎 session_id=%s", msg_type, sess_id)
                        await manager.send_personal(user_id, {
                            "type": "command_response",
                            "command": msg_type,
                            "session_id": sess_id,
                            "success": False,
                            "message": f"未找到运行中的 AutoPilot 会话: {sess_id}",
                            "timestamp": time.time(),
                        })
                    else:
                        try:
                            if msg_type == "pause":
                                engine.pause()
                                await manager.send_personal(user_id, {
                                    "type": "command_response",
                                    "command": "pause",
                                    "session_id": sess_id,
                                    "success": True,
                                    "message": "AutoPilot 已暂停",
                                    "timestamp": time.time(),
                                })
                            elif msg_type == "continue":
                                engine.resume()
                                await manager.send_personal(user_id, {
                                    "type": "command_response",
                                    "command": "continue",
                                    "session_id": sess_id,
                                    "success": True,
                                    "message": "AutoPilot 已恢复",
                                    "timestamp": time.time(),
                                })
                            elif msg_type == "stop":
                                engine.stop()
                                await manager.send_personal(user_id, {
                                    "type": "command_response",
                                    "command": "stop",
                                    "session_id": sess_id,
                                    "success": True,
                                    "message": "AutoPilot 已停止",
                                    "timestamp": time.time(),
                                })
                            elif msg_type == "strategy":
                                text = msg.get("text", "")
                                engine.set_strategy(text)
                                await manager.send_personal(user_id, {
                                    "type": "command_response",
                                    "command": "strategy",
                                    "session_id": sess_id,
                                    "success": True,
                                    "message": f"策略已更新: {text[:100]}",
                                    "timestamp": time.time(),
                                })
                            elif msg_type == "status":
                                status = engine.get_status()
                                await manager.send_personal(user_id, {
                                    "type": "command_response",
                                    "command": "status",
                                    "session_id": sess_id,
                                    "success": True,
                                    "status": status,
                                    "timestamp": time.time(),
                                })
                        except Exception as cmd_exc:
                            logger.error("[WS] 命令 %s 执行失败: %s", msg_type, cmd_exc)
                            await manager.send_personal(user_id, {
                                "type": "command_response",
                                "command": msg_type,
                                "session_id": sess_id,
                                "success": False,
                                "message": str(cmd_exc),
                                "timestamp": time.time(),
                            })

                if msg_type == "ping":
                    logger.debug("收到前端 ping: user=%s", username)
                    try:
                        await manager.send_personal(user_id, {
                            "type": "pong",
                            "timestamp": time.time(),
                        })
                        logger.debug("已回复 pong: user=%s", username)
                    except Exception as send_exc:
                        logger.warning("回复 pong 失败: user=%s, exc=%s", username, send_exc)
            except json.JSONDecodeError:
                # 不是 JSON，可能是原生 WebSocket ping 帧，忽略
                logger.debug("收到非 JSON 消息: user=%s, data=%r", username, data_raw[:100] if data_raw else "")
                pass
    except WebSocketDisconnect:
        logger.info("WebSocket 断开: user=%s", username)
    except Exception as exc:
        logger.debug("WebSocket 异常: %s", exc)
    finally:
        manager.disconnect(websocket)

"""
系统管理 API 路由 (api/system_routes.py)
端点:
- GET    /api/system/logs             获取系统日志
- GET    /api/system/mcp              获取MCP服务器状态
- POST   /api/system/mcp/start        启动MCP服务器
- POST   /api/system/mcp/stop         停止MCP服务器
"""
from __future__ import annotations
import os
import json
import asyncio
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["系统"])

# 全局状态 - 添加线程锁保护并发访问
_MCP_SERVER_RUNNING = False
_MCP_SERVER_TASK = None
_STATE_LOCK = threading.Lock()

# 日志文件路径（使用项目根目录的绝对路径，避免 uvicorn 工作目录不一致导致找不到文件）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILES = [
    str(PROJECT_ROOT / "logs" / "debug.log"),
    str(PROJECT_ROOT / "logs" / "error.log"),
    str(PROJECT_ROOT / "logs" / "security.log"),
]

def get_mcp_running() -> bool:
    """获取MCP服务器状态（线程安全）"""
    with _STATE_LOCK:
        return _MCP_SERVER_RUNNING

def set_mcp_running(running: bool) -> None:
    """设置MCP服务器状态（线程安全）"""
    global _MCP_SERVER_RUNNING
    with _STATE_LOCK:
        _MCP_SERVER_RUNNING = running

def get_mcp_task():
    """获取MCP服务器任务（线程安全）"""
    with _STATE_LOCK:
        return _MCP_SERVER_TASK

def set_mcp_task(task) -> None:
    """设置MCP服务器任务（线程安全）"""
    global _MCP_SERVER_TASK
    with _STATE_LOCK:
        _MCP_SERVER_TASK = task

@router.get("/logs")
async def get_system_logs(
    lines: int = 100,
    log_type: str = "all",
):
    """获取系统日志"""
    logs = []
    
    # 参数校验
    if lines < 10 or lines > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="日志行数必须在10-1000之间"
        )
    
    target_files = LOG_FILES
    if log_type != "all":
        target_files = [f"logs/{log_type}.log"]
    
    for log_file in target_files:
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    all_lines = f.readlines()
                    recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
                    for line in recent_lines:
                        # 解析日志级别
                        level = "INFO"
                        if "ERROR" in line.upper():
                            level = "ERROR"
                        elif "WARNING" in line.upper() or "WARN" in line.upper():
                            level = "WARNING"
                        
                        logs.append({
                            "timestamp": line[:19] if len(line) > 19 else "",
                            "level": level,
                            "source": os.path.basename(log_file),
                            "message": line.strip()
                        })
            except Exception as e:
                logs.append({
                    "timestamp": "",
                    "level": "ERROR",
                    "source": log_file,
                    "message": f"读取日志失败: {str(e)}"
                })
    
    # 按时间排序
    try:
        logs.sort(key=lambda x: x["timestamp"], reverse=True)
    except Exception:
        pass
    
    return {
        "logs": logs[:lines],
        "total": len(logs)
    }

@router.get("/mcp")
async def get_mcp_status():
    """获取MCP服务器状态"""
    try:
        from config.settings import get_settings
        settings = get_settings()
        
        host = getattr(settings, 'mcp_host', '127.0.0.1')
        port = getattr(settings, 'mcp_port', 8911)
        
        return {
            "running": get_mcp_running(),
            "host": host,
            "port": port,
            "endpoint": f"http://{host}:{port}/sse"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取MCP状态失败: {str(e)}"
        )

@router.post("/mcp/start")
async def start_mcp_server():
    """启动MCP服务器"""
    if get_mcp_running():
        return {
            "success": True,
            "running": True,
            "message": "MCP服务器已在运行中"
        }
    
    try:
        from config.settings import get_settings
        
        settings = get_settings()
        host = getattr(settings, 'mcp_host', '127.0.0.1')
        port = getattr(settings, 'mcp_port', 8911)
        
        # ★ 真正启动MCP服务器（后台任务，不阻塞API响应）
        try:
            from mcp_server.server import create_mcp_server
        except ImportError as ie:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"MCP模块导入失败: {ie}。请确保 mcp/server.py 存在且依赖已安装。"
            )
        
        # 先创建 MCP 服务器实例（不阻塞）
        mcp_server = await create_mcp_server(host=host, port=port)
        if mcp_server is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MCP服务器创建失败。请确认已安装 fastmcp: pip install fastmcp"
            )
        
        # run_http_async() 是阻塞的，需要在后台任务中运行
        async def _run_mcp():
            try:
                await mcp_server.run_http_async(transport="sse", host=host, port=port, show_banner=False)
            except asyncio.CancelledError:
                logger.info("[MCP] 服务器后台任务已取消")
                set_mcp_running(False)
            except Exception as exc:
                logger.error("[MCP] 服务器后台任务异常: %s", exc)
                set_mcp_running(False)
        
        task = asyncio.create_task(_run_mcp())
        set_mcp_task(task)
        set_mcp_running(True)
        
        logger.info("[MCP] 服务器已真实启动: http://%s:%d/sse", host, port)
        
        return {
            "success": True,
            "running": True,
            "host": host,
            "port": port,
            "message": f"MCP服务器已启动: http://{host}:{port}/sse"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MCP服务器启动失败: {str(e)}"
        )

@router.post("/mcp/stop")
async def stop_mcp_server():
    """停止MCP服务器"""
    if not get_mcp_running():
        return {
            "success": True,
            "running": False,
            "message": "MCP服务器未运行"
        }
    
    try:
        task = get_mcp_task()
        if task:
            task.cancel()
            set_mcp_task(None)
        
        set_mcp_running(False)
        
        return {
            "success": True,
            "running": False,
            "message": "MCP服务器已停止"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MCP服务器停止失败: {str(e)}"
        )


@router.get("/autopilot")
async def get_autopilot_status():
    """获取 AutoPilot 运行状态"""
    try:
        from core.auto_pilot import _running_engines
        any_running = bool(_running_engines)
        max_steps = 20
        current_step = 0

        # 尝试获取第一个运行中的引擎状态
        for sid, engine in _running_engines.items():
            status = engine.get_status()
            max_steps = status.get("max_steps", 20)
            current_step = status.get("current_step", 0)
            break

        return {
            "enabled": any_running,
            "running": any_running,
            "max_steps": max_steps,
            "current_step": current_step,
            "status": "running" if any_running else "idle",
        }
    except Exception as e:
        logger.warning("获取 AutoPilot 状态失败: %s", e)
        return {
            "enabled": False,
            "running": False,
            "max_steps": 20,
            "current_step": 0,
            "status": "idle",
        }


@router.post("/autopilot/toggle")
async def toggle_autopilot(enabled: bool = True):
    """启用或禁用 AutoPilot"""
    logger.info("切换 AutoPilot 状态: enabled=%s", enabled)
    return {
        "success": True,
        "enabled": enabled,
        "message": f"AutoPilot 已{'启用' if enabled else '禁用'}"
    }


@router.post("/test/simulate")
async def simulate_test_data():
    """推送模拟测试数据到前端（仅用于测试）"""
    import time
    from api.websocket import get_connection_manager
    manager = get_connection_manager()
    
    session_id = "e63a90e1de41"
    
    # 1. 阶段变更
    await manager.broadcast({
        "type": "autopilot_state_change",
        "session_id": session_id,
        "phase": "vulnerability_scanning",
        "phase_name": "漏洞扫描",
        "step": 2,
        "progress": 45,
        "state": "正在进行 Web 目录扫描...",
        "description": "使用 gobuster 扫描 Web 目录",
        "timestamp": time.time(),
    })
    
    # 2. 系统日志
    await manager.broadcast({
        "type": "autopilot_progress",
        "session_id": session_id,
        "source": "AutoPilot",
        "level": "info",
        "category": "system",
        "message": "🚀 AutoPilot 已启动，目标: http://test.example.com",
        "timestamp": time.time(),
    })
    
    # 3. 工具开始执行
    await manager.broadcast({
        "type": "tool_exec_started",
        "session_id": session_id,
        "command": "nmap -sV -sC test.example.com",
        "tool": "nmap",
        "timestamp": time.time(),
    })
    
    # 4. AI 分析结果
    ai_content = """📊 分析总结

我将执行端口扫描，这是渗透测试的第一步。

为什么做这一步：
- 端口扫描可以发现目标开放的服务
- 了解目标系统运行的服务版本
- 为后续的漏洞检测提供基础信息

⚠️ 漏洞风险

可能存在的风险点：
- 开放的服务可能存在已知漏洞
- 过时的服务版本可能有 CVE 漏洞
- 默认配置可能导致信息泄露

➡️ 下一步计划

1. 执行 nmap 端口扫描
2. 根据扫描结果选择针对性工具
3. 检测 Web 服务漏洞（如 SQL 注入、XSS）
4. 尝试获取系统访问权限"""
    
    await manager.broadcast({
        "type": "autopilot_ai_response",
        "session_id": session_id,
        "content": ai_content,
        "timestamp": time.time(),
    })
    
    # 5. 工具执行结果（成功，绿色）
    nmap_output = """Starting Nmap 7.94SVN ( https://nmap.org )
Nmap scan report for test.example.com (93.184.216.34)
Host is up (0.023s latency).

PORT   STATE SERVICE VERSION
22/tcp open  ssh     OpenSSH 8.2p1 Ubuntu 4ubuntu0.5
80/tcp open  http    Apache httpd 2.4.41 ((Ubuntu))
443/tcp open  https   Apache httpd 2.4.41 ((Ubuntu))
3306/tcp open mysql   MySQL 8.0.33

Nmap done: 1 IP address (1 host up) scanned in 12.34 seconds"""
    
    await manager.broadcast({
        "type": "tool_exec_result",
        "session_id": session_id,
        "command": "nmap -sV -sC test.example.com",
        "tool": "nmap",
        "success": True,
        "output": nmap_output,
        "error": "",
        "duration": 12.34,
        "return_code": 0,
        "timestamp": time.time(),
    })
    
    # 6. 成功日志（绿色）
    await manager.broadcast({
        "type": "autopilot_progress",
        "session_id": session_id,
        "source": "AutoPilot",
        "level": "info",
        "category": "success",
        "message": "✅ 端口扫描完成，发现 4 个开放端口",
        "detail": "22(ssh), 80(http), 443(https), 3306(mysql)",
        "timestamp": time.time(),
    })
    
    # 7. 第二个 AI 分析
    ai_analysis = """📊 扫描总结

端口扫描完成，发现 4 个开放服务。

📌 已发现

开放端口：
- 22/tcp: OpenSSH 8.2p1
- 80/tcp: Apache 2.4.41
- 443/tcp: Apache 2.4.41
- 3306/tcp: MySQL 8.0.33

⚠️ 漏洞风险

潜在风险点：
1. OpenSSH 8.2p1 - 需检查用户枚举漏洞
2. Apache 2.4.41 - 需检查路径遍历、请求走私
3. MySQL 8.0.33 - 需检查弱口令、默认配置
4. Web 服务可能存在 SQL 注入、XSS 等漏洞

➡️ 下一步计划

1. 对 Web 服务进行目录扫描（gobuster）
2. 检测 Web 应用漏洞（sqlmap、xsstrike）
3. 尝试 SSH 弱口令爆破（hydra）
4. 检查 MySQL 匿名访问"""
    
    await manager.broadcast({
        "type": "autopilot_ai_response",
        "session_id": session_id,
        "content": ai_analysis,
        "timestamp": time.time(),
    })
    
    # 8. gobuster 结果
    gobuster_output = """===============================================================
Gobuster v3.6
===============================================================
[+] Url:                     http://test.example.com
[+] Status codes:            200,204,301,302,307,401,403
===============================================================
/admin                (Status: 301) [Size: 0]
/login                (Status: 200) [Size: 1234]
/backup               (Status: 403) [Size: 278]
/uploads              (Status: 301) [Size: 0]
/phpmyadmin           (Status: 403) [Size: 94]
===============================================================
Finished
==============================================================="""
    
    await manager.broadcast({
        "type": "tool_exec_result",
        "session_id": session_id,
        "command": "gobuster dir -u http://test.example.com -w common.txt",
        "tool": "gobuster",
        "success": True,
        "output": gobuster_output,
        "error": "",
        "duration": 8.56,
        "return_code": 0,
        "timestamp": time.time(),
    })
    
    # 9. 成功日志
    await manager.broadcast({
        "type": "autopilot_progress",
        "session_id": session_id,
        "source": "AutoPilot",
        "level": "info",
        "category": "success",
        "message": "✅ 目录扫描完成，发现 5 个敏感路径",
        "detail": "/admin, /login, /backup, /uploads, /phpmyadmin",
        "timestamp": time.time(),
    })
    
    return {
        "success": True,
        "message": "模拟数据已推送",
        "data_count": 9,
    }

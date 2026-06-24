"""
FastAPI 应用主入口 (api/app.py)
⚠️ 安全警告：本工具仅用于合法授权的渗透测试！
使用者必须遵守当地法律法规，禁止用于未授权的系统测试。
开发者不承担任何因使用本工具造成的法律责任。
功能:
- FastAPI 应用实例 + CORS 中间件
- Swagger 文档自动生成 (/docs, /redoc)
- 注册所有 API 路由
- JWT 认证保护（未授权返回 401）
- 启动时初始化默认用户 + 创建表
- 生命周期管理
- 系统运行时长统计
"""
from __future__ import annotations
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from config.settings import get_settings
from utils.logger import get_logger  # 确保日志系统在启动时初始化

# 路由导入
from api.session_routes import router as session_router
from api.tool_routes import router as tool_router
from api.report_routes import router as report_router
from api.config_routes import router as config_router
from api.system_routes import router as system_router
from api.llm_routes import router as llm_router
from api.tool_routes_exec import router as tool_exec_router

logger = get_logger(__name__)
# 系统启动时间（全局变量）
SYSTEM_START_TIME = time.time()
def get_system_uptime() -> str:
    """获取系统运行时长，格式化字符串"""
    seconds = int(time.time() - SYSTEM_START_TIME)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}小时 {minutes}分 {secs}秒"
    elif minutes > 0:
        return f"{minutes}分 {secs}秒"
    return f"{secs}秒"
def get_system_uptime_seconds() -> int:
    """获取系统运行时长（秒数）"""
    return int(time.time() - SYSTEM_START_TIME)
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global SYSTEM_START_TIME
    SYSTEM_START_TIME = time.time()
    logger.info("FastAPI 服务启动中...")
    try:
        # 必须先导入 models 才能让 Base.metadata 包含所有表定义（与cli.py/main.py一致）
        import db.models
        from db.database import get_database
        db = get_database()
        await db.init_models()
        logger.info("数据库表已就绪")
    except Exception as exc:
        logger.warning("数据库初始化警告: %s", exc)
    yield
    logger.info("🛑 FastAPI 服务关闭中，正在清理运行中的任务...")
    # ⭐ 1. 先通知所有 LLM 客户端停止重试（最快响应，优先）
    try:
        from core.llm_client import signal_llm_shutdown
        signal_llm_shutdown()
        logger.info("✅ 已发送 LLM 关闭信号")
    except Exception as exc:
        logger.warning("发送 LLM 关闭信号时异常: %s", exc)
    # ⭐ 2. 取消所有 AutoPilot 任务
    try:
        from core.orchestrator import Orchestrator
        await Orchestrator.cancel_all_autopilots()
    except Exception as exc:
        logger.warning("取消 AutoPilot 任务时异常: %s", exc)
    # ⭐ 3. 终止所有工具子进程
    try:
        from tools.executor import get_executor
        killed_exec = get_executor().kill_all()
        logger.info("✅ 已终止 executor=%d 个子进程", killed_exec)
    except Exception as exc:
        logger.warning("终止子进程时异常: %s", exc)
    logger.info("FastAPI 服务已完全关闭")

app = FastAPI(
    title="PengStrike API",
    description="PengStrike 渗透测试辅助平台 - RESTful API 接口",
    version="0.1.0-alpha",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
# 从配置读取 CORS 允许来源（生产环境应限制）
settings = get_settings()
cors_origins_list = settings.get_cors_origins()
# 如果包含 "*"，则允许所有来源（开发模式）
if "*" in cors_origins_list:
    allow_origins_list = ["*"]
    allow_credentials_value = False  # 通配符 * 不能与 credentials=True 共存
    logger.warning(
        "CORS: 允许所有来源（开发模式）。生产环境请设置 CORS_ALLOWED_ORIGINS 环境变量。"
    )
else:
    allow_origins_list = cors_origins_list
    allow_credentials_value = True
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins_list,
    allow_credentials=allow_credentials_value,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局异常处理器：记录完整异常信息，返回安全响应"""
    import traceback
    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error(
        "全局未捕获异常: %s %s\n%s",
        request.method, request.url.path, tb_str
    )
    
    # 区分不同类型的异常
    status_code = 500
    detail = f"服务器内部错误: {type(exc).__name__}"
    
    if isinstance(exc, ValueError):
        status_code = 400
        detail = f"请求参数错误: {exc}"
    elif isinstance(exc, FileNotFoundError):
        status_code = 404
        detail = f"资源不存在: {exc}"
    elif isinstance(exc, PermissionError):
        status_code = 403
        detail = "权限不足"
    
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
    )
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "version": "0.1.0-alpha",
        "service": "PengStrike API",
        "uptime": get_system_uptime(),
        "uptime_seconds": get_system_uptime_seconds(),
    }
@app.get("/api/stats")
async def get_stats():
    from tools.registry import get_registry

    registry = get_registry()

    # 数据库操作放在try-except中，避免缺少依赖导致整个接口失败
    session_count = 0
    completed_count = 0
    try:
        from db.database import get_database
        from db.models import Session
        from sqlalchemy import func, select
        db = get_database()
        async with db.get_session() as session:
            result = await session.execute(func.count(Session.id))
            session_count = result.scalar() or 0
            result2 = await session.execute(
                select(func.count(Session.id)).where(Session.status == "completed")
            )
            completed_count = result2.scalar() or 0
    except Exception as e:
        logger.warning(f"数据库查询失败（不影响核心功能）: {e}")

    report_count = 0
    try:
        from pathlib import Path
        for d in [Path.cwd() / "data" / "reports", Path.cwd() / "reports_output"]:
            if d.exists():
                report_count += len([f for f in d.iterdir() if f.suffix in (".html", ".json")])
    except Exception:
        pass

    return {
        "tools": len(registry.list_all()),
        "sessions": session_count,
        "completedTasks": completed_count,
        "totalReports": report_count,
        "version": "0.1.0-alpha",
        "uptime": get_system_uptime(),
        "uptime_seconds": get_system_uptime_seconds(),
    }
@app.get("/api/activity")
async def get_activity():
    """返回最近活动列表（会话事件 + 工具执行 + 报告生成）。"""
    from db.database import get_database
    from db.models import Session, ExecutionStep
    from sqlalchemy import select, desc
    from utils.time_utils import format_timestamp

    items = []

    # 1. 最近 10 条会话
    try:
        db = get_database()
        async with db.get_session() as sess:
            result = await sess.execute(
                select(Session).order_by(desc(Session.updated_at)).limit(10)
            )
            for row in result.scalars():
                event_type = "session_created"
                if row.status == "completed":
                    event_type = "session_completed"
                elif row.status == "failed":
                    event_type = "session_failed"
                items.append({
                    "type": event_type,
                    "time": row.updated_at.isoformat() if row.updated_at else "",
                    "content": f"[{row.status}] {row.target} — {row.phase or '未开始'}",
                    "session_id": row.id,
                    "target": row.target,
                })
    except Exception as e:
        logger.debug("获取活动信息异常: %s", e)

    # 2. 最近 5 条工具执行
    try:
        db = get_database()
        async with db.get_session() as sess:
            result = await sess.execute(
                select(ExecutionStep).where(ExecutionStep.action_type == "tool_execution")
                .order_by(desc(ExecutionStep.created_at)).limit(5)
            )
            for row in result.scalars():
                items.append({
                    "type": "tool_execution",
                    "time": row.created_at.isoformat() if row.created_at else "",
                    "content": f"🔧 执行: {row.tool_name} ({str(row.tool_args or '')[:100]})",
                    "session_id": row.session_id,
                    "success": row.tool_success,
                })
    except Exception:
        pass

    # 按时间排序取前 20
    items.sort(key=lambda x: x.get("time", ""), reverse=True)
    return {"items": items[:20]}

app.include_router(session_router)
app.include_router(tool_router)
app.include_router(report_router)
app.include_router(config_router)
app.include_router(system_router)
app.include_router(llm_router)
app.include_router(tool_exec_router)
# WebSocket 路由 - 正确使用 WebSocket 类型
from api.websocket import websocket_endpoint
@app.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket_endpoint(websocket)

"""
报告管理 API 路由 (api/report_routes.py)

端点:
- GET  /api/reports                   列出所有报告文件
- POST /api/reports/generate          生成报告
- GET  /api/reports/download/{name}   下载报告文件
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from reports.manager import ReportManager
from pydantic import BaseModel
from utils.time_utils import format_timestamp

router = APIRouter(prefix="/api/reports", tags=["报告"])


class GenerateReportRequest(BaseModel):
    session_id: str
    format: str = "markdown"


def _get_reports_dir() -> Path:
    """获取报告目录：优先 settings 配置，其次 data/reports（新引擎），最后 reports_output（旧）。"""
    try:
        from config.settings import get_settings
        s = get_settings()
        if s.report_output_dir:
            p = Path(s.report_output_dir)
            if p.exists():
                return p
    except Exception:
        pass
    # 新引擎默认输出到 data/reports
    data_reports = Path.cwd() / "data" / "reports"
    if data_reports.exists():
        return data_reports
    return Path.cwd() / "reports_output"


@router.get("")
async def list_reports():
    reports_dir = _get_reports_dir()
    if not reports_dir.exists():
        return {"total": 0, "items": []}
    items = []
    for f in sorted(reports_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix in (".html", ".md", ".pdf"):
            ext = f.suffix.lstrip(".")
            items.append({
                "id": f.stem,
                "title": f.stem.replace("report_", "").replace("_", " ").title(),
                "filename": f.name,
                "format": ext,
                "status": "completed",
                "sessionName": f.stem.split("_")[1] if "_" in f.stem else "",
                "size": f.stat().st_size,
                "createdAt": format_timestamp(f.stat().st_mtime),
            })
    return {"total": len(items), "items": items}


@router.post("/generate")
async def generate_report(req: GenerateReportRequest):
    mgr = ReportManager(output_dir=str(_get_reports_dir()))
    path = None
    fmt = req.format.lower()
    if fmt == "markdown":
        path = await mgr.generate_markdown(req.session_id)
    elif fmt == "html":
        path = await mgr.generate_html(req.session_id)
    elif fmt == "pdf":
        path = await mgr.generate_pdf(req.session_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的格式: {fmt} (支持: markdown/html/pdf)"
        )
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="报告生成失败"
        )
    return {"status": "generated", "path": path, "filename": Path(path).name}


@router.get("/download/{filename:path}")
async def download_report(filename: str):
    reports_dir = _get_reports_dir()
    file_path = reports_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/{filename:path}")
async def delete_report(filename: str):
    reports_dir = _get_reports_dir()
    file_path = reports_dir / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")
    os.remove(str(file_path))
    return {"status": "deleted", "filename": filename}

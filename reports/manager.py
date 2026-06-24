"""
报告生成系统 (reports/manager.py)

核心职责:
- 从数据库提取会话数据和执行步骤
- 使用 Jinja2 模板引擎生成报告
- 支持三种输出格式: Markdown / HTML / PDF
- 支持添加自定义报告模板

技术实现:
- 数据库查询使用 SQLAlchemy 异步会话
- Jinja2 模板使用 FileSystemLoader 从 templates/ 目录加载
- PDF 生成使用 pdfkit (wkhtmltopdf 包装)
- HTML 报告包含表格和代码高亮
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from utils.time_utils import format_beijing_time, format_timestamp

from utils.logger import get_logger

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class ReportManager:
    """报告生成管理器。

    功能:
    - generate_markdown(): 生成 Markdown 报告
    - generate_html(): 生成 HTML 报告（含表格和代码高亮）
    - generate_pdf(): 生成 PDF 报告（基于 HTML 转换）
    - add_template(): 注册自定义模板
    - list_templates(): 列出所有可用模板
    """

    def __init__(
        self,
        templates_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        self._templates_dir = Path(templates_dir) if templates_dir else _TEMPLATES_DIR
        self._output_dir = Path(output_dir) if output_dir else Path.cwd() / "reports_output"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

        self._custom_templates: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # 数据提取
    # ------------------------------------------------------------------
    async def _fetch_session_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """从数据库提取会话数据。"""
        try:
            from db.database import get_database
            from db.models import Session, ExecutionStep
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            db = get_database()
            async with db.get_session() as dbsession:
                result = await dbsession.execute(
                    select(Session)
                    .where(Session.id == session_id)
                    .options(selectinload(Session.steps))
                )
                session = result.scalar_one_or_none()
                if session is None:
                    logger.error("[Report] 会话不存在: %s", session_id)
                    return None

                steps_data = []
                all_ports = []  # ★ 收集所有发现的端口
                all_vulns = []  # ★ 收集所有发现的漏洞
                for step in session.steps:
                    # ★ 确保 tool_args 是 dict 而非 JSON 字符串
                    _raw_args = step.tool_args
                    if isinstance(_raw_args, str):
                        try:
                            _raw_args = json.loads(_raw_args)
                        except Exception:
                            _raw_args = {}
                    steps_data.append({
                        "seq": step.seq,
                        "action_type": step.action_type,
                        "tool_name": step.tool_name,
                        "tool_args": _raw_args,
                        "tool_success": step.tool_success,
                        "tool_output_summary": step.tool_output_summary,
                        "tool_duration": step.tool_duration,
                        "llm_content": step.llm_content,
                        "from_state": step.from_state,
                        "to_state": step.to_state,
                        "created_at": format_beijing_time(step.created_at) if step.created_at else "",
                    })
                    # ★ 从 nmap 输出提取端口
                    if step.tool_name == "execute_kali_command" and step.tool_output_summary:
                        output = step.tool_output_summary or ""
                        for line in output.splitlines():
                            m = re.match(r'^(\d+)/tcp\s+open\s+(\S+)\s+(.+)$', line.strip())
                            if m:
                                port_info = f"{m.group(1)}/tcp {m.group(2)} {m.group(3).strip()}"
                                if port_info not in all_ports:
                                    all_ports.append(port_info)
                    # ★ 从 AI 分析提取漏洞关键词（改进版：清理 markdown 和序号）
                    if step.action_type == "ai_analysis" and step.llm_content:
                        llm_text = step.llm_content or ""
                        vuln_keywords = ["CVE-", "漏洞", "远程代码执行", "SQL注入", "XSS", "文件包含",
                                         "未授权访问", "命令执行", "任意文件", "权限提升",
                                         "文件上传", "SQL注入", "CSRF", "SSRF", "目录遍历"]
                        for kw in vuln_keywords:
                            if kw.lower() in llm_text.lower():
                                for line in llm_text.split("."):
                                    if kw.lower() in line.lower() and line.strip():
                                        raw = line.strip()[:200]
                                        # 清理 markdown 和序号后缀
                                        clean = raw.replace("**", "").replace("__", "")
                                        # 去掉末尾的数字序号（如 "1" "2" 但保留数字内容）
                                        clean = re.sub(r'\s*\d+\s*$', '', clean)
                                        if clean and len(clean) > 5:
                                            if clean not in [v["desc"] for v in all_vulns]:
                                                severity = "高危" if any(k in clean for k in ["远程代码执行","命令执行","权限提升","文件上传","SQL注入"]) else "中危"
                                                severity = "低危" if any(k in clean for k in ["目录遍历","信息泄露","点击劫持"]) else severity
                                                all_vulns.append({"desc": clean.strip(), "severity": severity})
                                        break

                return {
                    "id": session.id,
                    "target": session.target,
                    "status": {"active": "已完成", "completed": "已完成", "aborted": "已中止", "failed": "失败", "paused": "已暂停"}.get(session.status, session.status),
                    "phase": session.phase,
                    "is_paused": session.is_paused,
                    "created_at": format_beijing_time(session.created_at) if session.created_at else "",
                    "updated_at": format_beijing_time(session.updated_at) if session.updated_at else "",
                    "current_state_data": session.current_state_data or {},
                    "steps": steps_data,
                    "steps_count": len(steps_data),
                    "ports": sorted(all_ports),
                    "vulnerabilities": all_vulns,
                    "vuln_count_high": sum(1 for v in all_vulns if "高危" in v["severity"]),
                    "vuln_count_mid": sum(1 for v in all_vulns if "中危" in v["severity"]),
                    "vuln_count_low": sum(1 for v in all_vulns if "低危" in v["severity"]),
                    "generated_at": format_beijing_time(datetime.now()),
                }
        except Exception as exc:
            logger.error("[Report] 数据提取失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Markdown 报告
    # ------------------------------------------------------------------
    async def generate_markdown(self, session_id: str) -> Optional[str]:
        """生成 Markdown 格式报告。返回文件路径。"""
        data = await self._fetch_session_data(session_id)
        if data is None:
            return None

        try:
            template = self._env.get_template("markdown_report.jinja2")
            output = template.render(**data)

            output_path = self._output_dir / f"report_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            output_path.write_text(output, encoding="utf-8")
            logger.info("[Report] Markdown 报告已生成: %s", output_path)
            return str(output_path)
        except Exception as exc:
            logger.error("[Report] Markdown 生成失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # HTML 报告
    # ------------------------------------------------------------------
    async def generate_html(self, session_id: str) -> Optional[str]:
        """生成 HTML 格式报告。返回文件路径。"""
        data = await self._fetch_session_data(session_id)
        if data is None:
            return None

        try:
            template = self._env.get_template("html_report.jinja2")
            output = template.render(**data)

            output_path = self._output_dir / f"report_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            output_path.write_text(output, encoding="utf-8")
            logger.info("[Report] HTML 报告已生成: %s", output_path)
            return str(output_path)
        except Exception as exc:
            logger.error("[Report] HTML 生成失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # PDF 报告（基于 HTML 转换）
    # ------------------------------------------------------------------
    async def generate_pdf(self, session_id: str) -> Optional[str]:
        """生成 PDF 格式报告。返回文件路径。"""
        html_path = await self.generate_html(session_id)
        if html_path is None:
            return None

        try:
            import pdfkit

            output_path = html_path.replace(".html", ".pdf")
            pdfkit.from_file(html_path, output_path, options={
                "page-size": "A4",
                "margin-top": "15mm",
                "margin-right": "15mm",
                "margin-bottom": "15mm",
                "margin-left": "15mm",
                "encoding": "UTF-8",
                "enable-local-file-access": None,
            })
            logger.info("[Report] PDF 报告已生成: %s", output_path)
            return output_path
        except ImportError:
            logger.warning("[Report] pdfkit 未安装，无法生成 PDF")
            return None
        except Exception as exc:
            logger.error("[Report] PDF 生成失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    # 自定义模板
    # ------------------------------------------------------------------
    def add_template(self, name: str, template_content: str) -> bool:
        """注册自定义模板（运行时添加）。"""
        try:
            from jinja2 import Template
            Template(template_content)
            self._custom_templates[name] = template_content
            logger.info("[Report] 自定义模板已注册: %s", name)
            return True
        except Exception as exc:
            logger.error("[Report] 模板注册失败 %s: %s", name, exc)
            return False

    def list_templates(self) -> List[str]:
        """列出所有可用模板。"""
        builtin = []
        if self._templates_dir.exists():
            builtin = sorted([f.name for f in self._templates_dir.glob("*.jinja2")])
        return builtin + sorted(self._custom_templates.keys())

    async def generate_with_template(self, session_id: str, template_name: str) -> Optional[str]:
        """使用指定模板生成报告。"""
        data = await self._fetch_session_data(session_id)
        if data is None:
            return None

        try:
            if template_name in self._custom_templates:
                from jinja2 import Template
                tmpl = Template(self._custom_templates[template_name])
            else:
                tmpl = self._env.get_template(template_name)

            output = tmpl.render(**data)

            ext = "md"
            if ".html" in template_name:
                ext = "html"
            elif ".pdf" in template_name:
                ext = "pdf"

            output_path = self._output_dir / f"report_{session_id}_{template_name.replace('.jinja2','')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
            output_path.write_text(output, encoding="utf-8")
            logger.info("[Report] 自定义模板报告已生成: %s", output_path)
            return str(output_path)
        except Exception as exc:
            logger.error("[Report] 自定义模板生成失败: %s", exc)
            return None
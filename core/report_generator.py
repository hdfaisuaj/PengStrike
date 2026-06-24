"""
报告生成器 (core/report_generator.py)
=========================================
结构化报告生成，支持多种格式：
- JSON (机器可读)
- HTML (人类可读，浏览器打开)
- PDF (预留接口，需安装 weasyprint 或 reportlab)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ★ 修复：添加 HTML 转义函数（防止 XSS）
def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符。"""
    if not isinstance(text, str):
        text = str(text)
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
    )


class ReportGenerator:
    """渗透测试报告生成器。"""

    def __init__(
        self,
        session_id: str,
        target: str,
        scan_data: Optional[Dict[str, Any]] = None,
        output_dir: str = "./data/reports",
    ):
        self.session_id = session_id
        self.target = target
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 报告数据
        self.stages: Dict[int, Any] = {}
        self.vuln_list: List[Dict[str, Any]] = []
        self.exploit_results: List[Dict[str, Any]] = []
        self.summary: Dict[str, Any] = {}

        # 如果提供了 scan_data，直接初始化字段
        if scan_data:
            self.stages = scan_data.get("stages", {})
            self.vuln_list = scan_data.get("vuln_list", [])
            self.exploit_results = scan_data.get("exploit_results", [])
            self.summary = scan_data.get("summary", {})

    def load_from_engine(self, engine) -> None:
        """从 AutoPilotEngine 实例加载数据。"""
        self.stages = engine.stage_results
        self.vuln_list = engine.vuln_list
        self.exploit_results = engine.exploit_results
        self.summary = {
            "vuln_count": len(self.vuln_list),
            "exploit_attempts": len(self.exploit_results),
            "successful_exploits": sum(
                1 for r in self.exploit_results if r.get("status") == "success"
            ),
        }

    def generate_json(self) -> str:
        """生成 JSON 报告。返回文件路径。"""
        report = self._build_report_data()
        file_path = self.output_dir / f"report_{self.session_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return str(file_path)

    def generate_html(self) -> str:
        """生成 HTML 报告。返回文件路径。"""
        report = self._build_report_data()
        html_content = self._render_html(report)
        file_path = self.output_dir / f"report_{self.session_id}.html"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        return str(file_path)

    def generate_pdf(self) -> str:
        """生成 PDF 报告（需要 weasyprint）。返回文件路径。"""
        try:
            from weasyprint import HTML
            html_path = self.generate_html()
            pdf_path = str(self.output_dir / f"report_{self.session_id}.pdf")
            HTML(filename=html_path).write_pdf(pdf_path)
            return pdf_path
        except ImportError:
            raise RuntimeError("生成 PDF 需要安装 weasyprint: pip install weasyprint")

    def _build_report_data(self) -> Dict[str, Any]:
        """构建报告数据字典。"""
        return {
            "session_id": self.session_id,
            "target": self.target,
            "scan_time": datetime.now().isoformat(),
            "stages": self.stages,
            "vuln_list": self.vuln_list,
            "exploit_results": self.exploit_results,
            "summary": self.summary,
        }

    def _render_html(self, report: Dict[str, Any]) -> str:
        """渲染 HTML 报告（已转义特殊字符）。"""
        vuln_rows = ""
        for v in report.get("vuln_list", []):
            vuln_rows += f"""
            <tr>
                <td>{_escape_html(v.get("name", ""))}</td>
                <td>{_escape_html(v.get("priority", ""))}</td>
                <td>{_escape_html(v.get("reason", ""))}</td>
            </tr>
            """

        exploit_rows = ""
        for e in report.get("exploit_results", []):
            exploit_rows += f"""
            <tr>
                <td>{_escape_html(e.get("vuln", ""))}</td>
                <td>{_escape_html(e.get("status", ""))}</td>
                <td>{_escape_html(e.get("plan", "")[:200])}</td>
            </tr>
            """

        return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>渗透测试报告 - {_escape_html(report["target"])}</title>
    <style>
        body {{ font-family: sans-serif; margin: 40px; }}
        h1 {{ color: #2c3e50; }}
        table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        .summary {{ background: #f9f9f9; padding: 15px; border-radius: 5px; }}
    </style>
</head>
<body>
    <h1>渗透测试报告</h1>
    <div class="summary">
        <p><strong>目标:</strong> {_escape_html(report["target"])}</p>
        <p><strong>时间:</strong> {_escape_html(report["scan_time"])}</p>
        <p><strong>会话 ID:</strong> {_escape_html(report["session_id"])}</p>
    </div>

    <h2>执行摘要</h2>
    <p>发现漏洞: {report["summary"].get("vuln_count", 0)} 个</p>
    <p>利用尝试: {report["summary"].get("exploit_attempts", 0)} 次</p>
    <p>成功利用: {report["summary"].get("successful_exploits", 0)} 次</p>

    <h2>漏洞列表</h2>
    <table>
        <tr><th>名称</th><th>优先级</th><th>原因</th></tr>
        {vuln_rows}
    </table>

    <h2>利用结果</h2>
    <table>
        <tr><th>漏洞</th><th>状态</th><th>方案</th></tr>
        {exploit_rows}
    </table>

    <hr>
    <p><em>由 PengStrike 自动生成</em></p>
</body>
</html>
        """


def generate_report(
    session_id: str,
    target: str,
    engine=None,
    formats: List[str] = None,
    output_dir: str = "./data/reports",
) -> Dict[str, str]:
    """
    便捷函数：生成报告（多种格式）。
    返回: {{"json": path, "html": path, ...}}
    """
    if formats is None:
        formats = ["json", "html"]

    gen = ReportGenerator(session_id, target, output_dir)
    if engine:
        gen.load_from_engine(engine)

    results = {}
    for fmt in formats:
        if fmt == "json":
            results["json"] = gen.generate_json()
        elif fmt == "html":
            results["html"] = gen.generate_html()
        elif fmt == "pdf":
            results["pdf"] = gen.generate_pdf()
        else:
            raise ValueError(f"不支持的格式: {fmt}")

    return results

"""WPScan WordPress 安全扫描工具"""
from __future__ import annotations
import json, re
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class WpscanTool(BaseTool):
    metadata = ToolMetadata(
        name="wpscan",
        description="WPScan WordPress 安全扫描器，检测插件/主题/用户/漏洞。",
        category=ToolCategory.VULN_SCAN,
        tags=["wpscan", "wordpress", "web", "cms", "vulnerability"],
        version="3.8",
        references=["https://wpscan.com/"],
        parameters=[
            ToolParameter(name="url", type="string", description="WordPress 站点 URL", required=True),
            ToolParameter(name="enumerate", type="string", description="枚举内容: vp/vt/u/m (插件/主题/用户/媒体)", default="vp,vt,u"),
            ToolParameter(name="plugins_detection", type="string", description="插件检测模式: passive/aggressive/mixed", default="passive"),
            ToolParameter(name="enumerate_all", type="boolean", description="枚举所有插件", default=False),
            ToolParameter(name="api_token", type="string", description="WPScan API Token（获取漏洞详情）", default=""),
            ToolParameter(name="batch", type="boolean", description="批处理模式", default=True),
            ToolParameter(name="output_format", type="string", description="输出格式: json/cli", default="json"),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["wpscan", "--url", kwargs.get("url", "")]
        enumerations = kwargs.get("enumerate", "vp,vt,u")
        if enumerations:
            cmd.extend(["--enumerate", enumerations])
        if kwargs.get("enumerate_all"):
            cmd.append("--enumerate-all-plugins")
        if kwargs.get("plugins_detection"):
            cmd.extend(["--plugins-detection", kwargs["plugins_detection"]])
        token = kwargs.get("api_token", "")
        if token:
            cmd.extend(["--api-token", token])
        if kwargs.get("batch"):
            cmd.append("--batch")
        if kwargs.get("output_format") == "json":
            cmd.extend(["--format", "json"])
        extra = kwargs.get("extra_args", "")
        if extra:
            import shlex
            cmd.extend(shlex.split(extra))
        # ★ 统一通过 _run_cmd 执行，走安全链（注入检查/高危关键词拦截/超时管理）
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        """解析 wpscan 输出，优先解析 JSON 格式（--format json），失败则降级为文本解析。"""
        # ★ 优先尝试 JSON 解析
        if raw_output.strip().startswith("{"):
            try:
                data = json.loads(raw_output)
                result: Dict[str, Any] = {
                    "version": data.get("version", {}),
                    "plugins": [],
                    "themes": [],
                    "users": [],
                    "vulnerabilities": [],
                    "interesting_entries": [],
                }
                # 解析插件
                for name, info in (data.get("plugins") or {}).items():
                    plugin = {"name": name, "slug": info.get("slug", name), "location": info.get("location", "")}
                    vulns = info.get("vulnerabilities") or []
                    plugin["vulnerabilities"] = [{"cve": v.get("cve"), "title": v.get("title"), "cvss": v.get("cvss")} for v in vulns]
                    result["plugins"].append(plugin)
                # 解析主题
                for name, info in (data.get("themes") or {}).items():
                    theme = {"name": name, "slug": info.get("slug", name)}
                    vulns = info.get("vulnerabilities") or []
                    theme["vulnerabilities"] = [{"cve": v.get("cve"), "title": v.get("title"), "cvss": v.get("cvss")} for v in vulns]
                    result["themes"].append(theme)
                # 解析用户
                for u in (data.get("users") or []):
                    result["users"].append({"username": u.get("username", ""), "id": u.get("id"), "comment_author": u.get("comment_author", "")})
                # 主版本漏洞
                for v in (data.get("version", {}).get("vulnerabilities") or []):
                    result["vulnerabilities"].append({"type": "core", "cve": v.get("cve"), "title": v.get("title"), "cvss": v.get("cvss")})
                return result
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # JSON 解析失败，降级为文本解析

        # ★ 降级：文本行解析（兼容无 --format json 的情况）
        plugins, themes, users, vulns = [], [], [], []
        for line in raw_output.splitlines():
            if "Name:" in line and "Plugin" not in line:
                import re as _re
                m = _re.search(r"Name:\s+(.+)", line)
                if m and "theme" not in line.lower():
                    plugins.append(m.group(1).strip())
            if "Theme" in line:
                import re as _re
                m = _re.search(r"Name:\s+(.+)", line)
                if m:
                    themes.append(m.group(1).strip())
            if "Username:" in line:
                import re as _re
                m = _re.search(r"Username:\s+(.+)", line)
                if m:
                    users.append(m.group(1).strip())
        return {"plugins": plugins[:50], "themes": themes[:20], "users": users[:20], "vulnerabilities": vulns}

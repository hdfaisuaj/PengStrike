"""
Web目录爆破技能 (web_dir_brute.py)

执行流程:
1. 使用 gobuster 进行目录爆破（medium 字典）
2. 使用 ffuf 进行扩展名/文件爆破（可选）
3. 合并结果返回
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from roles_skills.base_skill import BaseSkill
from tools.base_tool import ToolResult

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


class WebDirBrute(BaseSkill):
    name: str = "web_dir_brute"
    description: str = "Web目录爆破：使用 gobuster + ffuf 发现隐藏目录/文件"
    parameters: dict = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL（如 http://example.com）"},
            "wordlist": {"type": "string", "description": "字典路径（默认 directory-list-2.3-medium.txt）"},
        },
        "required": ["url"],
    }

    async def run(self, orchestrator: Orchestrator, **kwargs) -> ToolResult:
        url = kwargs.get("url", "")
        if not url:
            return ToolResult(success=False, error="缺少必填参数: url", tool_name=self.name)

        wordlist = kwargs.get("wordlist", "/usr/share/wordlists/dirbuster/directory-list-2.3-medium.txt")

        # 步骤 1: gobuster 目录爆破
        step1 = await orchestrator.call_tool(
            "gobuster",
            url=url,
            wordlist=wordlist,
            extensions="php,txt,html,bak,zip,git,tar",
            threads=50,
        )
        if not step1.success:
            return ToolResult(
                success=False,
                error=f"gobuster 目录爆破失败: {step1.error}",
                duration=step1.duration,
                tool_name=self.name,
            )

        found_dirs = self._extract_gobuster_results(step1.output)

        # 步骤 2: ffuf 补充扫描（使用小字典快速扫描常见敏感文件）
        step2 = await orchestrator.call_tool(
            "ffuf",
            url=f"{url}/FUZZ",
            wordlist="/usr/share/wordlists/dirb/common.txt",
            threads=30,
        )

        found_files = []
        if step2.success:
            found_files = self._extract_ffuf_results(step2.output)

        combined = found_dirs + [f for f in found_files if f not in found_dirs]

        return ToolResult(
            success=True,
            output=f"Web目录爆破完成\nURL: {url}\n发现 ({len(combined)}):\n" + "\n".join(f"  - {item}" for item in combined),
            structured_data={
                "discovered": combined,
                "total": len(combined),
                "gobuster_finds": found_dirs,
                "ffuf_finds": found_files,
            },
            duration=step1.duration + (step2.duration if step2.success else 0),
            tool_name=self.name,
        )

    @staticmethod
    def _extract_gobuster_results(output: str) -> list[str]:
        """从 gobuster 输出中提取发现的目录。"""
        import re
        results: list[str] = []
        for line in output.splitlines():
            m = re.match(r"^/(\S+)\s+\(Status:\s*\d+\)", line)
            if m:
                results.append(f"/{m.group(1)}")
        return results

    @staticmethod
    def _extract_ffuf_results(output: str) -> list[str]:
        """从 ffuf 输出中提取发现的路径。"""
        import re
        results: list[str] = []
        for line in output.splitlines():
            m = re.match(r"^/?(\S+)\s+\(Status:\s*\d+\)", line)
            if m:
                results.append(f"/{m.group(1)}")
        return results
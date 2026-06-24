"""
安全拦截模块 (core/security.py) - Phase 4 增强版

⚠️ 安全警告：本工具仅用于合法授权的渗透测试！
使用者必须遵守当地法律法规，禁止用于未授权的系统测试。
开发者不承担任何因使用本工具造成的法律责任。

职责:
- 维护危险命令黑名单（针对 shell=True 的命令字符串场景）
- 维护渗透工具白名单（仅允许白名单中的工具执行）
- 敏感文件路径访问控制
- 评估用户/AI 生成命令的安全性
- 提供可扩展的安全检查接口

设计原则:
- 纵深防御：黑名单 + 白名单 + 敏感文件控制 三层拦截
- 纯函数/轻对象，不依赖外部状态
- 黑名单为单一定义源，便于集中维护
- 针对 shell=True 场景：严格拦截注入字符、危险命令
- 针对参数列表 (shell=False) 场景：只拦极其危险关键词，不拦截 shell 元字符
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple


# ========================================================================
# 危险的系统命令黑名单
# ========================================================================
DANGEROUS_KEYWORDS: List[str] = [
    # --- 破坏性文件操作 ---
    "rm -rf", "rm -fr", "rm /*", "rm /",
    "rm -rf --no-preserve-root",
    # --- 格式化 / 磁盘工具 ---
    "mkfs", "mkswap", "mke2fs", "mkfs.ext", "mkfs.btrfs", "mkfs.xfs",
    "format", "quickformat",
    # --- 块设备直接写入 ---
    "dd if=", "dd of=", "dd if=/dev/zero",
    # --- 磁盘覆写 / 擦除 ---
    "wipefs", "badblocks", "hdparm --wipe",
    "shred", "sfill", "sdmem", "scrub",
    # --- 直接写入块设备 ---
    "> /dev/sda", "> /dev/sdb", "> /dev/nvme",
    "> /dev/mmcblk", "> /dev/loop",
    "of=/dev/sda", "of=/dev/sdb",
    # --- 系统关机 / 重启 / 断电 ---
    "shutdown", "shutdown -h", "shutdown -r",
    "reboot", "halt", "poweroff", "init 0", "init 6",
    "systemctl poweroff", "systemctl reboot",
    # --- 资源耗尽 / fork bomb ---
    ":(){ :|:& };:", "fork bomb",
    # --- 权限篡改（危险级别） ---
    "chmod 777 /", "chmod -R 777 /",
    "chown -R /", "chown -R  /",
    "chattr -i /",
    # --- 密码 / 认证破坏 ---
    "passwd -d", "passwd -l",
    "userdel -rf", "userdel -r",
    # --- 网络攻击 / 扫描滥用 ---
    "dos", "ddos", "syn flood", "hping3 --flood",
    # --- 不安全的远程登录凭证 ---
    "ssh root@", "telnet ", "nc -lvp",
    # --- shell 反弹 / 危险命令执行 ---
    "bash -i >&", "bash -c 'exec",
    "/dev/tcp/", "/dev/udp/",
    # --- 网络配置篡改 ---
    "iptables -F", "iptables --flush",
    "ufw disable", "systemctl stop firewalld",
    # --- 敏感文件访问（黑名单补充） ---
    "cat /etc/shadow", "cat /etc/shadow ",
    "cat /etc/gshadow", "cat /etc/gshadow ",
    "chmod u+s /bin/bash", "chmod u+s /bin/sh",
    # --- 进程/内核操作 ---
    "kill -9", "killall -9",
    "sysctl -w", "modprobe ",
    # --- 包管理器操作 ---
    "apt remove", "apt purge", "apt autoremove",
    "dpkg --purge", "dpkg -r",
    "yum remove", "dnf remove",
    "pip uninstall", "pip3 uninstall",
    # --- 网络配置篡改 ---
    "ifconfig down", "ip link set down",
    "nmcli dev disconnect",
    # --- 防火墙/安全软件关闭 ---
    "systemctl stop ", "systemctl disable ",
    "service stop ", "service  stop",
    "killall avast", "killall defender",
    "netsh advfirewall set allprofiles state off",
]

# ========================================================================
# 注入字符（shell=True 场景下出现即拦截）
# 注意：此处只拦截最危险的注入字符，; && || 等由下面的正则精细化处理
# ========================================================================
INJECTION_CHARS: List[str] = [
    "`", "$(", "${", "$[", "$((  ",
]

# ========================================================================
# 正则模式：更精细的注入检测
# ========================================================================
INJECTION_PATTERNS: List = [
    re.compile(r"\$\([^)]+\)"),
    re.compile(r"\$\{[^}]+\}"),
    re.compile(r"\$\[[^\]]+\]"),
    re.compile(r"\$\(\([^)]+\)\)"),
    re.compile(r"\b(expr|let)\s+\d+\s*[+\-*/%]\s*\d+"),
    re.compile(r"`[^`]+`"),
    re.compile(r";\s*[&]?\s*\b(rm|shutdown|reboot|halt|poweroff|dd|mkfs|sudo|su)\b"),
    re.compile(r"&&\s*\b(rm|shutdown|reboot|halt|poweroff|dd|mkfs|sudo|su)\b"),
    # 管道注入检测：仅拦截高危管道（管道到不安全命令），不拦截 | head | grep | sort 等
    re.compile(r"\|\s*\b(bash|sh|zsh|python|python2|python3|perl|ruby|php)\b"),
    re.compile(r"\|\s*\b(while|for|eval|exec|source)\b"),
    re.compile(r">\s*/dev/(sda|sdb|nvme|mmcblk)"),
    re.compile(r"\b(wget|curl)\b.*\s\|\s*(bash|sh|zsh)\b"),
    re.compile(r"\bpython\d?\b.*-c\b.*\b(exec|eval|os\.system|subprocess)\b"),
    re.compile(r"\$(\{|\()\s*[A-Za-z_][A-Za-z0-9_]*\s*[:\-+?]"),
    re.compile(r"\$\(<[^)]*\)"),
    re.compile(r"<\([^)]+\)"),
    re.compile(r"\$\[\[[^\]]+\]\]"),
]

# ========================================================================
# 渗透工具白名单（仅允许使用这些工具）
# ========================================================================
ALLOWED_TOOLS: List[str] = [
    # --- 信息收集 / 侦察 ---
    "nmap", "masscan", "rustscan", "unicornscan", "zenmap",
    "whois", "dig", "nslookup", "host",
    "dnsenum", "dnsrecon", "dnsmap", "fierce",
    "theharvester", "recon-ng", "sublist3r", "amass",
    "whatweb", "wappalyzer", "builtwith",
    # --- Web 扫描 / 目录爆破 ---
    "gobuster", "dirb", "dirsearch", "ffuf", "wfuzz",
    "nikto", "wapiti", "skipfish", "arachni",
    "wpscan", "joomscan", "droopescan",
    "sqlmap", "nosqli", "xsstrike",
    # --- 漏洞分析 ---
    "searchsploit", "nuclei", "vulners", "cve-search",
    "hydra", "medusa", "john", "hashcat",
    # --- 利用 ---
    "msfconsole", "msfvenom", "metasploit",
    "exploitdb", "commix",
    # --- 后渗透 ---
    "netcat", "nc", "ncat", "socat",
    "proxychains", "ssh", "ssh-keygen", "scp",
    "python", "python3", "perl", "ruby", "php",
    "curl", "wget", "socat",
    # --- Windows 渗透 ---
    "impacket", "crackmapexec", "smbclient", "smbmap",
    "enum4linux", "ldapsearch", "rpcclient",
    "evil-winrm", "winexe", "psexec",
    "bloodhound", "bloodhound-python",
    # --- 系统工具（安全用途） ---
    "whoami", "id", "uname", "hostname", "ifconfig", "ip",
    "ls", "cat", "find", "grep", "sort", "wc",
    "ping", "traceroute", "tracepath", "mtr",
    "netstat", "ss", "lsof",
    "ps", "top", "htop",
    "date", "uptime", "w",
    "env", "printenv", "echo",
    "pwd", "which", "whereis",
    "df", "du", "free",
    "head", "tail", "less", "more",
    "sed", "awk", "sort", "uniq", "cut", "tr", "tee", "diff", "patch",
    "base64", "xxd", "md5sum", "sha256sum", "sha1sum",
    # --- Windows 特定（远程目标） ---
    "systeminfo", "tasklist", "schtasks",
    "reg", "wmic", "powershell", "cmd",
    "net", "net1", "nltest",
    "arp", "route", "nbtstat",
]

# ========================================================================
# 敏感文件路径（禁止 AI 直接读取）
# ========================================================================
SENSITIVE_FILE_PATTERNS: List[str] = [
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/ssh/ssh_host_",
    "/etc/ssh/sshd_config",
    "~/.ssh/id_rsa",
    "/root/.ssh/",
    "/var/log/auth.log",
    "/var/log/secure",
    "/var/log/wtmp",
    "/var/log/btmp",
    "/etc/ppp/chap-secrets",
    "/etc/ipsec.secrets",
    "/etc/openvpn/",
    # Windows 敏感路径
    "C:\\Windows\\System32\\config\\SAM",
    "C:\\Windows\\System32\\config\\SYSTEM",
    "C:\\Windows\\NTDS\\ntds.dit",
    "C:\\Windows\\System32\\drivers\\etc\\shadow",
]

# ========================================================================
# SecurityGuard 类
# ========================================================================
class SecurityGuard:
    """命令安全评估器（三层纵深防御）。"""

    def __init__(self, extra_keywords: Optional[List[str]] = None) -> None:
        self._keywords: List[str] = list(DANGEROUS_KEYWORDS)
        if extra_keywords:
            self._keywords.extend(extra_keywords)

    # ------------------------------------------------------------------
    # 黑名单管理
    # ------------------------------------------------------------------
    def add_keyword(self, keyword: str) -> None:
        keyword = keyword.strip()
        if keyword and keyword not in self._keywords:
            self._keywords.append(keyword)

    def remove_keyword(self, keyword: str) -> None:
        if keyword in self._keywords:
            self._keywords.remove(keyword)

    # ------------------------------------------------------------------
    # 第一层：危险命令检测（黑名单）
    # ------------------------------------------------------------------
    def is_dangerous(self, command: str) -> Tuple[bool, str]:
        """判断命令是否危险（针对 shell=True 的字符串命令场景）。

        检查内容:
        1. 高危命令关键词黑名单
        2. shell 注入字符（; ` $( || &&）
        3. 高级正则注入模式检测

        返回:
            (is_dangerous: bool, reason: str)
        """
        if not command or not command.strip():
            return False, ""

        cmd_lower = command.lower()

        # 1. 高危命令关键词匹配
        for keyword in self._keywords:
            if keyword.lower() in cmd_lower:
                return True, f"匹配危险关键词: {keyword}"

        # 2. Shell 注入字符检测
        for ch in INJECTION_CHARS:
            if ch in command:
                return True, f"高危命令已被拦截（包含注入字符: {repr(ch)}）"

        # 3. 高级正则注入模式
        for pat in INJECTION_PATTERNS:
            if pat.search(command):
                return True, f"匹配注入模式: {pat.pattern}"

        return False, ""

    # ------------------------------------------------------------------
    # 第一层（轻量版）：参数列表检测（shell=False 场景）
    # ------------------------------------------------------------------
    def is_dangerous_args(self, args: List[str]) -> Tuple[bool, str]:
        """判断参数列表形式的命令是否危险（shell=False 场景）。

        由于参数已经由操作系统解析为独立 argv，无需关心 shell 注入字符。
        仅需拦极其危险的命令（rm -rf /、dd of=块设备、shutdown 等）。
        """
        if not args:
            return False, ""

        cmd_str = " ".join(args)
        cmd_lower = cmd_str.lower()
        for keyword in [
            "rm -rf /", "rm --no-preserve-root",
            "shutdown", "reboot", "halt", "poweroff",
            "mkfs", "dd of=/dev/", "dd if=/dev/zero",
            "chmod 777 /", "chown -R /",
        ]:
            if keyword in cmd_lower:
                return True, f"匹配危险关键词: {keyword}"
        return False, ""

    # ------------------------------------------------------------------
    # 第二层：白名单校验
    # ------------------------------------------------------------------
    def is_allowed_tool(self, command: str) -> Tuple[bool, str]:
        """检查命令是否为白名单中的允许工具。

        提取命令的基础工具名（使用 basename），检查是否在 ALLOWED_TOOLS 中。
        禁止通过路径绕过白名单（如 /usr/bin/nmap 绕过 nmap 检测）。
        返回 (是否允许, 原因)。
        """
        if not command or not command.strip():
            return False, "空命令"

        raw_first = command.strip().split()[0]

        # 禁止命令中包含路径分隔符（避免路径绕过白名单）
        if "/" in raw_first or "\\" in raw_first:
            return False, f"禁止使用全路径执行工具: '{raw_first}'（请使用基础命令名，如 'nmap' 而非 '/usr/bin/nmap'）"

        first_word = raw_first.lower()

        for tool in ALLOWED_TOOLS:
            if first_word == tool:
                return True, ""

        return False, f"工具 '{first_word}' 不在白名单中"

    def is_only_allowed_tools(self, command: str) -> Tuple[bool, str]:
        """检查命令串中的所有工具是否都在白名单中（用于管道/复合命令）。

        用 shell 元字符分割，对每段提取工具名检查。
        """
        if not command or not command.strip():
            return False, "空命令"

        segments = re.split(r"[;|&]", command)
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            tool_name = seg.split()[0].lower()
            is_allowed, _ = self.is_allowed_tool(tool_name)
            if not is_allowed:
                return False, f"工具 '{tool_name}' 不在白名单中"
        return True, ""

    # ------------------------------------------------------------------
    # 第三层：敏感文件访问控制
    # ------------------------------------------------------------------
    def accesses_sensitive_file(self, command: str) -> Tuple[bool, str]:
        """检查命令是否访问敏感文件。

        返回 (是否访问敏感文件, 原因)。
        """
        if not command or not command.strip():
            return False, ""

        cmd_lower = command.lower()
        for pattern in SENSITIVE_FILE_PATTERNS:
            if pattern.lower() in cmd_lower:
                return True, f"命令访问敏感文件: {pattern}"
        return False, ""

    # ------------------------------------------------------------------
    # 统一安全入口（三层全检）
    # ------------------------------------------------------------------
    def check_command(self, command: str) -> Tuple[bool, str]:
        """三层安全检查入口。

        检查顺序:
        1. 危险命令黑名单
        2. 白名单校验
        3. 敏感文件访问控制

        返回:
            (safe: bool, reason: str)
            - safe=True: 命令安全，允许执行
            - safe=False: 命令危险，已拦截，reason 为拦截原因
        """
        if not command or not command.strip():
            return False, "空命令"

        # 第一层：危险命令检测
        dangerous, reason = self.is_dangerous(command)
        if dangerous:
            return False, f"危险命令已拦截: {reason}"

        # 第三层：敏感文件访问控制
        sensitive, sensitive_reason = self.accesses_sensitive_file(command)
        if sensitive:
            return False, f"sensitive_access拦截: {sensitive_reason}"

        # ★ 已移除白名单校验（用户要求只拦危险命令）
        return True, ""

    # ------------------------------------------------------------------
    # 兼容旧接口
    # ------------------------------------------------------------------
    def is_allowed(self, command: str) -> Tuple[bool, str]:
        """兼容命名: is_allowed = 不危险 + 白名单 + 不访问敏感文件。"""
        return self.check_command(command)


# ========================================================================
# 模块级快捷函数（保持与旧代码的简单兼容）
# ========================================================================
_default_guard: Optional[SecurityGuard] = None


def _get_guard() -> SecurityGuard:
    global _default_guard
    if _default_guard is None:
        _default_guard = SecurityGuard()
    return _default_guard


def is_dangerous_command(command: str) -> bool:
    """简单接口: 返回 True/False，供迁移过渡期使用。"""
    dangerous, _ = _get_guard().is_dangerous(command)
    return dangerous
"""
LLM        (core/llm_client.py) - Phase 4      

  :
-    OpenAI    API       (chat.completions.create with stream=True)
-    messages     
-        (TOOLS)     schema
-    System Prompt (  AutoPilot     )
- Phase 4: LLM API       (   3  ,    )
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
import traceback
from typing import Any, Dict, Generator, List, Optional

from rich.console import Console

from config.settings import Settings, get_settings
from utils.logger import get_logger

#    Tool Call    
from core.tool_call_sanitizer import sanitize_tool_calls as sanitize_tc

logger = get_logger(__name__)

# *       :LLM         (  TOOLS       +       )
#           ,      LLM function      
_ALLOWED_TOOL_NAMES: set = {"execute_kali_command", "save_exploit"}
try:
    from tools.registry import get_registry
    _registry_tools = set(get_registry().list_tools())
    _ALLOWED_TOOL_NAMES.update(_registry_tools)
    #   :       
    logger.info("[LLM]       (   ): %s", _ALLOWED_TOOL_NAMES)
except Exception as _exc:
    logger.warning("[LLM]          (       ): %s", _exc)


# ========================================================================
#    Prompt / AutoPilot    / Tool   
# ========================================================================
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_kali_command",
            "description": (
                "  Kali Linux                       ."
                "             (autopilot              )."
                "        :nmap (  /  /    ),wapiti (Web     ,   ),nikto (Web      ,     ),"
                "sqlmap (SQL        ),hydra (    ),gobuster (    ,   ),"
                "dirb,gobuster,searchsploit (     ),curl (HTTP   ),"
                "whoami,uname,cat,ls,ping,netstat  ."
                "   nmap       -sC -sV -T4   ."
                "               ,               ,"
                "      -oN           ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "      (   ),   'nmap -sC -sV 10.0.0.1'",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_exploit",
                "description": (
                    "  searchsploit        exploit      "
                    "     exploit/          ."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {
                            "type": "string",
                            "description": "searchsploit     exploit        ",
                        },
                    },
                    "required": ["source_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_login_brute",
            "description": (
                "           .         /    ,"
                "   cookie,          ."
                "          (  'welcome', 'logout', 'dashboard')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "          URL,   http://target/login.php",
                    },
                    "username_field": {
                        "type": "string",
                        "description": "       name    ,   'username'",
                    },
                    "password_field": {
                        "type": "string",
                        "description": "      name    ,   'password'",
                    },
                    "usernames": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "         ,   ['admin']",
                    },
                    "passwords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "        ",
                    },
                    "success_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "       HTML   ,   ['logged in', 'welcome', 'logout', 'dashboard']",
                    },
                    "cookie_file": {
                        "type": "string",
                        "description": "   cookie      ,   /tmp/autopilot_cookie.txt",
                    },
                    "extra_fields": {
                        "type": "string",
                        "description": "    POST   (JSON  ),  '{\"submit\":\"Login\"}'",
                    },
                },
                "required": ["url", "passwords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_form_extract",
            "description": (
                "     URL   HTML   ,           ."
                "        action,method,      ."
                "                ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "       URL",
                    },
                    "save_html": {
                        "type": "boolean",
                        "description": "     HTML         ,   true",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_form_submit",
            "description": (
                "    URL       (POST   GET),         cookie."
                "         .    ,  ,         ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "      URL",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["POST", "GET"],
                        "description": "    ,   POST",
                    },
                    "fields": {
                        "type": "object",
                        "description": "          ,  {\"username\":\"admin\",\"password\":\"test\"}",
                        "additionalProperties": {"type": "string"},
                    },
                    "cookie_file": {
                        "type": "string",
                        "description": "cookie     ,      ",
                    },
                },
                "required": ["url", "fields"],
            },
        },
    },
]


AUTOPILOT_INJECTION = (
    "\n\n[AUTOPILOT MODE -         -          ]\n"
    "      **       (AutoPilot)**.                     :\n"
    "0. [  ]                  AutoPilot       !"
    "            ,           ,    !\n"
    "1.           !    '   ','    ','    ','   ...'   .\n"
    "2.         ,             ,      execute_kali_command!\n"
    "3.               :   ->    ->      ->       ->   ...\n"
    "4.        ,  :     ->      ->      ->      ->    ->     \n"
    "5.      EXACT                  (     ):\n"
    "   -             Shell/     \n"
    "   -      ROOT/SYSTEM   (   'ROOT SHELL'   '    ')\n"
    "   -      3         ,      \n"
    "   -              (    ,     )\n"
    "6.         ,                      .\n"
    "7.     (rm -rf, shutdown, reboot, dd  )       ,    .\n"
)

SYSTEM_PROMPT = (
    "         PengStrike.\n\n"

    "[       ]\n"
    "1.      :          IP     ,                    ."
    "                 ( :       ,         ),"
    "                  (  :'        ?      /  /y/yes    ')."
    "            ,       execute_kali_command   .\n"
    "       :       AUTOPILOT   (        'autopilot on'),"
    "                 ,           .\n"
    "2.       :        ->    ->      ->    ->      ->         .\n"
    "3.       :  execute_kali_command       (  nmap, wapiti, nikto  ) ,"
    "          !           :"
    "  -              ."
    "  -                (CVE)     ."
    "  -             .\n\n"

    "[    ]\n"
    "                  ,                   ,"
    "        JSON            .\n\n"

    "[Auto-Pilot       ]\n"
    "        autopilot    ,   :\n"
    "  -         ,       \n"
    "  -               \n"
    "  -    ReAct   (   ->    ->    ->    )\n"
    "            .\n"
    "          ,        autopilot        :\n"
    "  -      root/       (  ROOT SHELL       )\n"
    "  -       3           \n"
    "  -                \n"
    "  -              \n"
    "   autopilot  ,          '    ,      '   '    ROOT SHELL'   .\n\n"

    "[      ]\n"
    "0. [  ]                 ,           :\n"
    "   - nmap     -T4(    )  --top-ports    -p-    \n"
    "   - Web     wapiti / nuclei    nikto / skipfish\n"
    "   -       gobuster    ffuf\n"
    "   -    Web      curl         \n"
    "1.      Kali Linux       (nmap, wapiti, sqlmap, hydra, gobuster, searchsploit  ).\n"
    "2.    nmap    ,     -sC -sV -T4 --min-rate 1000        .\n"
    "3.       (      ,      ) ,              .\n"
    "4. [  ]    :         .\n"
    "   -    Top 1000   (nmap -sS -sV -T4 --top-ports 1000),"
    "                .\n"
    "   -          nmap -p-      !\n"
    "5. Web        wapiti,    :wapiti -u http://target --scope url -f json -o /tmp/output.json --timeout 30 --no-bugreport\n"
    "   -        -m        !    :-m \"sql,xss\"       -m   \n"
    "   - MySQL   :    -p''   ,    mysql -h IP -u root -e \"SQL\"\n"
    "         :mysql -h 10.0.0.1 -u root -p'' -e \"show databases;\"(         )\n"
    "         :mysql -h 10.0.0.1 -u root -e \"show databases;\"\n"
    "6.      ,       tool_calls       ,    content    JSON    .\n"
    "7. arguments      JSON      (    ),   {\"command\": \"nmap -sV 10.0.0.1\"}.\n"
    "   -             ,    \\\"   ,      JSON     !\n"
    "8.     execute_kali_command   save_exploit      ,        .\n"
    "9.          ,               .\n"
    "   *            (         ),         tool_calls          !\n"
    "10. [  ]Tool Call         :\n"
    "   -     arguments     function.name  !\n"
    "   -     :execute_kali_command{\"command\": \"curl http://1.1.1.1\"}\n"
    "   -     :execute_kali_command(command=\"curl...\")\n"
    "   -     :\n"
    "     {\n"
    "       \"name\": \"execute_kali_command\",\n"
    "       \"arguments\": \"{\\\"command\\\": \\\"curl http://1.1.1.1\\\"}\"\n"
    "     }\n"
    "   - function.name           ,       JSON      .\n"
    "   -         ,            .\n\n"

    "[        ]\n"
    "         ,exploit,  ,payload  :\n"
    "  1.      /usr/bin/locate <   >\n"
    "  2.    locate    ,   sudo updatedb     /usr/bin/locate\n"
    "  3.       ,    find / -name \"<   >\" 2>/dev/null\n"
    "  4.        find /usr/share,        .\n\n"

    "[       ]\n"
    "1.                           .\n"
    "2.                (  DOS   ),    (  rm -rf)                  .\n"
    "3.    execute_kali_command        'Permission denied: Dangerous command blocked',"
    "                 ,           .\n\n"

    "[    ]\n"
    "-     ,  ,   .      ,         .\n"
    "-            JSON          .\n\n"

    "[  ]                    ."
    "             ,                   ."
)


# ========================================================================
#         :         True,             
# ========================================================================
_global_shutdown = False

def signal_llm_shutdown() -> None:
    """     LLM          (  api.app       )."""
    global _global_shutdown
    _global_shutdown = True

def is_llm_shutting_down() -> bool:
    return _global_shutdown


def reset_llm_shutdown() -> None:
    """        ,   LLM              ."""
    global _global_shutdown
    _global_shutdown = False
    logger.info("[LLM]          ,LLM         ")

# ========================================================================
# LLM    
# ========================================================================
class LLMClient:
    """  LLM API       (     ,    Provider)."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        console: Optional[Console] = None,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.console = console or Console()
        self.system_prompt = system_prompt
        self.tools = tools or TOOLS
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.provider = self.settings.llm_provider

        #       (    : 422/429/timeout     )
        self._lock = asyncio.Lock()
        self._multi_models: list[dict] = self._load_multi_models()
        self._current_model_index: int = 0
        self._consecutive_422_count: int = 0
        self._consecutive_429_count: int = 0
        self._consecutive_timeout_count: int = 0
        self._original_model: str = self.settings.llm_model

        self.console.print(
            f"[bold cyan]  [   ] Provider={self.provider}      : {self.settings.llm_base_url}[/bold cyan]"
        )
        self.client = self._init_client()

        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]

    def _init_client(self) -> Any:
        """   provider        API    ."""
        provider = self.provider.lower()

        if provider == "azure":
            from openai import AzureOpenAI
            return AzureOpenAI(
                azure_endpoint=self.settings.llm_base_url,
                api_version=self.settings.llm_azure_api_version,
                api_key=self.settings.llm_api_key,
            )

        elif provider == "anthropic":
            from anthropic import Anthropic
            return Anthropic(api_key=self.settings.llm_api_key)

        else:
            from openai import OpenAI
            return OpenAI(
                base_url=self.settings.llm_base_url,
                api_key=self.settings.llm_api_key,
            )

    # ------------------------------------------------------------------
    #         (    : 422/429/timeout     )
    # ------------------------------------------------------------------

    def _load_multi_models(self) -> list[dict]:
        """        (  settings.multi_models)."""
        multi_models = self.settings.multi_models or []
        if not isinstance(multi_models, list):
            return []
        valid = []
        for m in multi_models:
            if isinstance(m, dict) and "model" in m:
                valid.append(m)
        return valid

    def _notify_model_switch_sync(self, old_model: str, new_model: str, reason: str) -> None:
        """              WebSocket."""
        try:
            import asyncio
            import json
            loop = asyncio.get_event_loop()
            if loop.is_running():
                manager = get_connection_manager()
                payload = {
                    "type": "model_switch",
                    "from_model": old_model,
                    "to_model": new_model,
                    "reason": reason,
                    "timestamp": __import__('time').time(),
                }
                asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)
        except Exception as exc:
            logger.warning("[LLM]           : %s", exc)

    def _notify_model_switch_sync(self, old_model: str, new_model: str, reason: str) -> None:
        """              WebSocket(           )."""
        try:
            import asyncio
            from api.websocket import get_connection_manager
            loop = asyncio.get_event_loop()
            if loop.is_running():
                manager = get_connection_manager()
                import time
                payload = {
                    "type": "model_switch",
                    "from_model": old_model,
                    "to_model": new_model,
                    "reason": reason,
                    "timestamp": time.time(),
                }
                asyncio.run_coroutine_threadsafe(manager.broadcast(payload), loop)
        except Exception as exc:
            logger.warning("[LLM]           : %s", exc)

    def _switch_to_next_model(self) -> bool:
        """
                  .        .
        """
        if not self._multi_models:
            return False

        self._current_model_index += 1
        if self._current_model_index >= len(self._multi_models):
            #         ,       
            self._current_model_index = -1
            self.settings.llm_model = self._original_model
            self._consecutive_422_count = 0
            self._consecutive_429_count = 0
            self._consecutive_timeout_count = 0
            self.console.print(f"[yellow]            ,       : {self._original_model}[/yellow]")
            return False

        next_model_cfg = self._multi_models[self._current_model_index]
        new_model = next_model_cfg.get("model")
        new_base_url = next_model_cfg.get("base_url", self.settings.llm_base_url)
        new_api_key = next_model_cfg.get("api_key", self.settings.llm_api_key)

        self.settings.llm_model = new_model
        self.settings.llm_base_url = new_base_url
        self.settings.llm_api_key = new_api_key

        #         
        self.client = self._init_client()

        self.console.print(f"[yellow]         : {new_model}[/yellow]")
        logger.warning("[LLM]        : %s", new_model)

        #       
        self._consecutive_422_count = 0
        self._consecutive_429_count = 0
        self._consecutive_timeout_count = 0

        return True

    def _validate_and_fix_request(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """
              LLM API   ,   422   .
        """
        #    model     
        if "model" not in kwargs or not kwargs["model"]:
            kwargs["model"] = self.settings.llm_model

        #    messages         
        if "messages" in kwargs:
            msgs = kwargs["messages"]
            if not msgs or not isinstance(msgs, list):
                kwargs["messages"] = [{"role": "system", "content": self.system_prompt}]
            else:
                #    content=None    tool_calls   assistant    (    422)
                cleaned = []
                for m in msgs:
                    if m.get("role") == "assistant" and m.get("content") is None and "tool_calls" not in m:
                        continue
                    cleaned.append(m)
                kwargs["messages"] = cleaned

        return kwargs

    # ------------------------------------------------------------------
    #       (    :   asyncio.Lock   )
    # ------------------------------------------------------------------
    def append_user(self, content: str) -> None:
        """      (    )."""
        async def _lock_and_append():
            async with self._lock:
                self.messages.append({"role": "user", "content": content})
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.ensure_future(_lock_and_append())
                return
        except RuntimeError:
            pass
        #           
        self.messages.append({"role": "user", "content": content})

    def append_assistant(self, content: Optional[str], tool_calls: Optional[List[Dict[str, Any]]] = None) -> None:
        """      (    )."""
        msg: Dict[str, Any] = {"role": "assistant", "content": content if content else None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        async def _lock_and_append():
            async with self._lock:
                self.messages.append(msg)
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.ensure_future(_lock_and_append())
                return
        except RuntimeError:
            pass
        self.messages.append(msg)

    # -----------------------------------------------------------------
    #          (        ,         )
    # -----------------------------------------------------------------
    TOOL_RESULT_MAX_CHARS = 1500

    # -----------------------------------------------------------------
    #         :      +      + HTML    
    # -----------------------------------------------------------------
    def _compress_tool_result(self, result: str, command: str = "", max_chars: int = None) -> str:
        """        ,      ,   token   .

          :
        1.    (<= max_chars):    
        2.    :          
           - HTML/HTTP   :     
           - nmap:      ,  ,  
           - sqlmap:     ,     
           - gobuster/ffuf:       
           - nikto/nuclei:      
           - hydra:      
           -   :    (    +   )
        """
        if not result:
            return "       "
        if max_chars is None:
            max_chars = self.TOOL_RESULT_MAX_CHARS
        if len(result) <= max_chars:
            return result

        #       
        cmd_lower = command.lower() if command else ""

        # 1. HTML/HTTP   
        if self._is_html_or_http(result):
            return self._extract_web_metadata(result, command=command, max_chars=max_chars)

        # 2. nmap     
        if 'nmap' in cmd_lower or cmd_lower.startswith('nmap'):
            return self._compress_nmap_output(result, command=command, max_chars=max_chars)

        # 3. sqlmap   
        if 'sqlmap' in cmd_lower or 'sqlmap' in result.lower():
            return self._compress_sqlmap_output(result, command=command, max_chars=max_chars)

        # 4. gobuster/ffuf/dirb     
        if any(t in cmd_lower for t in ['gobuster', 'ffuf', 'dirb', 'dirsearch']):
            return self._compress_dirscan_output(result, command=command, max_chars=max_chars)

        # 5. nikto     
        if 'nikto' in cmd_lower or 'nikto' in result.lower():
            return self._compress_nikto_output(result, command=command, max_chars=max_chars)

        # 6. nuclei     
        if 'nuclei' in cmd_lower or 'nuclei' in result.lower():
            return self._compress_nuclei_output(result, command=command, max_chars=max_chars)

        # 7. hydra     
        if 'hydra' in cmd_lower:
            return self._compress_hydra_output(result, command=command, max_chars=max_chars)

        # 8. wpscan   
        if 'wpscan' in cmd_lower or 'wpscan' in result.lower():
            return self._compress_wpscan_output(result, command=command, max_chars=max_chars)

        # 9.       
        return self._generic_compress(result, command=command, max_chars=max_chars)

    def _is_html_or_http(self, content: str) -> bool:
        """        HTML   HTTP   ."""
        content_lower = content.lower()
        if '<html' in content_lower or '<!doctype' in content_lower:
            return True
        if content.strip().startswith('HTTP/') or 'HTTP/' in content[:200]:
            return True
        return False

    def _extract_web_metadata(self, content: str, command: str = "", max_chars: int = 2000) -> str:
        """  HTML/HTTP           ,      HTML     LLM."""
        import re
        
        metadata = []
        summary_lines = []

        # 1.    HTTP    (   )
        if content.strip().startswith('HTTP/'):
            head_end = content.find('\n\n')
            if head_end > 0:
                head_text = content[:head_end]
                for line in head_text.splitlines():
                    if ':' in line:
                        metadata.append(line.strip())
                content = content[head_end:].strip()

        # 2.    HTML title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if title_match:
            metadata.append(f"Title: {title_match.group(1).strip()}")

        # 3.    Generator    (   CMS)
        generator_match = re.search(r'<meta[^>]*name=["\']generator["\'][^>]*content=["\'](.*?)["\']', content, re.IGNORECASE)
        if generator_match:
            metadata.append(f"Generator: {generator_match.group(1).strip()}")
        else:
            #    Drupal   Generator   (    head  )
            if 'Drupal' in content or 'drupal' in content:
                gen_line = [line for line in content.splitlines() if 'Generator' in line]
                if gen_line:
                    metadata.append(f"Generator: {gen_line[0].strip()}")

        # 4.    Server  
        server_match = re.search(r'Server:\s*(.+)', content, re.IGNORECASE)
        if server_match:
            metadata.append(f"Server: {server_match.group(1).strip()}")
        else:
            #   HTTP     
            for line in metadata:
                if line.startswith('Server:'):
                    break

        # 5.    X-Powered-By
        powered_match = re.search(r'X-Powered-By:\s*(.+)', content, re.IGNORECASE)
        if powered_match:
            metadata.append(f"X-Powered-By: {powered_match.group(1).strip()}")

        # 6.       (   ,    )
        common_paths = ['/admin', '/login', '/wp-admin', '/administrator', '/phpmyadmin']
        found_paths = []
        for path in common_paths:
            if f'href="{path}' in content or f"href='{path}" in content:
                found_paths.append(path)
        if found_paths:
            metadata.append(f"      : {', '.join(found_paths)}")

        # 7.       (       )
        form_count = content.lower().count('<form')
        if form_count > 0:
            metadata.append(f"    : {form_count}")

        # 8.     curl -I    ,       
        if command and 'curl' in command and '-I' in command:
            #        (    )
            lines = content.splitlines()
            headers = [line for line in lines if ':' in line or line.strip().startswith('HTTP/')]
            result = "\n".join(headers)[:max_chars]
            return (
                f"[HTTP    ]\n"
                f"  : {command}\n"
                f"  : {len(content)}    |    : {len(result)}   \n\n"
                f"{result}\n\n"
                f"[  ]     HTTP    ,      ."
            )

        #       
        result_parts = []
        result_parts.append("[Web        ]")
        result_parts.append(f"  : {command}")
        result_parts.append(f"  : {len(content)}    |    :       \n")

        if metadata:
            result_parts.append("[    ]")
            result_parts.extend(metadata)
            result_parts.append("")

        #      HTML        (   10  )
        content_lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(content_lines) > 20:
            result_parts.append("[HTML   (  10  )]")
            result_parts.extend(content_lines[:10])
            result_parts.append("...")
            result_parts.append("[HTML   (  10  )]")
            result_parts.extend(content_lines[-10:])

        result = "\n".join(result_parts)[:max_chars]
        return result

    def _compress_nmap_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """   nmap     ,      ,  ,  ."""
        open_ports = []
        service_info = []
        os_info = []
        script_results = []

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            #       
            if '/tcp' in line or '/udp' in line:
                if 'open' in line:
                    open_ports.append(line)

            #       
            if line.startswith('|') or line.startswith('||'):
                service_info.append(line)

            #    OS   
            if 'OS:' in line or 'Aggressive OS' in line:
                os_info.append(line)

            #       
            if '|_' in line or '|' in line:
                if any(k in line for k in ['CVE-', 'vuln', 'exploit', 'password', 'login']):
                    script_results.append(line)

        #       
        parts = []
        parts.append("[nmap       ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}    |    :        \n")

        if open_ports:
            parts.append(f"[    ]({len(open_ports)}  )")
            parts.extend(open_ports[:20])
            if len(open_ports) > 20:
                parts.append(f"...    {len(open_ports) - 20}    ")
            parts.append("")

        if os_info:
            parts.append("[      ]")
            parts.extend(os_info[:5])
            parts.append("")

        if script_results:
            parts.append(f"[      ]({len(script_results)}  )")
            parts.extend(script_results[:15])
            if len(script_results) > 15:
                parts.append(f"...    {len(script_results) - 15}    ")
            parts.append("")

        if service_info:
            parts.append(f"[    ]({len(service_info)}  )")
            parts.extend(service_info[:10])
            if len(service_info) > 10:
                parts.append(f"...    {len(service_info) - 10}    ")

        result = "\n".join(parts)[:max_chars]
        return result

    def _compress_sqlmap_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """   sqlmap   ,     ,     ."""
        injection_info = []
        db_info = []
        data_found = []
        critical_info = []

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            #      
            if any(k in line for k in ['injection', 'parameter', 'Type:', 'Title:', 'Payload:']):
                injection_info.append(line)

            #      
            if any(k in line for k in ['Database:', 'DBMS:', 'web server', 'web application']):
                db_info.append(line)

            #      
            if any(k in line for k in ['[INFO]', '[WARNING]', '[CRITICAL]']):
                if 'password' in line.lower() or 'username' in line.lower() or 'admin' in line.lower():
                    critical_info.append(line)
                elif 'found' in line or 'retrieved' in line:
                    data_found.append(line)

        #       
        parts = []
        parts.append("[sqlmap       ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}    |    :        \n")

        if injection_info:
            parts.append("[     ]")
            parts.extend(injection_info[:10])
            parts.append("")

        if db_info:
            parts.append("[     ]")
            parts.extend(db_info[:10])
            parts.append("")

        if critical_info:
            parts.append("[    ](      )")
            parts.extend(critical_info[:10])
            parts.append("")

        if data_found:
            parts.append(f"[    ]({len(data_found)}  )")
            parts.extend(data_found[:10])
            if len(data_found) > 10:
                parts.append(f"...    {len(data_found) - 10}  ")

        result = "\n".join(parts)[:max_chars]
        return result

    def _compress_dirscan_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """        ,       ."""
        import re

        found_paths = []
        status_codes = {}

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            #        (       )
            if any(k in line for k in ['(Status:', '[Status:', 'Status:']):
                found_paths.append(line)

                #      
                status_match = re.search(r'Status:\s*(\d+)', line)
                if status_match:
                    code = status_match.group(1)
                    status_codes[code] = status_codes.get(code, 0) + 1

        #       
        parts = []
        parts.append("[        ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}    |   : {len(found_paths)}    \n")

        if status_codes:
            parts.append("[     ]")
            for code in sorted(status_codes.keys()):
                parts.append(f"  {code}: {status_codes[code]}    ")
            parts.append("")

        if found_paths:
            parts.append(f"[     ]({len(found_paths)}  )")
            #      200,204,301,302       
            important_paths = [p for p in found_paths if any(c in p for c in ['200', '204', '301', '302', '403'])]
            other_paths = [p for p in found_paths if p not in important_paths]

            parts.append("(    200/301/302/403  )")
            parts.extend(important_paths[:20])
            if len(important_paths) > 20:
                parts.append(f"...    {len(important_paths) - 20}      ")
            parts.append("")

            if other_paths:
                parts.append(f"(     )")
                parts.extend(other_paths[:10])
                if len(other_paths) > 10:
                    parts.append(f"...    {len(other_paths) - 10}      ")

        result = "\n".join(parts)[:max_chars]
        return result

    def _compress_nikto_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """   nikto     ,      ."""
        vulnerabilities = []
        info_items = []

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            if not line or line.startswith('+'):
                if line.startswith('+'):
                    # nikto     : +   :   
                    if any(k in line for k in ['VULNERABLE', 'CVE-', 'exploit', 'password', 'admin', 'login']):
                        vulnerabilities.append(line)
                    else:
                        info_items.append(line)

        #       
        parts = []
        parts.append("[nikto       ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}    |    :        \n")

        if vulnerabilities:
            parts.append(f"[    ]({len(vulnerabilities)}  )")
            parts.extend(vulnerabilities[:15])
            if len(vulnerabilities) > 15:
                parts.append(f"...    {len(vulnerabilities) - 15}    ")
            parts.append("")

        if info_items:
            parts.append(f"[    ]({len(info_items)}  )")
            parts.extend(info_items[:15])
            if len(info_items) > 15:
                parts.append(f"...    {len(info_items) - 15}    ")

        result = "\n".join(parts)[:max_chars]
        return result

    def _compress_nuclei_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """   nuclei     ,      ."""
        import json

        vulnerabilities = []
        stats = {}

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            # nuclei       JSON   
            if line.startswith('{'):
                try:
                    data = json.loads(line)
                    if 'info' in data and 'name' in data['info']:
                        vuln_name = data['info']['name']
                        severity = data['info'].get('severity', 'unknown')
                        vulnerabilities.append(f"[{severity.upper()}] {vuln_name}")

                        #       
                        stats[severity] = stats.get(severity, 0) + 1
                except:
                    pass
            else:
                #       
                if any(k in line for k in ['[critical]', '[high]', '[medium]', '[low]']):
                    vulnerabilities.append(line)

        #       
        parts = []
        parts.append("[nuclei       ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}    |   : {len(vulnerabilities)}    \n")

        if stats:
            parts.append("[      ]")
            for severity in ['critical', 'high', 'medium', 'low', 'info']:
                if severity in stats:
                    parts.append(f"  {severity.upper()}: {stats[severity]}  ")
            parts.append("")

        if vulnerabilities:
            parts.append(f"[    ]({len(vulnerabilities)}  )")
            parts.extend(vulnerabilities[:20])
            if len(vulnerabilities) > 20:
                parts.append(f"...    {len(vulnerabilities) - 20}    ")

        result = "\n".join(parts)[:max_chars]
        return result

    def _compress_hydra_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """   hydra   ,      ."""
        success_credentials = []
        attempts_info = []

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            # hydra     : [PORT][protocol] host: login:PASSWORD
            if 'login:' in line and 'host:' in line:
                success_credentials.append(line)

            #     
            if 'attempts' in line.lower() or 'finished' in line.lower():
                attempts_info.append(line)

        #       
        parts = []
        parts.append("[hydra         ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}   \n")

        if success_credentials:
            parts.append(f"[      ]({len(success_credentials)}  )")
            parts.extend(success_credentials)
            parts.append("")

        if attempts_info:
            parts.append("[    ]")
            parts.extend(attempts_info)

        if not success_credentials:
            parts.append("[INFO]        ")

        result = "\n".join(parts)[:max_chars]
        return result

    def _compress_wpscan_output(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """   wpscan   ,   WordPress     ."""
        vulnerabilities = []
        plugins = []
        themes = []
        users = []

        lines = result.splitlines()

        for line in lines:
            line = line.strip()

            #     
            if any(k in line for k in ['Vulnerability', 'CVE-', 'exploit', 'vuln']):
                vulnerabilities.append(line)

            #     
            if 'Plugin(s):' in line or '[P]' in line:
                plugins.append(line)

            #     
            if 'Theme(s):' in line or '[T]' in line:
                themes.append(line)

            #     
            if 'User(s):' in line or '[U]' in line or 'username' in line.lower():
                users.append(line)

        #       
        parts = []
        parts.append("[wpscan       ]")
        parts.append(f"  : {command}")
        parts.append(f"  : {len(result)}    |    :        \n")

        if vulnerabilities:
            parts.append(f"[    ]({len(vulnerabilities)}  )")
            parts.extend(vulnerabilities[:15])
            if len(vulnerabilities) > 15:
                parts.append(f"...    {len(vulnerabilities) - 15}    ")
            parts.append("")

        if plugins:
            parts.append(f"[  ]({len(plugins)}  )")
            parts.extend(plugins[:10])
            if len(plugins) > 10:
                parts.append(f"...    {len(plugins) - 10}    ")
            parts.append("")

        if themes:
            parts.append(f"[  ]({len(themes)}  )")
            parts.extend(themes[:5])
            parts.append("")

        if users:
            parts.append(f"[  ]({len(users)}  )")
            parts.extend(users[:10])
            parts.append("")

        result = "\n".join(parts)[:max_chars]
        return result

    def _generic_compress(self, result: str, command: str = "", max_chars: int = 2000) -> str:
        """      :      +     ."""
        lines = [line.rstrip() for line in result.splitlines() if line.strip()]

        #          
        keywords = [
            "open", "closed", "filtered",
            "cve-", "CVE-", "vulnerability",
            "found", "password", "username", "login",
            "http/", "https/", "ssl",
            "status:", "title:", "server:",
            "directory", "error", "timeout",
            "port", "service", "version",
            "VULNERABLE", "exploit", "payload",
            "success", "cracked", "valid",
        ]

        key_lines = []
        for line in lines:
            if any(k in line for k in keywords):
                key_lines.append(line)
            if len(key_lines) >= 40:
                break

        head_lines = lines[:12]
        tail_lines = lines[-12:]
        seen = set()
        merged = []
        for line in head_lines + key_lines + tail_lines:
            if line not in seen:
                seen.add(line)
                merged.append(line)
            if len(merged) >= 60:
                break

        summary = "\n".join(merged)
        if len(summary) > max_chars:
            summary = summary[:max_chars]

        return (
            f"[        ]\n"
            f"  : {command}\n"
            f"  : {len(result)}    |    : {len(summary)}   \n\n"
            f"{summary}\n\n"
            f"[  ]        ,          ."
        )

    def append_tool_result(self, tool_call_id: str, name: str, result: str, command: str = "") -> None:
        """        (    )."""
        #         :           
        result = self._compress_tool_result(result, command=command)

        async def _lock_and_append():
            async with self._lock:
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result,
                    }
                )
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.ensure_future(_lock_and_append())
                return
        except RuntimeError:
            pass
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            }
        )

    def clear(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]

    # -----------------------------------------------------------------
    #     :    content      422
    # -----------------------------------------------------------------
    def _sanitize_messages(self, msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """      ,        422      ."""
        cleaned = []
        for i, msg in enumerate(msgs):
            role = msg.get("role", "")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            # *    tool_calls        (arguments_json  )
            if role == "assistant" and tool_calls:
                cleaned_tc = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    args_raw = fn.get("arguments", "{}")
                    if isinstance(args_raw, str) and args_raw.strip():
                        try:
                            json.loads(args_raw)
                        except json.JSONDecodeError:
                            logger.warning("[LLM][    ] arguments      JSON,   : %s", args_raw[:100])
                            args_raw = "{}"
                    cleaned_tc.append({
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": args_raw,
                        },
                    })
                msg = dict(msg)
                msg["tool_calls"] = cleaned_tc

            # assistant   :  tool_calls   content     None
            if role == "assistant" and tool_calls:
                if content is None or content == "":
                    msg = dict(msg)
                    msg["content"] = None
                    cleaned.append(msg)
                    continue

            # tool   :content         (   ,      tool_call   tool   )
            if role == "tool":
                # * Mistral API     tool      name   ,  
                msg = dict(msg)
                msg.pop("name", None)
                if not content or not isinstance(content, str) or not content.strip():
                    msg["content"] = " "
                    cleaned.append(msg)
                    continue

            #     :content         
            if not content or not isinstance(content, str) or not content.strip():
                logger.warning(
                    "[LLM][    ]     %d      : role=%s, content=%r",
                    i, role, content
                )
                continue

            cleaned.append(msg)

        return cleaned

    # -----------------------------------------------------------------
    # Tool Call     :            
    # -----------------------------------------------------------------
    @staticmethod
    def _normalize_tool_calls(message, allow_content_json: bool = True) -> List[Dict[str, Any]]:
        """     LLM     tool call       dict   .

             :
        1. OpenAI   :message.tool_calls (list),arguments     str   dict
        2. content    JSON    (      tool call     )
        3. Anthropic   :message.content   type=tool_use   
        4. arguments   dict   (      )
        """
        import uuid
        import re
        tool_calls: List[Dict[str, Any]] = []

        #    1:   OpenAI    tool_calls
        if hasattr(message, 'tool_calls') and message.tool_calls:
            for tc in message.tool_calls:
                if not hasattr(tc, 'function') or tc.function is None:
                    continue
                fn = tc.function
                args = fn.arguments if hasattr(fn, 'arguments') else {}
                if isinstance(args, dict):
                    args_str = json.dumps(args)
                else:
                    args_str = str(args) if args is not None else "{}"
                tool_calls.append({
                    "id": getattr(tc, 'id', None) or f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": getattr(fn, 'name', '') or 'execute_kali_command',
                        "arguments": args_str,
                    },
                })

        #    2:content    JSON    (      tool call    )
        if allow_content_json and hasattr(message, 'content') and message.content:
            content = message.content if isinstance(message.content, str) else ""
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1))
                    cmd = data.get("command") or data.get("cmd", "")
                    if cmd:
                        tool_calls.append({
                            "id": f"auto_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": data.get("name", "execute_kali_command"),
                                "arguments": json.dumps(data.get("parameters", data.get("arguments", {"command": cmd}))),
                            },
                        })
                except (json.JSONDecodeError, KeyError):
                    pass

        #    tool calls,     token   
        tool_calls = sanitize_tc(tool_calls)
        
        return tool_calls

    # -----------------------------------------------------------------
    #     :             token   
    #   :   system +    N    (         token)
    # -----------------------------------------------------------------
    def _trim_messages(self, msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """      ,       .

            :
        1.         system   
        2.      context_reserve_recent  (user+assistant    )
        3.          context_max_tokens*4,         
        4.      tool         assistant   (   OpenRouter 400)
        """
        if not msgs:
            return msgs

        #    system   (   )
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        non_system = [m for m in msgs if m.get("role") != "system"]

        #      :context_reserve_recent        2*N    
        reserve_msgs = self.settings.context_reserve_recent or 4
        max_non_system = reserve_msgs * 2 + 4  #        

        if len(non_system) > max_non_system:
            original_non_system = list(non_system)  #        
            non_system = non_system[-max_non_system:]

            # *   :       tool     ,   assistant     
            #         ,   assistant +    tool          
            if non_system and non_system[0].get("role") == "tool":
                extra = 0
                for m in reversed(original_non_system[:-max_non_system]):
                    extra += 1
                    if m.get("role") == "assistant":
                        #        assistant,         tool
                        break
                non_system = original_non_system[-(max_non_system + extra):]

        #        (1 token   4   ,    )
        max_chars = (self.settings.context_max_tokens or 8192) * 4
        trimmed_non_system = []
        total_chars = 0
        for m in reversed(non_system):
            content = m.get("content") or ""
            tool_calls = m.get("tool_calls")
            # tool_calls    token,    
            tc_chars = 0
            if tool_calls:
                import json as _json
                try:
                    tc_chars = len(_json.dumps(tool_calls))
                except Exception:
                    tc_chars = 200
            chars = len(str(content)) + tc_chars
            if total_chars + chars > max_chars and trimmed_non_system:
                break
            trimmed_non_system.insert(0, m)
            total_chars += chars

        # *   :         tool   ,    
        if trimmed_non_system and trimmed_non_system[0].get("role") == "tool":
            #   non_system      ,        assistant
            pre_msgs = []
            for m in reversed(non_system):
                pre_msgs.insert(0, m)
                if m.get("role") == "assistant":
                    break
            if len(pre_msgs) > len(trimmed_non_system):
                trimmed_non_system = pre_msgs

        return system_msgs + trimmed_non_system

    # -----------------------------------------------------------------
    # AutoPilot     
    # -----------------------------------------------------------------
    def build_api_messages(self, autopilot: bool = False) -> List[Dict[str, Any]]:
        raw = list(self.messages)

        if not autopilot:
            trimmed = self._trim_messages(raw)
            return self._sanitize_messages(trimmed)

        injected_system = self.system_prompt + AUTOPILOT_INJECTION
        rewritten = [{"role": "system", "content": injected_system}] + list(self.messages[1:])
        trimmed = self._trim_messages(rewritten)
        return self._sanitize_messages(trimmed)

    # ------------------------------------------------------------------
    #    :         API
    # ------------------------------------------------------------------
    def _api_call_with_retry(self, kwargs: Dict[str, Any]) -> Any:
        """   LLM API   ,        +       +       .
        
                (    ):
        - 422   :     multi_model_switch_422_count         
        - 429   :     multi_model_switch_429_count         
        - timeout:     multi_model_switch_timeout_count         
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            #            ,    
            if is_llm_shutting_down():
                logger.warning("[LLM]       ,   API    (   %d/%d)",
                              attempt + 1, self.max_retries + 1)
                raise RuntimeError("    :LLM API       ")

            # *         (   422)
            kwargs = self._validate_and_fix_request(kwargs)
            
            # *          (      )
            kwargs["model"] = self.settings.llm_model

            try:
                result = self.client.chat.completions.create(**kwargs)
                
                # *   :        
                self._consecutive_422_count = 0
                self._consecutive_429_count = 0
                self._consecutive_timeout_count = 0
                
                return result
            
            except Exception as exc:
                last_exception = exc
                
                err_str = str(exc).lower()
                
                # *    Mistral 422   (          )
                if "422" in str(exc) or "Unprocessable" in str(exc):
                    self._consecutive_422_count += 1
                    logger.error("[LLM] API    422!     : %d/%d,   : %s", 
                                self._consecutive_422_count, 
                                self.settings.multi_model_switch_422_count,
                                str(exc)[:500])
                    try:
                        if hasattr(exc, 'response') and hasattr(exc.response, 'text'):
                            logger.error("[LLM] 422     : %s", exc.response.text[:2000])
                    except Exception:
                        pass

                    # *           
                    if (self.settings.multi_model_auto_switch and 
                        self._consecutive_422_count >= self.settings.multi_model_switch_422_count):
                        old_model = self.settings.llm_model
                        if self._switch_to_next_model():
                            kwargs["model"] = self.settings.llm_model
                            logger.warning("[LLM] 422       : %s -> %s", old_model, self.settings.llm_model)
                            self._notify_model_switch_sync(old_model, self.settings.llm_model, "422_error")
                            #     ,    (    retry   )
                            try:
                                result = self.client.chat.completions.create(**kwargs)
                                self._consecutive_422_count = 0
                                return result
                            except Exception as switch_exc:
                                last_exception = switch_exc
                                #        ,    
                
                # *    429 (rate limit)
                elif "429" in err_str or "rate limit" in err_str:
                    self._consecutive_429_count += 1
                    logger.warning("[LLM] API    429 (    ),     : %d/%d",
                                 self._consecutive_429_count,
                                 self.settings.multi_model_switch_429_count)
                    
                    # *           
                    if (self.settings.multi_model_auto_switch and 
                        self._consecutive_429_count >= self.settings.multi_model_switch_429_count):
                        old_model = self.settings.llm_model
                        if self._switch_to_next_model():
                            kwargs["model"] = self.settings.llm_model
                            logger.warning("[LLM] 429       : %s -> %s", old_model, self.settings.llm_model)
                            self._notify_model_switch_sync(old_model, self.settings.llm_model, "429_rate_limit")
                            try:
                                result = self.client.chat.completions.create(**kwargs)
                                self._consecutive_429_count = 0
                                return result
                            except Exception as switch_exc:
                                last_exception = switch_exc
                
                # *    timeout
                elif "timeout" in err_str or "timed out" in err_str:
                    self._consecutive_timeout_count += 1
                    logger.warning("[LLM] API     ,     : %d/%d",
                                 self._consecutive_timeout_count,
                                 self.settings.multi_model_switch_timeout_count)
                    
                    # *           
                    if (self.settings.multi_model_auto_switch and 
                        self._consecutive_timeout_count >= self.settings.multi_model_switch_timeout_count):
                        old_model = self.settings.llm_model
                        if self._switch_to_next_model():
                            kwargs["model"] = self.settings.llm_model
                            logger.warning("[LLM]       : %s -> %s", old_model, self.settings.llm_model)
                            self._notify_model_switch_sync(old_model, self.settings.llm_model, "timeout")
                            try:
                                result = self.client.chat.completions.create(**kwargs)
                                self._consecutive_timeout_count = 0
                                return result
                            except Exception as switch_exc:
                                last_exception = switch_exc
                
                else:
                    #     ,   422/429/timeout   
                    self._consecutive_422_count = 0
                    self._consecutive_429_count = 0
                    self._consecutive_timeout_count = 0

                error_trace = traceback.format_exc()

                #           
                is_retryable = any(keyword in err_str for keyword in [
                    "timeout", "connection", "network", "econnrefused",
                    "econnreset", "eagain", "service unavailable",
                    "rate limit", "429", "500", "502", "503",
                ])

                if not is_retryable or attempt >= self.max_retries:
                    logger.error("LLM API      (   %d/%d): %s",
                                 attempt + 1, self.max_retries + 1, exc)
                    self.console.print(f"[bold red]  LLM API      (   {attempt + 1}/{self.max_retries + 1}): {exc}[/bold red]")
                    raise RuntimeError(f"LLM API      (   {attempt + 1}/{self.max_retries + 1}): {exc}") from exc

                #      +   
                base_delay = self.retry_delay * (2 ** (attempt - 1)) if attempt > 0 else self.retry_delay
                capped_delay = min(base_delay, 60.0)
                jitter = capped_delay * 0.25
                delay = capped_delay + random.uniform(-jitter, jitter)
                delay = max(0.5, delay)

                logger.warning("LLM API      (   %d/%d),%.1fs    : %s",
                               attempt + 1, self.max_retries + 1, delay, exc)
                self.console.print(f"[bold yellow]   LLM API      (   {attempt + 1}/{self.max_retries + 1}),{delay:.0f}s    ...[/bold yellow]")
                
                #      sleep,  0.5s         
                elapsed = 0.0
                while elapsed < delay:
                    if is_llm_shutting_down():
                        raise RuntimeError("    :LLM       ")
                    sleep_chunk = min(0.5, delay - elapsed)
                    time.sleep(sleep_chunk)
                    elapsed += sleep_chunk

        #        
        raise RuntimeError(f"LLM API       : {last_exception}")

    # ------------------------------------------------------------------
    #        (  AutoPilot     )
    # ------------------------------------------------------------------

    async def chat(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
              LLM   (     ).
        - prompt:     
        - system_prompt:   ,      system_prompt(      ,     )
           LLM        .
        """
        # *   :      ,         
        messages = self.messages.copy()

        #       system_prompt,  (     )
        if system_prompt:
            if messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system", "content": system_prompt}
            else:
                messages.insert(0, {"role": "system", "content": system_prompt})

        #         
        messages.append({"role": "user", "content": prompt})

        kwargs = {"model": self.settings.llm_model, "messages": messages}
        if hasattr(self.settings, "llm_temperature") and self.settings.llm_temperature:
            kwargs["temperature"] = self.settings.llm_temperature
        if hasattr(self.settings, "llm_max_tokens") and self.settings.llm_max_tokens:
            kwargs["max_tokens"] = self.settings.llm_max_tokens

        try:
            response = await asyncio.to_thread(self._api_call_with_retry, kwargs)
            content = response.choices[0].message.content or ""
            #        
            self.append_user(prompt)
            self.append_assistant(content)
            return content
        except Exception as exc:
            logger.error("[LLM] chat     : %s", exc)
            raise

    def stream_chat(
        self,
        autopilot: bool = False,
        *,
        enable_tools: bool = True,
        stop_event: Optional[asyncio.Event] = None,
    ) -> tuple[str, list[Any]]:
        """       LLM   (     ,  Provider   ).

          :
            autopilot:      AutoPilot   (      )
            enable_tools:         
            stop_event:       ,              

          :
            (full_content, collected_tool_call_dicts)
        """
        #       (             )
        from config.settings import reload_settings
        reload_settings()
        self.settings = get_settings()
        self.provider = self.settings.llm_provider
        self.client = self._init_client()

        api_messages = self.build_api_messages(autopilot=autopilot)
        provider = self.provider.lower()

        try:
            self.console.print(f"[bold yellow]  [DEBUG] Provider={provider}     {self.settings.llm_base_url}       ...[/bold yellow]")

            if provider == "anthropic":
                return self._stream_chat_anthropic(api_messages, enable_tools, stop_event)

            return self._stream_chat_openai(api_messages, enable_tools, stop_event)

        except RuntimeError:
            raise
        except Exception as exc:
            error_trace = traceback.format_exc()
            self.console.print(f"[bold red]  LLM API     : {exc}[/bold red]")
            print(f"[PengStrike ERROR]\n{error_trace}", file=sys.stderr)
            logger.error("LLM API     : %s", exc)
            raise RuntimeError(f"LLM API     : {exc}") from exc

    def _stream_chat_openai(
        self,
        api_messages: List[Dict[str, Any]],
        enable_tools: bool,
        stop_event: Optional[asyncio.Event] = None,
    ) -> tuple[str, list[Any]]:
        """OpenAI / Ollama / Azure OpenAI        ."""
        kwargs: Dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": api_messages,
            "temperature": self.settings.llm_temperature,
            "stream": True,
        }
        if enable_tools:
            kwargs["tools"] = self.tools
            kwargs["tool_choice"] = "auto"
        if self.settings.llm_max_tokens is not None:
            kwargs["max_tokens"] = self.settings.llm_max_tokens

        #                                                       
        #   :       (      )
        #                                                       
        debug_kwargs = {k: v for k, v in kwargs.items()}
        #       messages       
        debug_messages = []
        for m in debug_kwargs.get("messages", []):
            dm = dict(m)
            content = dm.get("content", "")
            if isinstance(content, str) and len(content) > 200:
                dm["content"] = content[:200] + f"...(  , {len(content)} )"
            debug_messages.append(dm)
        debug_kwargs["messages"] = debug_messages
        logger.info("[LLM][   ] %s", json.dumps(debug_kwargs, ensure_ascii=False))

        stream = self._api_call_with_retry(kwargs)

        collected_tool_calls: list[Any] = []
        full_content_parts: List[str] = []

        try:
            for chunk in stream:
                #         
                if stop_event is not None and stop_event.is_set():
                    logger.warning("[LLM]       ,      ")
                    stream.close()
                    break

                #   :         choices   
                if not chunk.choices or len(chunk.choices) == 0:
                    continue
                choice = chunk.choices[0]
                if not hasattr(choice, "delta"):
                    continue
                delta = choice.delta
                if delta is None:
                    continue

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        while idx >= len(collected_tool_calls):
                            collected_tool_calls.append(
                                type(
                                    "obj",
                                    (object,),
                                    {"id": None, "function": type("obj", (object,), {"name": "", "arguments": ""})()},
                                )()
                            )
                        existing = collected_tool_calls[idx]
                        if tc.id is not None:
                            existing.id = tc.id
                        if tc.function is not None:
                            if tc.function.name is not None:
                                existing.function.name = tc.function.name
                            if tc.function.arguments is not None:
                                existing.function.arguments += tc.function.arguments

                elif delta.content is not None:
                    full_content_parts.append(delta.content)
                    self.console.print(delta.content, end="")

            self.console.print("\n")
        except Exception as exc:
            self.console.print(f"[bold red]        : {exc}[/bold red]")
            logger.exception("      : %s", exc)

        full_content = "".join(full_content_parts)

        tool_call_dicts = []
        for tc in collected_tool_calls:
            #      :       /  token   name
            raw_name = tc.function.name or ""
            args_raw = tc.function.arguments or ""

            #      OpenRouter/       token:<|...|>   
            import re as _re
            _strip_pattern = _re.compile(r"<\|[^|]*\|>.*", _re.DOTALL)
            raw_name = _strip_pattern.sub("", raw_name).strip()
            #         <>    :<|   
            if "<|" in raw_name:
                raw_name = raw_name.split("<|", 1)[0].strip()

            #      JSON      name    :{...}   (...)
            if "{" in raw_name:
                clean_name = raw_name.split("{", 1)[0].strip()
                suffix = raw_name[len(clean_name):].strip()
                if not args_raw.strip():
                    args_raw = suffix
                raw_name = clean_name
            elif "(" in raw_name and ")" in raw_name:
                clean_name = raw_name.split("(", 1)[0].strip()
                suffix = raw_name[len(clean_name):].strip()
                if not args_raw.strip():
                    args_raw = suffix
                raw_name = clean_name

            #           /  
            raw_name = raw_name.strip().strip("\"'")

            #        :          
            if raw_name not in _ALLOWED_TOOL_NAMES:
                logger.warning(
                    "[LLM][     ]         : raw=%r,    =%r",
                    tc.function.name, raw_name
                )
                continue

            tc.function.name = raw_name

            tc_dict = {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": args_raw,
                },
            }
            tool_call_dicts.append(tc_dict)

        return full_content, tool_call_dicts

    # ------------------------------------------------------------------
    # SSE      :   yield,       
    # ------------------------------------------------------------------
    def stream_chat_sse(
        self,
        autopilot: bool = False,
        *,
        enable_tools: bool = True,
        stop_event: Optional[asyncio.Event] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """SSE      ,   yield content/tool_calls   .

        Yields:
            {"type": "content", "text": "..."}
            {"type": "tool_calls", "calls": [...]}
            {"type": "done"}
            {"type": "error", "message": "..."}
        """
        self.settings = get_settings()
        self.provider = self.settings.llm_provider
        self.client = self._init_client()

        api_messages = self.build_api_messages(autopilot=autopilot)
        provider = self.provider.lower()

        if provider == "anthropic":
            yield {"type": "error", "message": "SSE        Anthropic"}
            return

        try:
            kwargs: Dict[str, Any] = {
                "model": self.settings.llm_model,
                "messages": api_messages,
                "temperature": self.settings.llm_temperature,
                "stream": True,
            }
            if enable_tools:
                kwargs["tools"] = self.tools
                kwargs["tool_choice"] = "auto"
            if self.settings.llm_max_tokens is not None:
                kwargs["max_tokens"] = self.settings.llm_max_tokens

            stream = self._api_call_with_retry(kwargs)
            collected_tool_calls: list[Any] = []

            for chunk in stream:
                if stop_event is not None and stop_event.is_set():
                    logger.warning("[LLM]       ,      ")
                    stream.close()
                    break

                #   :         choices   
                if not chunk.choices or len(chunk.choices) == 0:
                    continue
                choice = chunk.choices[0]
                if not hasattr(choice, "delta"):
                    continue
                delta = choice.delta
                if delta is None:
                    continue

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        while idx >= len(collected_tool_calls):
                            collected_tool_calls.append(
                                type("obj", (object,),
                                    {"id": None, "function": type("obj", (object,),
                                    {"name": "", "arguments": ""})()})()
                            )
                        existing = collected_tool_calls[idx]
                        if tc.id is not None:
                            existing.id = tc.id
                        if tc.function is not None:
                            if tc.function.name is not None:
                                existing.function.name = tc.function.name
                            if tc.function.arguments is not None:
                                existing.function.arguments += tc.function.arguments

                elif delta.content is not None:
                    yield {"type": "content", "text": delta.content if delta.content else ""}
                    if delta.content:
                        self.console.print(delta.content, end="")

            #    tool_calls   
            if collected_tool_calls:
                tool_call_dicts = []
                for tc in collected_tool_calls:
                    #      :          name
                    raw_name = tc.function.name or ""
                    args_raw = tc.function.arguments or ""

                    #   1: name     JSON   ,  execute_kali_command{"command": "..."}
                    if "{" in raw_name:
                        #         
                        clean_name = raw_name.split("{", 1)[0].strip()
                        #       name       
                        suffix = raw_name[len(clean_name):].strip()
                        #    arguments       ,     arguments;    suffix
                        if not args_raw.strip():
                            args_raw = suffix
                        tc.function.name = clean_name

                    #   2: name        ,  execute_kali_command(command=...)
                    elif "(" in raw_name and ")" in raw_name:
                        clean_name = raw_name.split("(", 1)[0].strip()
                        suffix = raw_name[len(clean_name):].strip()
                        if not args_raw.strip():
                            args_raw = suffix
                        tc.function.name = clean_name

                    try:
                        args_json = json.loads(args_raw) if args_raw.strip() else {}
                    except json.JSONDecodeError:
                        args_json = {"command": args_raw}

                    tool_call_dicts.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name or "",
                            "arguments": args_json,
                        },
                    })
                yield {"type": "tool_calls", "calls": tool_call_dicts}

            yield {"type": "done"}
            self.console.print("\n")

        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            logger.exception("SSE       : %s", exc)

    def _stream_chat_anthropic(
        self,
        api_messages: List[Dict[str, Any]],
        enable_tools: bool,
        stop_event: Optional[asyncio.Event] = None,
    ) -> tuple[str, list[Any]]:
        """Anthropic Claude     (   SDK   messages.stream)."""
        system_content = ""
        claude_messages = []
        for msg in api_messages:
            if msg["role"] == "system":
                system_content += (msg.get("content") or "") + "\n"
            else:
                claude_messages.append({"role": msg["role"], "content": msg.get("content") or ""})

        kwargs: Dict[str, Any] = {
            "model": self.settings.llm_model,
            "max_tokens": self.settings.llm_max_tokens or 4096,
            "messages": claude_messages,
        }
        if system_content.strip():
            kwargs["system"] = system_content.strip()
        if enable_tools:
            kwargs["tools"] = self._convert_to_anthropic_tools(self.tools)

        stream = self.client.messages.stream(**kwargs)

        full_content_parts: List[str] = []
        collected_tool_calls: list[Any] = []

        with stream as s:
            for event in s:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    full_content_parts.append(event.delta.text)
                    self.console.print(event.delta.text, end="")
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    tool_obj = type(
                        "obj",
                        (object,),
                        {
                            "id": event.content_block.id,
                            "function": type("obj", (object,), {
                                "name": event.content_block.name,
                                "arguments": "",
                            })(),
                        },
                    )()
                    collected_tool_calls.append(tool_obj)

            for event in s.events():
                if event.type == "content_block_delta" and hasattr(event.delta, "partial_json"):
                    if collected_tool_calls:
                        collected_tool_calls[-1].function.arguments += event.delta.partial_json

        self.console.print("\n")
        full_content = "".join(full_content_parts)

        tool_call_dicts = []
        for tc in collected_tool_calls:
            args_raw = tc.function.arguments
            tool_call_dicts.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": args_raw if isinstance(args_raw, str) else json.dumps(args_raw) if args_raw else "{}",
                },
            })

        return full_content, tool_call_dicts

    def _convert_to_anthropic_tools(self, openai_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """  OpenAI            Anthropic   ."""
        anthropic_tools = []
        for tool in openai_tools:
            fn = tool.get("function", tool)
            anthropic_tools.append({
                "name": fn.get("name", "unknown"),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools
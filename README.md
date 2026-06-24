# PengStrike

[![Version](https://img.shields.io/badge/version-v0.1.0--alpha-orange)](https://github.com/yourusername/PengStrike)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/vue-3.0+-green)](https://vuejs.org/)# PengStrike

> **AI 驱动的渗透测试辅助平台** — 将 LLM 与 Kali Linux 专业工具相结合，实现智能化的渗透测试工作流。

（此版本仅为测试版本，呼吁大家一起测试发现bug，或者提出想要的功能，我会尽力去完善）

PengStrike 是一个基于 AI 大语言模型（LLM）的渗透测试辅助平台，专为 Kali Linux 环境设计。它通过自然语言交互和自动化引擎，帮助安全测试人员更高效地完成信息收集、漏洞扫描、漏洞利用和报告生成等渗透测试全流程任务。

### 核心特性

- **智能对话**: 集成 OpenAI/Anthropic/Ollama 等 LLM，支持自然语言驱动的渗透测试
- **AutoPilot 自动巡航**: 自动化执行信息收集、漏洞扫描、漏洞利用的全流程
- **多模型备份**: 支持配置多个 LLM 模型，自动切换实现故障转移
- **阶段化任务管理**: 将渗透测试分为初始化、信息收集、漏洞排序、漏洞利用、报告生成五个阶段
- **实时 WebSocket 通信**: 前后端通过 WebSocket 实时同步进度、日志和结果
- **工具集成**: 内置 40+ 专业渗透测试工具（nmap、sqlmap、hydra、gobuster 等）
- **安全防护**: 内置命令安全链、异常检测、参数验证等多层防护机制
- **中文支持**: 全中文交互界面和日志输出

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    前端 (Vue 3 + Element Plus)            │
│  Dashboard │ SessionDetail │ ToolList │ ReportList ...  │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────▼──────────────────────────────────┐
│                  API 层 (FastAPI)                        │
│  session_routes │ tool_routes │ config_routes │ ...     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   核心服务层                              │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────────┐     │
│  │ AutoPilot   │ │ LLM      │ │ Orchestrator     │     │
│  │ Engine      │ │ Client   │ │ (任务编排)        │     │
│  └─────────────┘ └──────────┘ └──────────────────┘     │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────────┐     │
│  │ State       │ │ Tool     │ │ Report Generator │     │
│  │ Manager     │ │ Executor │ │ (报告生成)        │     │
│  └─────────────┘ └──────────┘ └──────────────────┘     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  基础设施层                               │
│  SQLite │ Config │ Middleware │ Security │ Plugins      │
└─────────────────────────────────────────────────────────┘
```

### 目录结构

```
PengStrike/
├── api/                  # API 路由（FastAPI）
│   ├── session_routes.py # 会话管理
│   ├── tool_routes.py    # 工具接口
│   ├── websocket.py      # WebSocket 通信
│   └── ...
├── core/                 # 核心逻辑
│   ├── auto_pilot.py     # AutoPilot 引擎
│   ├── llm_client.py     # LLM 客户端
│   ├── state_manager.py  # 状态管理
│   ├── orchestrator.py   # 任务编排
│   └── ...
├── config/               # 配置管理
│   ├── settings.py       # 配置中心
│   └── config.json       # 主配置文件
├── frontend/             # 前端（Vue 3）
│   └── src/
│       ├── views/        # 页面组件
│       ├── components/   # 通用组件
│       └── utils/        # 工具函数
├── tools/                # 工具系统
│   ├── modules/          # 工具模块
│   │   ├── reconnaissance/  # 侦察
│   │   ├── scanner/         # 扫描器
│   │   ├── exploit/         # 利用
│   │   ├── privesc/         # 提权
│   │   └── ...
│   └── executor.py       # 命令执行器
├── db/                   # 数据库
├── middleware/           # 中间件
├── security/             # 安全模块
└── reports/              # 报告模板
```

## 快速开始

### 环境要求

- Kali Linux（推荐）或其他 Debian 系 Linux
- Python 3.9+
- Node.js 18+
- 有效的 LLM API Key（OpenAI / Anthropic / Ollama 等）

### 安装部署

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/PengStrike.git
cd PengStrike

# 2. 配置 Python 虚拟环境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 安装前端依赖
cd frontend
npm install
cd ..

# 4. 配置 API Key
# 编辑 config.json，填写 LLM 配置
# 或通过环境变量设置（优先级更高）:
export PENGSTRIKE_LLM_API_KEY="your-api-key-here"
export PENGSTRIKE_LLM_BASE_URL="https://your-llm-endpoint"

# 5. 启动服务
bash Pentest-Web.sh
```

服务启动后，访问 `http://127.0.0.1:5173` 进入前端界面。

### 快速使用

1. **创建会话**: 在 Dashboard 页面点击"新建会话"，输入目标 IP 或域名
2. **启动 AutoPilot**: 进入会话详情，点击"启动 AutoPilot"开始自动化渗透测试
3. **AI 对话**: 在右侧面板直接与 AI 对话，下达指令或获取分析
4. **工具执行**: 在对话框中让 AI 自动调用 nmap、sqlmap 等工具
5. **生成报告**: 测试完成后，点击"下载报告"获取 HTML/JSON 格式报告

## 配置说明

### config.json 配置

| 配置段 | 字段 | 说明 | 默认值 |
|--------|------|------|--------|
| llm | base_url | LLM API 地址 | https://openrouter.ai/api/v1 |
| llm | api_key | API 密钥 | "" |
| llm | model | 模型名称 | openrouter/free |
| llm | provider | 提供商 | openai |
| system | command_timeout | 命令超时(秒) | 300 |
| system | log_level | 日志级别 | INFO |
| system | dangerous_interrupt | 允许危险中断 | true |
| backend | port | 后端端口 | 8000 |
| backend | frontend_port | 前端端口 | 5173 |

### 环境变量

| 环境变量 | 对应配置 | 说明 |
|----------|----------|------|
| PENGSTRIKE_LLM_API_KEY | llm.api_key | LLM API 密钥（优先级最高） |
| PENGSTRIKE_LLM_BASE_URL | llm.base_url | LLM API 地址 |
| LLM_API_KEY | llm.api_key | 传统环境变量 |
| LLM_MODEL | llm.model | 模型名称 |
| LLM_PROVIDER | llm.provider | 提供商 |
| LOG_LEVEL | system.log_level | 日志级别 |

> 环境变量的优先级高于 config.json 配置文件，适用于部署时动态调整。

### 多模型备份配置

在 config.json 的 `multi_models` 字段中配置多个备用模型，当主模型遇到 429（限流）、422（请求错误）或超时时自动切换：

```json
{
  "multi_models": [
    { "model": "gpt-4", "base_url": "...", "api_key": "..." },
    { "model": "claude-3", "base_url": "...", "api_key": "..." }
  ],
  "multi_model_auto_switch": true,
  "multi_model_switch_429_count": 2,
  "multi_model_switch_422_count": 1,
  "multi_model_switch_timeout_count": 2
}
```

## 功能列表

### 核心功能
- [x] LLM 驱动的渗透测试对话
- [x] AutoPilot 全自动渗透测试
- [x] 多阶段任务管理
- [x] 实时日志与进度展示
- [x] 报告生成（HTML/JSON）
- [x] 多模型备份与自动切换
- [x] 断点续扫

### 集成工具
- **侦察**: nmap, masscan, gobuster, ffuf, sublist3r, theHarvester, dnsrecon, fierce, amass, crt.sh, dig, whois
- **扫描**: nikto, nuclei, sqlmap, wpscan, xsstrike, nmap_script
- **利用**: hydra, msfconsole, searchsploit
- **提权**: linpeas, winpeas, linenum, enum4linux, suid3num
- **网络**: crackmapexec, impacket, responder, evil-winrm, bloodhound
- **实用工具**: curl, nc, ping, netstat, hashcat, john, crunch, cewl, reaver

### 安全特性
- [x] 命令白名单过滤
- [x] 高危命令拦截
- [x] Tool Call 清洗器
- [x] 异常行为检测
- [x] 参数验证中间件
- [x] 安全链审查机制

## 贡献指南

欢迎贡献代码、提交 Issue 或改进建议！

### 开发流程

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 提交 Pull Request

### 代码规范

- Python 代码遵循 PEP 8 规范
- 前端代码遵循 Vue 3 组合式 API 风格
- 提交信息使用中文或英文均可，需清晰描述变更内容

### 开发环境

```bash
# 后端开发（热重载）
cd ~/Desktop/PengStrike
source venv/bin/activate
uvicorn api.app:app --reload --host 127.0.0.1 --port 8000

# 前端开发（热重载）
cd frontend
npm run dev
```

## 许可证

本项目基于 Apache License 2.0 许可证开源 — 查看 [LICENSE](LICENSE) 文件获取详细信息。

---

**安全声明**: 本工具仅用于授权的安全测试和教育目的。使用者应遵守当地法律法规，对使用本工具产生的后果自行承担责任。

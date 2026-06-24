# 贡献指南

感谢您对 PengStrike 的关注和贡献！我们欢迎所有形式的贡献，包括但不限于代码、文档、问题报告和功能建议。

## 行为准则

请阅读并遵守我们的 [行为准则](CODE_OF_CONDUCT.md)。我们致力于创建一个开放、包容和尊重的社区。

## 如何贡献

### 报告问题
- 在提交问题之前，请先搜索现有问题，确保没有重复
- 提供尽可能详细的信息，包括：
  - 问题描述
  - 复现步骤
  - 预期行为
  - 实际行为
  - 环境信息（操作系统、浏览器、版本等）

### 提交代码
1. **Fork 仓库**：点击 GitHub 上的 "Fork" 按钮
2. **克隆仓库**：
   ```bash
   git clone https://github.com/your-username/PengStrike.git
   cd PengStrike
   ```
3. **创建分支**：
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   cd frontend && npm install
   ```
5. **进行更改**：在代码中实现您的功能或修复
6. **测试**：确保所有测试通过
   ```bash
   pytest
   cd frontend && npm run test
   ```
7. **提交更改**：
   ```bash
   git add .
   git commit -m "feat: add your feature description"
   ```
8. **推送分支**：
   ```bash
   git push origin feature/your-feature-name
   ```
9. **创建 Pull Request**：在 GitHub 上创建 Pull Request 到 `main` 分支

### 代码规范
- **Python**：遵循 PEP 8 规范
- **JavaScript/TypeScript**：遵循 ESLint 规范（配置在 `frontend/.eslintrc.js`）
- **提交信息**：使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式
  - `feat:` 新功能
  - `fix:` 修复问题
  - `docs:` 文档更新
  - `style:` 代码格式调整
  - `refactor:` 代码重构
  - `test:` 测试相关
  - `chore:` 其他杂项

### 文档贡献
- 文档使用 Markdown 格式
- 确保文档与代码保持同步更新
- 文档存放在 `docs/` 目录下

## 审查流程
- 所有 Pull Request 需要至少 1 个核心成员的批准
- 代码审查将关注：
  - 代码质量
  - 安全性
  - 可维护性
  - 测试覆盖率
- 审查意见将在 72 小时内反馈

## 社区
- 加入我们的讨论组：[Discord/Slack 链接]（如果有）
- 关注项目动态：[Twitter/微博 链接]（如果有）

感谢您的贡献！
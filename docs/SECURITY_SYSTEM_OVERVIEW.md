# Security System Overview

## 🎯 Mission

保护用户免受恶意 skills 的攻击，同时保持自动化和可扩展性。

## 📊 实施方案

我们采用 **Schema 验证 + 安全扫描脚本（可手动/可集成 CI）** 的组合：

### ✅ 已实施的功能

| 组件 | 状态 | 功能 |
|------|------|------|
| JSON Schema | ✅ | 严格的 YAML frontmatter 验证 |
| Security Scanner | ✅ | 检测恶意代码模式 |
| GitHub Actions | ✅ | 数据同步 + 索引构建 |
| 文档 | ✅ | 完整的安全策略和使用指南 |

## 🛡️ 两层防护架构

```
┌─────────────────────────────────────────┐
│  Layer 1: Schema Validation             │
│  ├─ Required fields                     │
│  ├─ Pattern matching (name, version)    │
│  ├─ Length limits                       │
│  └─ Allowed licenses                    │
└─────────────────────────────────────────┘
              ↓ (Pass)
┌─────────────────────────────────────────┐
│  Layer 2: Security Scanning             │
│  ├─ Dangerous patterns (eval, exec)     │
│  ├─ Command injection                   │
│  ├─ Unsafe YAML loading                 │
│  ├─ Prompt injection detection          │
│  └─ Sensitive file access               │
└─────────────────────────────────────────┘
              ↓
         [Manual Review]
```

## 🔍 检测能力

### 自动拦截 (ERROR - 阻止合并)

| 威胁类型 | 检测模式 | 示例 |
|---------|---------|------|
| 代码执行 | `eval()`, `exec()`, `__import__()` | `eval(user_input)` |
| 命令注入 | `os.system()`, `shell=True` | `os.system(f"rm {file}")` |
| YAML 攻击 | `yaml.load()` | `yaml.load(content)` |
| 敏感文件 | `/etc/passwd`, `~/.ssh` | `open('/etc/passwd')` |
| Prompt 注入 | "ignore previous", "system:" | 见 SECURITY.md |

### 需要审查 (WARNING - 标记但不阻止)

| 类型 | 检测 | 建议 |
|-----|------|------|
| 网络访问 | `import requests` | 在 frontmatter 声明 `requires_network: true` |
| 文件删除 | `os.remove()` | 文档说明原因 |
| 子进程 | `subprocess.run()` | 避免 `shell=True` |

## 🤖 自动化工作流

### GitHub Actions 触发条件

```yaml
触发事件:
  - schedule (每天 00:00 UTC)
  - push to main (build index)
  - workflow_dispatch (手动触发)
```

### 执行步骤

1. **Sync Data** (`sync-data.yml`)
   - 发现新 skills
   - 下载/更新归档
   - 重建 registry.json
   - 推送 data + core 变更

2. **Build Index** (`build-index.yml`)
   - 基于 registry.json 生成搜索索引
   - 发布 GitHub Pages

## 📁 文件结构

```
skill-registry/
├── schema/
│   └── skill.schema.json           # JSON Schema 定义
├── scripts/
│   ├── security_scanner.py         # 安全扫描器
│   └── test_discovery.py           # 测试脚本
├── .github/workflows/
│   ├── sync-data.yml               # 数据同步
│   └── build-index.yml             # 索引构建
├── docs/
│   ├── SECURITY_GUIDE.md           # 使用指南
│   └── SECURITY_SYSTEM_OVERVIEW.md # 系统概览
└── SECURITY.md                     # 安全策略
```

## 🎬 使用示例

### 提交新 Skill

```bash
# 1. 创建 skill
mkdir -p skills/my-skill
cat > skills/my-skill/SKILL.md <<'EOF'
---
name: my-skill
description: A helpful skill that does something useful
version: 1.0.0
license: MIT
category: development
---

# My Skill

Instructions here...
EOF

# 2. 本地验证
python scripts/security_scanner.py skills/my-skill/SKILL.md

# 3. 提交 PR
git add skills/my-skill/
git commit -m "feat: Add my-skill"
git push origin my-skill

# 4. GitHub Actions 自动运行
#    - 安全扫描
#    - PR 评论
#    - CodeQL 分析
```

### 查看安全报告

```bash
# 扫描整个目录
python scripts/security_scanner.py skills/ --output report.json

# 查看结果
cat report.json | jq '.skills[] | select(.safe == false)'

# 严格模式 (WARNING 也会失败)
python scripts/security_scanner.py skills/ --strict
```


## 📊 监控指标

### 实时跟踪

- **通过率**: 通过安全扫描的 skills 百分比
- **平均分**: 所有 skills 的平均信任分数
- **认证比例**: 来自认证作者的 skills 百分比
- **更新率**: 90 天内更新的 skills 百分比

### GitHub Security Tab

- **Security Advisories**: 已发布的安全公告
- **Dependabot**: 依赖漏洞警报
- **Code Scanning**: CodeQL 发现
- **Secret Scanning**: 密钥泄露检测

## 🚨 威胁应对

### 发现恶意 Skill

1. **自动检测**
   - 安全扫描器标记
   - 社区报告
   - 异常行为监控

2. **响应流程**
   - 立即从 registry 移除
   - 发布 Security Advisory
   - 通知受影响用户
   - 封禁作者 (如果是故意)
   - 发布事后报告

3. **预防措施**
   - 加强检测规则
   - 更新文档
   - 社区教育

## 🔄 持续改进

### 已知限制

1. **无法检测零日漏洞** - 只能检测已知模式
2. **可能有误报** - 静态分析不是完美的
3. **对抗性攻击** - 攻击者可能绕过检测

### 改进方向

- [ ] 机器学习模型检测异常
- [ ] 沙箱动态执行测试
- [ ] 社区投票和举报系统
- [ ] 自动化渗透测试
- [ ] 行为分析和异常检测

## 📚 参考资源

- [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [Anthropic Prompt Injection Research](https://www.anthropic.com/research/prompt-injection-defenses)
- [Microsoft LLM Security](https://www.microsoft.com/en-us/msrc/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks)
- [YAML Security](https://www.kusari.dev/learning-center/yaml-security)
- [GitHub CodeQL](https://codeql.github.com/)

## 🤝 贡献

欢迎贡献安全相关的改进：

- 新的检测规则
- 误报修复
- 文档改进
- 测试用例

提交 PR 到 `security` 分支。

## 📄 License

MIT License - 详见 LICENSE 文件

---

**System Version**: 1.0.0
**Last Updated**: 2026-01-08
**Status**: ✅ Production Ready

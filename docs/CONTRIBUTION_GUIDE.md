# Skill Registry 收录指南

本文档说明社区提交如何被审核和收录到 Claude Skills Registry。

收录规则以 core 仓库的 [CONTRIBUTING.md](../CONTRIBUTING.md#requirements)
为准。`claude-skill-registry-core` 维护来源、审核与发布流程；
`claude-skill-registry-data` 保存归档；`claude-skill-registry` 是生成的发布产物。
不要直接修改发布仓库中的生成文件。

## 实体类型

Registry 有两种实体：

| 类型 | 说明 | 收录方式 |
|------|------|----------|
| **Skill** | SKILL.md 及其运行所需的随附文件 | 自动发现；社区提交经审核后加入来源列表 |
| **Plugin** | Claude Code 插件包（多个 skills + commands + hooks） | 手动审核 |

## Skill 收录流程

### 自动爬取（主要途径）

Registry 通过 GitHub 搜索和来源列表自动发现候选 Skill，由
`scripts/sync_and_download.py` 编排同步和下载。

发现 `SKILL.md` 不等于完成审核或获得维护者推荐。社区提交需要有效的
frontmatter、允许归档与再分发的许可证、完整安装步骤和可用的任务流程，
并通过适用的来源验证与安全检查。

**来源与分类：**

- 社区 Skill 来源维护在 `sources/community.json`；Plugin 来源维护在 `sources/plugins.json`。
- 自动发现使用 `scripts/discover_by_topic.py` 等流水线入口；无需为普通社区提交修改历史批量导入脚本中的仓库列表。
- 分类以 [taxonomy/categories.yaml](../taxonomy/categories.yaml) 为准。自动发现根据 SKILL.md 的 frontmatter、正文和路径等语义进行分类；弱信号或有歧义的结果保留为 `other` 并记录原因。

### 手动提交

通过 core 的 [Add Skill 表单](https://github.com/majiayu000/claude-skill-registry-core/issues/new?template=add-skill.yml)
提交，或向 core 发 PR，在 `sources/community.json` 末尾追加条目。
保留现有条目的内容、顺序和格式；`path` 填 Skill 所在目录相对于仓库根目录的路径，
根目录 Skill 使用空字符串。Issue 表单中的 Skill path 则可填写完整的 `SKILL.md` 路径。

提交中说明用途、类别、许可证文本、安装依赖、账号与密钥需求、费用，以及本地修改和外部数据发送行为。
维护者完成审核后，通过 core 同步、data 归档、core 索引生成和 main 发布流程交付。
合并来源条目不等于已完成归档与发布；核验实际产物后再关闭收录申请。

### 商业服务与推广

商业 API 集成、作者另售付费产品的免费工具，都可以申请收录。
关键是提交的 Skill 在已披露的前提下能完成具体任务；只有广告、推荐购买或引导注册，
缺少可用任务流程的内容不接受。

- 说明是否需要联网、账号、API key、免费额度和付费使用，并提供当前价格页面；开源 Skill 不代表外部服务免费。
- 区分安装阶段与正常运行阶段，说明脚本、依赖、凭据保存位置和配置修改。
- 说明向哪个服务发送哪些数据、用途是什么，并保持 README 与 SKILL.md 一致。
- 注册时发送个人数据、订阅、付款和修改 shell 配置前，应取得用户明确授权；安装 Skill 本身不构成这些操作的授权。

隐瞒费用、未经授权注册或修改配置、收集与任务无关的凭据，均可拒收。
在条目现有 `description` 中标明“需账号与计费 API”或“依赖远程 MCP”等重要条件。
收录与推荐分开；安全扫描通过不证明功能有效，维护者推荐还需要记录实测版本、范围与限制。

### 在本地验证提交的 Skill

提交 Issue 或 PR 前，请确认源仓库公开且无需登录即可访问，并确认
`SKILL.md` 以 YAML frontmatter 开头，其中 `name` 和 `description` 均为非空值：

```yaml
---
name: your-skill-name
description: 清楚说明这个 Skill 的用途。
---
```

Skill 及其随附文件不得包含密钥、令牌或私有端点。在已安装本仓库依赖的
core checkout 中运行：

```bash
# PR 修改 sources/community.json 时，验证来源条目
python scripts/validate_sources.py community.json

# 扫描本地检出的 Skill 目录及其随附文件
python scripts/security_scanner.py /path/to/skill-directory
```

以上是普通社区提交所需的检查。`python scripts/rebuild_registry.py --help` 和
`python scripts/build_search_index.py --help` 仅用于了解维护者的全量归档构建接口；
普通提交不需要在本地重建 registry 或搜索索引。

## Plugin 收录流程

Plugin 是 Claude Code 的打包扩展，包含多个 skills、commands、hooks 等。

### 判断标准：何时用 Plugin 而非多个 Skill

| 信号 | → 收录为 |
|------|----------|
| 仓库中有 1 个独立 SKILL.md | Skill |
| 仓库中有多个**无关联**的 SKILL.md | 多个独立 Skill |
| 仓库中有多个**内聚**的 SKILL.md，共享安装命令 | Plugin |
| 提供 `npx` / 一键安装，捆绑 skills + commands + hooks | Plugin |
| 同一个 skill 在仓库中存在多个副本（template/plugin/...） | Plugin（避免重复） |

### 提交要求

同样通过 core 的 Add Skill 表单提交，并注明为 Plugin；除上述许可和依赖披露外，需包含：
- 仓库 URL
- 安装命令
- 包含的 skills 列表
- 包含的 commands 列表（如有）
- 类别和标签

### 审核流程

1. **验证仓库**：clone 并确认 SKILL.md 文件存在且质量合格
2. **安全扫描**：检查 Skill 及随附文件；扫描结果不替代功能验证
3. **分类决策**：根据上方判断标准决定收录为 Skill 还是 Plugin
4. **写入源文件**：手动添加到 `sources/plugins.json`
5. **构建与发布验证**：由维护者通过 core 流水线生成并发布，核验对应版本的实际产物
6. **关闭 Issue**：完成归档与发布核验后回复提交者收录结果

### 源文件格式

Plugins 维护在 `sources/plugins.json`：

```json
{
  "plugins": [
    {
      "name": "plugin-name",
      "description": "简短描述",
      "repo": "owner/repo",
      "category": "product",
      "tags": ["tag1", "tag2"],
      "install": "npx package-name@latest",
      "homepage": "https://...",
      "author": "github-username",
      "skills": ["skill-a", "skill-b"],
      "commands": ["/cmd:a", "/cmd:b"],
      "hooks": ["pre-tool-use"],
      "source_url": "https://github.com/owner/repo",
      "license": "MIT"
    }
  ]
}
```

Schema 定义：`schema/plugin.schema.json`

## 去重策略

- Skill 级别：`repo:path` 作为唯一键去重
- Plugin 级别：`name` 唯一，不与 skill 条目合并
- Plugin 内的子 skill **不会**被拆解为独立 skill 条目（避免膨胀）

## 分类列表

Skill 类别使用 [taxonomy/categories.yaml](../taxonomy/categories.yaml) 中的有效 slug；
Plugin 条目还需符合 [schema/plugin.schema.json](../schema/plugin.schema.json)。

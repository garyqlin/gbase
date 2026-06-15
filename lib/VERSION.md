# Gbase 版本管理制度 v1.0

## 核心理念

> 底盘（Base）不动刀，插件（Plugin）可迭代。

## 底盘定义（16 个文件，非必要不改）

| 文件 | 职责 | 大小 | 状态 |
|------|------|------|:--:|
| `kernel.py` | 核心运行循环（run → _loop → tool_call） | 56KB | ✅ 稳定 |
| `session.py` | Session 管理（JSONL 读写/构建上下文） | 17KB | ✅ 稳定 |
| `mirror.py` | 鉴面引擎（三层记忆 hot/warm/auto） | 63KB | ✅ 稳定 |
| `toolkit.py` | 工具注册/扫描/分发 | 24KB | ✅ 稳定 |
| `experience.py` | 经验引擎（提取/沉淀/检索） | 25KB | ✅ 稳定 |
| `storage.py` | 数据存储（SQLite dat.db） | 14KB | ✅ 稳定 |
| `tracer.py` | Trace 追踪（调用链记录） | 9KB | ✅ 稳定 |
| `identity.py` | 身份加载（system_prompt） | 4KB | ✅ 稳定 |
| `territory.py` | 领地边界（操作权限隔离） | 8KB | ✅ 稳定 |
| `scheduler.py` | 定时调度（cron 引擎） | 18KB | ✅ 稳定 |
| `sleep_cycle.py` | 离线巩固（session 整理/mirror 修剪） | 10KB | ✅ 稳定 |
| `daily_memory.py` | 日记忆引擎（跨 session 注入） | 16KB | ✅ 稳定 |
| `monitor.py` | 系统监控 | 2KB | ✅ 稳定 |
| `safe_shell.py` | 安全命令执行 | 4KB | ✅ 稳定 |
| `fetcher.py` | URL 获取（供经验引擎使用） | 4KB | ✅ 稳定 |
| `backup.py` | 文件备份（供 write_file 工具依赖） | 5KB | ✅ 稳定 |

**底盘改动规则：**
1. 仅允许 P0 级修复（内存泄漏、安全漏洞、数据损坏、系统崩溃）
2. 每次改动必须：写 CHANGELOG → 单功能 commit → 回归测试 → 全舰队重启验证
3. 禁止：优化重构、新增功能、删除"未使用"代码（先标记 LEGACY，等主人审批）

## 插件定义（独立迭代，不影响底盘）

| 分类 | 位置 | 说明 |
|------|------|------|
| **工具** | `gundam-home/tools/` | 78 个工具文件，Agent 特有 |
| **规则** | `gundam-home/rules/` | 21 个 .md 规则文件 |
| **技能** | SkillHub skills/ | YF-ha-operator, YF-sonos-dual-tts 等 |
| **知识** | dat.db knowledge 表 | 运行时积累，自动管理 |
| **身份** | SOUL.md / IDENTITY.md | 每个 Agent 有自己的身份文件 |
| **定时任务** | cron/ 目录 | 学习任务、质量评分等 |

## 当前版本

- **Gbase 底盘版本**: v1.0.0（2026-06-15）
- **最后底盘修改**: commit 070497b（修复 _llm_compress_sync 引用断裂）
- **共享方式**: 4 Agent 通过硬链接共享 gbase-lib
- **OpenClaw 底座**: 2026.4.21-5（参考稳定性标杆）

## 版本号规则

- 底盘: `Gbase-Base vX.Y.Z`
  - X: 架构级重构（kernel 循环重写、storage 引擎更换）
  - Y: P0 修复累积（非架构变更的安全/稳定性修复）
  - Z: 微小热修复
- 插件: 独立版本，不追踪全局版本号

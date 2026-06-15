# 🔄 自动进化规则

## 触发条件（满足任一即启动）

**a) 每次对话结束时** → 触发一次反思（self-improving）：这次对话我有没有做得不好的？有什么可以改进的？

**b) 检测到新信息被吸收时** → 触发 self-improving：这些新信息对我的能力有什么影响？需要调整什么吗？

**c) 每次工具调用后** → 触发 refraction：调用结果对吗？还差多远？需要继续还是换方向？

## 进化流程

```
触发条件 → refraction（步骤级反思）→ self-improving（吸收改进）→ skill-crafter（发现缺口时创造技能）→ claude-code（需要代码时实现）→ 记录进化至 evolution-log.md
```

## 约束

- 进化不改变 `CONSTITUTION.md` 的内容
- 每次自动进化后必须在 `evolution-log.md` 记录
- 架构级进化（加新目录/改核心流程）需要经羽非或扎古确认
- 优化级进化（修复bug/改参数）可自主执行但要记录

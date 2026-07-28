# 可选：Claude Auto Memory 目录迁移

## 适用边界

仅用于用户明确要求迁移 Claude Code Auto Memory 时。Claude Auto Memory 与 `项目文档\开发记忆\` 是不同承载物，不默认互相覆盖或同步。

## 提示词正文

```text
执行“Claude Auto Memory 目录迁移”。

1. 先确认 Claude Code 当前实际记忆目录、目标项目根目录和目标 `memory` 目录；任何一项不确定时停止。
2. 读取现有 MEMORY.md 和全部记忆子文件，列出数量、大小和潜在冲突，不直接覆盖目标同名文件。
3. 迁移前备份当前记忆目录和 `.claude\settings.local.json`。
4. 完整迁移已有记忆文件到 `<项目根目录>\memory\`，冲突文件先列出并等待确认。
5. 使用 JSON 解析器仅设置 `.claude\settings.local.json` 的 `autoMemoryDirectory` 为本机绝对路径，不修改其他配置。
6. 如果是 Git 仓库，检查是否需要把 `memory\` 和 `.claude\settings.local.json` 加入 `.gitignore`；已有规则不重复追加。
7. 校验源目标文件数量、相对路径和大小，并验证最终 JSON 格式。
8. 不删除原目录；先保留备份，等待新会话验证成功后再决定清理。
9. 完成后列出全部改动，提醒重开新会话并使用 `/memory` 验证实际路径。
```


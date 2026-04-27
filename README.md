# Minecraft 入群正版验证

这是一个 AstrBot 插件，用于自动处理 QQ 群加入申请中的 Minecraft 正版 ID 校验。

插件会读取 OneBot `aiocqhttp` 的入群申请事件，优先取验证信息中 `答案：` 后的内容作为 Minecraft Java 版 ID，并调用 Mojang Username -> UUID 接口验证该 ID 是否存在。

## 行为

- ID 存在：不做动作，保留给管理员或其他流程处理。
- 查询接口超时、限流或异常：不做动作，避免误拒。
- ID 不存在：自动拒绝，并返回配置的拒绝理由。
- 申请答案不是完整 ID：默认自动拒绝，可在配置中关闭。
- ID 存在且管理员手动同意入群后：默认把新成员群名片改为申请中的 Minecraft ID。

## 配置

插件支持在 AstrBot WebUI 中配置 `_conf_schema.json` 暴露的选项。常用项：

- `target_group_ids`：目标 QQ 群号，留空表示所有群。
- `reject_when_no_username`：申请答案不是完整 ID 时是否拒绝。
- `reject_reason`：申请答案不是完整 ID，或查询不到该 ID 时的拒绝理由。
- `enable_set_group_card`：入群后是否自动修改群名片。
- `group_card_template`：群名片模板，可使用 `{username}`、`{group_id}`、`{user_id}`。
- `dry_run`：试运行模式，只记录日志不实际拒绝。

## 申请信息示例

```text
Steve
问题：请只填写自己的正版ID
答案：Steve
```

## 注意事项

- 仅支持 OneBot v11 / `aiocqhttp` 适配器。
- 机器人账号需要在目标 QQ 群拥有处理入群申请、修改群名片的权限。
- 如果 AstrBot 开启了会话白名单，请将目标群号加入白名单。

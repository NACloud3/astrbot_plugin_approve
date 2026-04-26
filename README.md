# Minecraft 入群正版验证

这是一个 AstrBot 插件，用于自动处理 QQ 群加入申请中的 Minecraft 正版 ID 校验。

插件会读取 OneBot `aiocqhttp` 的入群申请事件，从验证信息中提取 Minecraft Java 版 ID，并调用 Mojang Username -> UUID 接口验证该 ID 是否存在。

## 行为

- ID 存在：不做动作，保留给管理员或其他流程处理。
- 查询接口超时、限流或异常：不做动作，避免误拒。
- ID 不存在：自动拒绝，并返回配置的拒绝理由。
- 未填写或无法提取 ID：默认自动拒绝，可在配置中关闭。
- ID 存在且管理员手动同意入群后：默认把新成员群名片改为申请中的 Minecraft ID。

## 配置

插件支持在 AstrBot WebUI 中配置 `_conf_schema.json` 暴露的选项。常用项：

- `target_group_ids`：目标 QQ 群号，留空表示所有群。
- `username_patterns`：从验证信息中提取 ID 的正则表达式。
- `reject_when_no_username`：无法提取 ID 时是否拒绝。
- `reject_reason_not_found`：ID 不存在时的拒绝理由，可使用 `{username}`。
- `enable_set_group_card`：入群后是否自动修改群名片。
- `group_card_template`：群名片模板，可使用 `{username}`、`{group_id}`、`{user_id}`。
- `dry_run`：试运行模式，只记录日志不实际拒绝。

## 申请信息示例

```text
正版ID: Steve
Minecraft ID: Steve
我的ID是Steve
Steve
```

## 注意事项

- 仅支持 OneBot v11 / `aiocqhttp` 适配器。
- 机器人账号需要在目标 QQ 群拥有处理入群申请、修改群名片的权限。
- 如果 AstrBot 开启了会话白名单，请将目标群号加入白名单。

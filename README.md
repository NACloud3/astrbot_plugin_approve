# Minecraft 入群正版验证

这是一个 AstrBot 插件，用于自动处理 QQ 群加入申请中的 Minecraft 正版 ID 或 UUID 校验。

插件会读取 OneBot `aiocqhttp` 的入群申请事件，优先取验证信息中 `答案：` 后的内容作为 Minecraft Java 版 ID 或 UUID。插件会先调用 Mojang Username -> UUID 接口验证 ID；如果未命中且答案像 UUID，会删除短横线后调用 UUID 查询接口。

## 行为

- ID 或 UUID 存在：不做动作，保留给管理员或其他流程处理。
- 查询接口超时、限流或异常：不做动作，避免误拒。
- ID 不存在：自动拒绝，并返回配置的拒绝理由。
- 申请答案不是完整 ID 或 UUID：默认自动拒绝，可在配置中关闭。
- ID 或 UUID 存在且管理员手动同意入群后：默认把新成员群名片改为查询到的 Minecraft ID。

## 配置

插件支持在 AstrBot WebUI 中配置 `_conf_schema.json` 暴露的选项。常用项：

- `target_group_ids`：目标 QQ 群号，留空表示所有群。
- `uuid_lookup_url_template`：UUID 回退查询接口，默认使用 Minecraft Services 接口。
- `reject_when_no_username`：申请答案不是完整 ID 或 UUID 时是否拒绝。
- `reject_reason`：申请答案不是完整 ID 或 UUID，或查询不到该 ID 时的拒绝理由。
- `enable_set_group_card`：入群后是否自动修改群名片。
- `group_card_template`：群名片模板，可使用 `{username}`、`{group_id}`、`{user_id}`。
- `dry_run`：试运行模式，只记录日志不实际拒绝。

## 申请信息示例

```text
Steve
问题：请只填写自己的正版ID
答案：Steve
答案：853c80ef-3c37-49fd-aa49-938b674adae6
```

## 注意事项

- 仅支持 OneBot v11 / `aiocqhttp` 适配器。
- 机器人账号需要在目标 QQ 群拥有处理入群申请、修改群名片的权限。
- 如果 AstrBot 开启了会话白名单，请将目标群号加入白名单。

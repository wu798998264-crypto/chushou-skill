# 即梦全能画布 CLI

官方入口：https://jimeng.jianying.com/canvas-cli 。当前核对版本 1.0.1，命令为 `dreamina-canvas`，不是 npm 展示页中的同名示例，也不是旧 `dreamina`。

神思浏览器：当前配置的即梦网页使用 `webcanvas.canvas_auth` 查询/授权；`webcanvas.canvas` 用 `schema` 列命令，以 `describe` 取得具体命令字段，再执行。账号逐浏览器配置隔离，不能借用全局或其他配置。

本机终端：先 `dreamina-canvas --format json --profile <明确配置> schema`、`auth status`。按官方技能及实际 schema 使用命令，不编造 flag。神思浏览器配置不能仅凭同名 CLI profile 冒称已打通；凭据仍由官方系统存储保存。

- `canvas ls/create` 列出/创建画布，返回官网 webUrl；所有 node/resource/operation 命令明确传 project-id。
- `node find/show` 读取已有文字、参数、资源和关系。创建/编辑支持 text/image/video/audio/element/timeline。
- 图片参考以 `ref: ["node:真实NodeID"]` 建立连接，正文用 `{{node:真实NodeID}}` 标记位置。回读 generation.references、upstreamNodeIds 和 promptParts，不能仅凭缩略图判定连线正确。
- `canvas_upload` 只接受真实笔记附件，用户确认后上传。建立带 resource-id 及 import-kind 的图片节点，后续使用其 Node ID。
- `canvas_download` 下载 Resource ID 并校验大小/SHA256，出现在浏览器下载列表。资源权限拒绝时不绕过。
- 选模型先实时 `model list --type image|video|audio` 或 `model find`，只用返回的 canonical model；aliases 不能当提交型号。官方未明确映射的 GPT 型号不猜。
- 每次传递真实 count；省略为1。保存草稿不运行。`node quote` 只报价，`node run` 经神思确认当前节点及积分上限后执行；不自动确认未知金额。
- 等待超时不等于远端失败；保留 projectId/nodeId/updateId/逐项submitId，用 operation status/wait 找回，不新建任务补单。确认 token 不落盘、不输出。
- 5.5已在画布目录出现；无限生成权益不是 CLI 的已开放参数。目录、草稿保存、报价通过与生成成品通过分开报告。

本轮已实测创建画布、编辑文字、5.5单图草稿、上传合成图、正文引用及连线、素材下载。经单独授权，5.5单图和2.5 Flash带图4秒视频各生成一次，均下载并完整解码通过。不能把这两项结论扩展为全部型号、多图或并发验收通过。

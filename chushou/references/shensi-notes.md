# 新版神思笔记白板适配

新画布任务未指定目标时使用本适配。用户指定的旧创作引擎／网页平台和既有绑定优先；本页不授权迁移旧任务或提交视频。共享来源链、参考规则与阶段范围见 [canvas-adapters.md](canvas-adapters.md) 和 [shensi-generation.md](shensi-generation.md)。

下述结构已由当前神思笔记源码的 `apps/desktop/server.mjs`、`packages/core/src/service.mjs`、`packages/ui/generation-*.js` 核验；**源码核验不等于当前任务已实测导入**。应用更新后先核实当前版本契约与UI，记录适配协议，不能硬套版本号或作者机器路径。

## 发现与目标绑定

- 先运行随包 `scripts/shensi_canvas_probe.py`。新版可能由Electron主进程内嵌服务，以相关进程的实际本地监听端口及首页 `meta name="shensi-session"` 识别；不能只找 `node server.mjs --port`，也不能将旧引擎的 `shensi-session-token` 当新版。
- 当前新版使用 `X-Shensi-Token` 会话头；令牌仅在内存内从真实首页握手读取，不打印、不保存、不放入绑定或素材包。服务重启重新发现。仅连接明确的本机实例，不把会话头发送至外部站点。
- 通过真实应用UI或当前接口读取库列表、选择或创建本任务笔记本，再选择／创建白板。当前 `GET /api/bootstrap` 提供 `libraries` 及版本／能力；只保留本次目标所需非秘密字段，不整份输出用户库及配置。新建笔记用 `POST /api/library {name}`，白板用 `POST /api/document`，参数包含 `id`（真实笔记本ID）、新读的 `expectedStateStamp`、`kind:"whiteboard"`、`document:{title,canvas}`、`source:"agent"`、本次稳定 `operationId`。读取真实返回ID，不猜目录条目或手拼注册数据。
- 绑定至少记录 `protocol:"notes"`、`adapter:"shensi-notes"`、当前 `origin`、真实 `notebookId/documentId` 与非秘密操作标识；探测器的 `--binding-file` 使用其中 `protocol/origin`。端口只是当前会话位置，不是永久目标ID；旧地址失效后重新动态发现，再核验同一笔记和白板身份并更新会话地址，不能改绑别的笔记。多实例无法唯一识别时才局部询问，不任取第一个。

## 附件、原视频与源帧

当前读写为 `GET /api/state?id=<真实notebookId>` 和 `POST /api/state {id,state,expectedStateStamp}`；响应中的 `state/stateStamp` 按当前实例回读。已有内容先读最新状态，仅合并本任务节点、边和草稿；保留其他文档、历史、运行任务及用户修改。冲突后重读并重新合并，不强制覆盖。

`POST /api/attachment` 当前接受 `{id,name,mimeType,base64,whiteboardDocumentId,whiteboardMediaKind}`，返回附件对象本身；不能按旧接口再取不存在的 `.attachment`。上传实际文件后使用返回的真实 `relativePath`。当前原生上传控件单文件上限55MiB、JSON请求体上限80MiB，base64有体积膨胀；操作前核实现行限制。未核实新版multipart／大文件导入，不照搬旧引擎的方式，也不直接向应用托管目录写文件冒充附件。

媒体节点位于 `state.documents[documentId].canvas.nodes`，示意：

```json
{"id":"本次稳定源视频节点ID","type":"file","kind":"video","file":"上传返回的relativePath","name":"原视频","mimeType":"video/mp4","x":0,"y":0,"width":360,"height":203}
```

尺寸、`durationMs/videoWidth/videoHeight` 使用真实媒体信息。先让原视频可播放，再把源帧放在其右侧。每张源帧登记原片SHA、实际PTS／源范围和对应段号，建立真实边：

```json
{"id":"本次稳定来源边ID","fromNode":"原视频真实节点ID","toNode":"源帧真实节点ID","fromSide":"right","toSide":"left"}
```

原生圆口偏移和连线渲染交给应用，不能用卡片上方坐标或画线替代。相同原视频和源帧按SHA复用，补全边而不重复上传。

这些边用于展示“截取自／分析后形成文字设计／用于该段”的来源路径。默认纯文字生图，源帧与资产的来源关系不能自动转成图片模型参考；仅用户明确特殊要求时才将指定截图作为生图输入。只补来源关系不触发生成；各通道真实模型输入由草稿白名单决定。核实当前实现不会绕过白名单，不能仅凭连线声称参考已绑定。

当前原生抽帧 `POST /api/board/media/edit` 可使用 `{id,documentId,nodeId:源视频节点ID,expectedStateStamp,operationId,action:"frame",position:"current",currentTimeMs}`。它会创建图像节点、记录 `coreMediaEdit.sourceNodeId` 并建立源视频→结果帧连线。调用后读取真实结果与媒体时间；`currentTimeMs` 是请求定位，不自动等于已核实的解码PTS。已有FFmpeg帧直接导入并补相同来源边，按 [video-inspection.md](video-inspection.md) 的实际PTS登记，不为连线重复抽帧。

## 分段生成卡与独立草稿

卡片使用真实文本节点及 `generationIntent:{channel:"video"}`。卡片正文仅写段号、标题、原片范围、预计秒数→整秒时长等导航信息，完整五项提示词只进入生成操作栏。资产列在源帧右侧，生成卡最右侧按段号纵排；参考节点共享并连到各自实际下游。

新版草稿在 `canvas.coreGenerationDrafts[本段节点ID]`，不是旧 `/api/whiteboard/generation-drafts` 的scope/key；字段示意：

```json
{
  "channel":"video","profileId":"当前真实配置ID","model":"当前真实模型",
  "prompt":"本段完整提示词，按实际映射插入原生引用",
  "aspectRatio":"16:9","durationSeconds":8,"generateAudio":true,
  "references":["验收后的角色资产节点ID","验收后的场景资产节点ID"],
  "promptReferenceSequence":["按提示词每次出现顺序的真实节点ID"]
}
```

示意中的8秒替换为本段时间轴的 `ROUND_HALF_UP` 整秒结果；默认16:9，明确要求优先。保留原稿与各 `channels.video` 等原生快照，不清除其他通道。配置／模型支持的实际秒数、比例和生成模式通过当前模型目录及UI确认，写JSON成功不等于UI接受。不得套用旧版 `connectionId/duration/explicitReferences` 字符串字段。

`references` 只列本段实际模型输入，**不自动递归加入原片、截图或所有祖先**。除非用户专门要求将原视频截图／抽帧用于本段视频生成，否则不得将其写入 `references`、`promptMentions`、`promptReferenceSequence`、`channels.video` 快照或生成输入边；裁切图及联系表同样适用。来源展示用独立关系／说明节点或已核实的白名单隔离，不能仅靠“分析用”标签。先写明确白名单，再核验生成栏中的缩略图；专门授权仅限指定素材、段落与用途。新版 `generation-references.js` 的图片／视频编号依整个 `references` 数组位置计算，例如图像A、视频B、图像C可显示为图片1、视频2、图片3；音频等其他引用可使用节点语义别名。按当前UI回读编排 `@「…」` 与每次引用序列，不能照搬旧版按类型分别编号的示例。需要引用位置身份信息时保留原生 `promptMentions`，不自造偏移。

文内位置同时遵守 [canvas-adapters.md](canvas-adapters.md)：初始空间关系中插入对应已有参考；正式剧情中每个人物、场景、剧情道具首次出现处再次插入，每个独立段重新计数。重复文内引用沿用同一节点，不重复上传或增加 `references` 项，但每次出现都记录到 `promptReferenceSequence` 与匹配的原生 `promptMentions`。顶层及原生 `channels.video` 快照保持一致，逐卡回读富引用及参考缩略图。

外部Codex资产图保存原始生成提示词、上游来源和实际文件身份。使用当前原生资产／版本契约登记，不能伪造神思job或仅把白板图片可见当作图库已登记；来源关系和再生成草稿保持对应。

## 收起与持久化验收

准备完使用原生收起路径（当前版本点击白板空白处／界面 `session.dismiss` 行为），不按 `data-generation-close` 明确关闭按钮，不设置 `coreGenerationAutoOpenDisabled`，也不清除用户原先主动关闭的标记。收起后单击卡片应恢复该卡草稿；显式关闭仍遵从应用设计。

保存后停止本轮后台写入，再原生UI回读并刷新：原视频可播放、帧来源和右→左边正确、资产可复用、段序与位置正确；逐卡提示词／整秒时长／比例／参考缩略图一致，初始空间关系及正文首出引用齐全。图片输入无未获准源帧，获准截图限于指定资产及用途；视频输入默认无源帧，专门授权的例外须逐项核对截图、目标段落及用途，不能混入其他分析帧。收起后点击可恢复。只通过API或源码检查时明确剩余UI验收项，不能报 `bound/verified`。

本页不提供新版视频提交脚本。用户已明确要求且已核对本次具体生成信息时，使用当前原生生成操作或已核实的新接口；旧 `shensi_bridge.py submit`、旧任务同步脚本均不可投到本协议。仅更新规则、准备卡片或补来源连线不启动图片／视频测试生成。

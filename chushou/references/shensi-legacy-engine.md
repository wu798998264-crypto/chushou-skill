# 旧神思创作引擎适配

**本页仅适用于已识别为旧创作引擎的实例**，包括该引擎自身的笔记模式；它不是新版「神思笔记」协议。先按 [shensi-generation.md](shensi-generation.md) 路由。`scripts/shensi_bridge.py` 及本页的 `/api/workspace/*`、托管路径、generation-drafts key、multipart 和任务接口均不得直接套到新版神思笔记。新任务未指定平台时以新版神思笔记为默认，旧引擎用于用户指定或延续既有绑定。

旧画布同样遵守 [canvas-adapters.md](canvas-adapters.md) 的原片溯源链：真实原视频位于源帧左侧，与对应帧通过原生右口→左口连线；来源边不自动作为模型引用。原视频、源帧及生成参考分别记录，原帧仍不直接作为视频生成参考。旧入边编号规则只用于当前实际模型引用，不能把新增溯源节点顺带加入生成草稿。

使用神思本机媒体后台，复用设置界面的全部图片／视频配置。`scripts/shensi_bridge.py` 只需要 Python 3.11+ 标准库；命令示例的 `python` 替换为当前宿主实际可用的Python解释器；不要求作者电脑上的安装目录。

## 启用条件

图片默认用Codex自身内置图像生成；只有用户明确指定即梦／LibTV或提出需要这些通道的特殊要求时才用神思对应图片配置。连接可用、账号已登录、画面复杂或内置失败均不改变默认通道。神思里的OpenAI CLI／聚合API不是Codex内置生成。

用户要求准备画布、草稿或参考绑定时可直接完成准备；视频只有在用户明确要求并核对本次具体信息后才提交。提交前必须展示本次具体配置与模型、分段数量和内容、每段与总时长、画幅、分辨率、对白与音频、完整实际提示词、参考图映射及插入位置，等待用户核对确认。仅上传视频、调用技能、说「继续」或接通神思不等于视频生成确认。核对期间可完成分镜、缺失图片、参考映射与参数准备，不能先提交视频任务。

## 连接与配置

```powershell
python scripts/shensi_bridge.py catalog --out output/shensi/profile-catalog.json
```

脚本从正在运行的神思后台进程发现端口，通过应用首页的标准本地会话握手调用 API。会话令牌仅在内存中，不写入文件、不展示；不复制厂商密钥。需要指定实例时，在子命令前加 `--origin http://127.0.0.1:实际端口`。服务重启后重新运行即可，不保留过期端口。

安装目录从当前用户环境或运行实例发现，持久数据由应用管理。应用未运行时启动已安装神思并重新连接，不能另起一个缺少已登录凭据的源码服务器。发现多实例时核对正在使用的实例。

`catalog` 输出每个通道的 `activeProfileId`、全部 `profiles` 和 `bindingRegistered`。神思启用条件满足后使用用户指定配置的精确ID；未指定账号时使用神思当前对应配置，并将视频配置列入核对信息。每次任务重新读取，以自动识别新增配置。绑定已注册仅表示本机调用入口存在，不证明登录、额度或厂商任务成功。配置失效时报告实际原因，不声称全部配置都已实测可生成。

## 默认笔记模式与专用托管工作区

Codex及其他agent操作神思默认使用笔记模式，创建或复用本任务笔记本。用户明确要求作品模式才用project；不能把“本地项目目录”理解成用户要求神思作品模式。工作区类型必须来自真实绑定，上传、生成目标、草稿key和请求均采用同一类型。

```powershell
python scripts/shensi_bridge.py workspace --kind notebook --name "触手" --operation-id "本任务稳定连接编号" --out output/shensi/workspace-binding.json
python scripts/shensi_bridge.py target --binding-file output/shensi/workspace-binding.json --operation-id "本项生成稳定编号" --out output/shensi/task-target.json
```

`workspace` 默认notebook，通过神思原生笔记本接口创建／复用，并用安装版空笔记本工厂初始化新空笔记本。`--kind project`只用于用户明确要求作品或继续明确指定的既有作品。已有绑定仍按记录中的类型连接，不静默改写其类别；切换时另建绑定并复制本任务内容，保留源工作区。神思安装位置不同时用 `workspace --app-root 实际resources/app目录` 指定工厂。

真实笔记接口为 `GET /api/notebooks/list`、`POST /api/notebooks/create`，创建参数`{name,operationId}`；create仅建托管目录，新的空笔记本还需安装版`createBlankNotebookState`初始化并保存。既有笔记本不重置。作品白板迁入笔记时使用原生`/api/workspaces/documents/transfer`的copy操作，检查真实返回的documentId及每个附件路径／SHA；生成操作栏草稿不随白板自动复制，需要另行重绑workspaceId、documentId与keys。源生成任务继续留在原工作区，不能伪装成笔记任务。

不要直接将当前外部工作目录传为 `target.workspacePath`，神思只允许自己的托管目录。桥接使用真实工作区的对话ID及独立消息编号作为 `conversation-message` 生成目标，`landAfterGeneration:false`，文件由后台落盘，不在其他对话插入消息。每项资产使用独立目标及稳定操作编号。

## 参考图和音视频

先查看参考图并盘点已有资产，只导入本次生成确实需要的参考。

```powershell
python scripts/shensi_bridge.py upload --binding-file output/shensi/workspace-binding.json --file "参考图绝对路径.png" --out output/shensi/reference-student.json
```

返回记录中的 `attachment` 放入参数文件的 `referenceMedia` 数组。使用完整返回信息，尤其 `id/relativePath/name/mimeType`；视频／音频参考需补实际 `durationSeconds`。不要将外部绝对路径直接写到 `relativePath`。重复导入记录会复用相同内容。单项 JSON 导入上限40MiB，更大的媒体用神思原生 multipart 附件导入：`X-Shensi-File-Size` 必须是真实大小。

布局参考仅约束版式，角色身份参考约束面容／服装。按各模型允许的引用数量设置参数与提示词；保留原有资产映射，避免角色互换。

## 智能插入参考图

先查看图片内容并把每段实际出现的角色、场景与已确认必要道具关联到已有资产。该段「初始站位／初始空间关系」提及的全部人物、场景和道具也属于必需参考范围：将对应已有参考全部插入该处各自描述，重复提及时仍插入同一引用，并逐项完成本段真实连线、`explicitReferences`、`referenceOrder` 与 `promptReferenceSequence` 的一致映射；具体核验遵循 [canvas-adapters.md](canvas-adapters.md) 的共用不变量。不能用「只选该段需要的图」省略初始站位中的对象。建立可核对的编号、缩略图／路径、用途与实际附件ID映射，再写最终提示词。身份图约束脸、发型和衣服；场景图约束空间与光线；明确的首尾帧用于指定起止构图；设定图的真人白底三栏或3D灰底四栏版式均不能进入视频画面。不要把该段正文没有涉及的无关角色、版式模板或整个素材库全塞进一个片段。

把引用插入对应描述，例如：

```text
男学生外貌和服装参考 @「图片1」；办公室布局参考 @「图片2」，视频画面不出现参考图的白底三栏。
（00:00-00:03）中近景，固定拍摄，男学生 @「图片1」站在办公室 @「图片2」内，眉头收紧，目光看向画外老师……
```

此处 `@「图片N」` 是旧引擎已核实的本地引用标记。必须以本次实际参考图替换示例映射；不能只在提示词末尾堆文件路径或把示例场景带入别的视频。完整提示词和映射在提交视频前交用户核对，准备草稿和来源连线可继续完成。

LibTV 对提示词标记有确定的附件ID到上传节点映射：

```json
{
  "referenceMedia": [
    {"id":"image-A","relativePath":"实际附件路径A","name":"男学生.png","mimeType":"image/png","referenceRole":"attachment"},
    {"id":"image-B","relativePath":"实际附件路径B","name":"办公室.png","mimeType":"image/png","referenceRole":"attachment"}
  ],
  "referenceOrder": ["image-A","image-B"],
  "providerPromptReferenceTokens": ["@「图片1」","@「图片2」"],
  "promptReferenceSequence": ["image-A","image-B","image-A","image-B"]
}
```

上例对应提示词中两次身份／场景引用，序列必须按实际每次出现的顺序记录，重复出现不去重。`providerPromptReferenceTokens` 为去重后的标记表，每个序列ID恰好对应一个附件。原始 `request.prompt` 必须保留这些标记；神思会编译为真实 `{{Node 节点编号}}`，不要自行编造上传节点ID。

即梦还需 `preserveReferenceTokens:true` 才会保留这些本地标记。当前桥接按附件顺序向官方CLI传图片，未实现与LibTV相同的显式ID编译；因此同时写清「男学生面容和服装参考图片1」等自然中文用途约束，不把未经核实的 `@图片`／`素材1` 别名声称为厂商协议。引用效果须验收实际生成结果。

`referenceOrder` 不会自动重排服务端的附件，必须直接按确认顺序排列 `referenceMedia`。`referenceRole:"target"` 会前置，非首帧／目标编辑图通常使用 `attachment`；确需目标图时先排好，再编号与核对。混合图片、视频、音频时考虑CLI按类型分组的实际顺序，各类标记与用途分别明确。

## 分段卡片、操作栏与侧面端口

本节用于已获授权的神思视频准备或参考插入演示，创建草稿不代表提交视频。每段提示词对应一张独立 `generationIntent:{"channel":"video"}` 文本卡片及独立操作栏草稿。卡片 `text` 仅保留段号／标题、预计时长→整秒时长／比例、原片范围等导航信息；完整提示词只写入操作栏 `values.prompt`，其中不含导航信息。按剧情顺序在右侧同一列纵向排列，下一张的 `y` 至少为上一张的 `y+height+间距`。上游参考图在左侧，留出连线通道。同一资产上传一次、只保留一个共享节点，可连接多个下游；各段引用编号仍按各自入边顺序编号。

填写完成后使用收起／折叠或原生非 `Close` 的收起路径，保留单击已配置卡片直接展开对应操作栏的能力；不点击明确关闭按钮、不清除用户的自动展开禁用标记。继续使用 `type:"text"`、`kind:"text"`、真实 `generationIntent` 和原生草稿，不能伪造 `generation`、任务记录或 `kind:"video"` 来获得点击行为。神思的点击恢复逻辑应读取对应草稿／真实生成记录，并以 `generationIntent.channel` 兜底，同时保留显式关闭后禁止单击展开的设计。保留 DepthExplorer 操作节点的独立点击行为，不将其当作普通生成卡。保存后验收首次点击、收起后点击、刷新后点击和跨卡切换，核对各卡提示词、参数及参考均正确恢复，并确认显式关闭仍禁止单击展开；这些操作只打开草稿，不提交生成。

当前版本普通生成栏可通过点击白板空白处收起：`closeWhiteboardGenerationDialogsOutside` 只收起当前界面并保存草稿，不禁用下次单击。带 `data-close-whiteboard-dialog` 的“关闭”按钮则会设置自动展开禁用标记，agent准备完成时不得用它代替收起。

每段从最终提示词的镜头时间轴取得预计时长（本段结束减本段开始，不能用原片绝对结束时间），四舍五入为操作栏秒数：28.49→28、28.50→29、28.07→28。`shensi_bridge.py` 提供 `prompt_duration_seconds(prompt)` 与 `round_video_duration(seconds)`，使用十进制 `ROUND_HALF_UP`，不能用 Python 的偶数舍入 `round()`。`prepare --channel video` 自动读取单段镜头时间轴并填入整数 `request.duration`；无时间轴时才使用参数文件中的预计秒数。多段重置时间轴须拆成独立卡片／请求，不合并算成一个总时长。保留精确分镜时间，并在核对信息记录预计秒数和操作栏秒数。默认比例16:9，用户指定的其他比例同时写入提示词、操作栏和请求。

白板存于 `state.documents[documentId].canvas`，参考图节点的 `file` 使用导入附件的真实 `relativePath`。连线使用节点ID及侧面端口：

笔记的常规库目录在当前安装版使用 `workspaceView:"default"`；白板文档与 `moduleItems.library` 中该文档的元数据须使用相同视图。旧示例的 `general` 会使白板仅在已打开的标签页可见、目录列表为空。注册后用安装版目录构造器及真实UI核对目录能找到白板；修复只改本白板的这两处视图元数据，保留画布、草稿、任务与其他文档。

```json
{"id":"ref-A-to-segment-01","fromNode":"ref-A","toNode":"segment-01","order":0,"fromSide":"right","toSide":"left"}
```

`fromSide:"right"` 是上游卡片右侧居中的圆形输出口，`toSide:"left"` 是下游卡片左侧居中的圆形输入口。不能使用 `bottom/top`、卡片上方工具栏坐标或画一条装饰线代替真实连接。神思 `src/whiteboard.js` 的 `normalizeEdge/addCanvasEdge` 与 `src/app.js` 的 `whiteboardNodeSidePoint` 已核实该契约。每张卡的入边 `order` 决定参考编号，须与该段 `@「图片N」` 和参考顺序一致；编号在各段内独立核对。不要把另一张提示词卡连为上游参考而改变编号。

工作区先 `POST /api/workspace/load`（`{workspacePath,fresh:true}`），修改本任务白板后 `POST /api/workspace/save`，传新读到的 `expectedStateStamp` 和本次 `operationDocumentIds`，保留其他文档。操作栏不是画布节点的任意 `genConfig` 属性，须写原生 `POST /api/whiteboard/generation-drafts`：

```text
workspaceKind = binding.workspaceKind // 默认为notebook，必须取实际绑定
workspaceId = workspaceKind + ":" + workspacePath.trim().toLowerCase()
scope = {workspaceId, documentId, nodeId: 本段卡片ID, channel:"video"}
key = [workspaceId,documentId,nodeId,"video"].map(encodeURIComponent).join("::")
entry = {...scope, updatedAt, values:{
  prompt:本段完整提示词, connectionId:真实配置ID, model:真实模型,
  generationMode:"smart_params", aspectRatio:"16:9", duration:"28",
  resolution:"720p", generateAudio:true, videoCount:"1",
  explicitReferences:本段参考节点ID逗号串,
  referenceOrder:本段参考节点ID逗号串,
  promptReferenceSequence:按每次引用出现顺序的节点ID逗号串,
  includeTargetReference:""
}}
body = {workspacePath,workspaceKind,sessionUpdatedAt:updatedAt,
  cache:{version:9,entries:{[key]:entry},active:null,openSessions:[]}}
```

这是结构示例，28秒和16:9须换成本段实际结果／用户比例，workspaceKind换成实际notebook或用户指定的project。多段使用各自独立的 `key:entry`，可将整批段落一次写入 `entries`；后台按key与时间戳合并，不要让多段共用同一个key。修复既有草稿时先 `GET /api/whiteboard/generation-drafts?workspacePath=URL编码路径&workspaceKind=实际类型`，响应含 `cache` 与 `sessionUpdatedAt`，仅更新对应段落并保留其他草稿及当前 `active`。白板操作栏引用的是实际参考**节点ID**；后台 `referenceMedia` 则是附件信息，不能混淆。需要展示时才将 `active` 设为指定段落 `{...scope,key,updatedAt,collapsed:false}`。

保存后打开每张卡片操作栏，回读实际显示的时长、比例与参考缩略图，并检查连线落在侧面圆口。时长与比例是受当前模型能力约束的选项；不存在的选项在草稿恢复时会被跳过，不能仅凭JSON写入就声称已对齐。如果模型不支持所需秒数／比例，先选择支持的配置或调整分段方案，再更新生成核对信息；不能静默回退30秒或其他默认值。操作栏、实际请求和交给用户核对的参数必须一致，准备期间不提交生成。

## Codex图片登记与再生成参考

每张Codex图验收落盘后同步项目清单／图库，导入神思时还要写入其真实图库资产记录，不能只写白板图片节点。优先读取当前版本的原生资产契约；已核实的版本采用 `state.workspaceAssets` 和白板 `canvas.assets`，记录 `id,kind,origin,source,sourceNodeId,nodeName,prompt,referenceOrder,attachment,createdAt,aspectRatio`。外部Codex生成图可登记真实生成来源，不伪造神思任务或jobId；`attachment`来自实际上传回执。保留其他已有资产。

资产图关联原始实际生成提示词、生成通道及上游参考映射，图像操作栏草稿使用同一generation-drafts接口，scope的channel为image。外部Codex图的原提示词存于真实图库记录及操作栏草稿；不为导入伪造node.generation或jobId，避免原生生成节点命名逻辑覆盖资产名称。必要的真实平台引用标记可加入再生成草稿，但原始提示词文本另行保留，不能冒充它。上游原帧／角色参考同样共享且去重，放在资产图更左侧，从右口连到资产图左口；视频生成卡放在最右侧。已生成图本身作为视频参考，不把原帧上游自动塞入所有视频段落。

修复时先读取fresh工作区与草稿，只修改本任务对应字段，保留既有节点的生成结果、运行任务、附件及用户改动；同一视频节点保持稳定ID。不要为入库调用媒体生成接口，也不要删除或取消用户正在运行的任务。

通过API完成整批修改后再刷新UI并等待启动恢复结束，之后逐卡验收。验收期间不并行写同一工作区，否则旧页面保存版本可能失效，造成自动保存冲突或恢复此前打开的操作栏；发生冲突先重载最新状态，不用旧UI强制覆盖服务器数据。

## 准备与提交

保存实际提示词到 UTF-8 文件，参数单独写 JSON。只准备请求不会生成：

```powershell
python scripts/shensi_bridge.py prepare --channel image --profile "准确图片配置ID" --prompt-file output/shensi/prompt.txt --target-file output/shensi/task-target.json --params-file output/shensi/params.json --operation-id "本项生成稳定编号" --out output/shensi/request.json
python scripts/shensi_bridge.py submit --request-file output/shensi/request.json --receipt output/shensi/submission-receipt.json
```

视频改为 `--channel video` 及核对后的配置。省略 `--profile` 使用当前选中的配置，不能因此跳过视频信息核对。桥接从完整的非秘密配置构造 `request.settings`，包含 `id/adapter/provider/protocol/model`；不能只传配置ID。神思从本机运行时绑定恢复端点、CLI和授权。

图片参数例：

```json
{"aspectRatio":"16:9","quality":"2k","imageCount":1,"referenceMedia":[]}
```

这是即梦示例。GPT Image／LibTV 的质量与像素分辨率分开设置，例如 `quality:"high", resolution:"2k"`；默认值只作起点，实际支持选项以神思当前配置与模型能力为准，不能把一套参数硬套全部模型。

视频参数例：

```json
{"aspectRatio":"16:9","duration":8,"resolution":"720p","generationMode":"smart_params","generateAudio":true,"referenceMedia":[]}
```

8秒只是示例，实际秒数按本段提示词预计时长四舍五入；画幅默认16:9，明确要求优先。对白片段需要生成语音时不能关闭音频；“无BGM、无字幕”写入分镜提示词。模型限制与取整规则不相容时调整可支持的模型／分段方案并重新核对，不压缩正常说话速度。需要确认选项时使用神思已有能力探测接口 `POST /api/media/capabilities/probe`，主体 `{channel,settings}`，不将能力探测成功说成成片验收。

`submit` 使用持久媒体接口 `/api/generation/jobs/media`；旧 `/api/images/generate`、`/api/videos/generate` 已弃用。提交前排查已有资产与任务。回执文件以独占方式创建，已有回执会阻止重复提交；网络失败记为 `outcome_unknown`，先查询队列找回任务。保留同一 `submissionId`，不能换编号、删除回执或强制新生成来绕过未知结果。

视频提交必须增加 `--video-confirmation-file output/shensi/video-confirmation.json`。**只有在收到用户对本次具体生成信息的明确确认后**才写确认记录；不能由助手自行确认、使用旧接通授权或把沉默当确认。记录形式：

```json
{"confirmed":true,"confirmedBy":"user","confirmationMessage":"本次用户确认原话","submissionId":"与request.json一致","requestSha256":"本次完整请求的SHA256"}
```

使用脚本的 `request_digest(payload)` 计算完整请求摘要，绑定实际提示词、配置、参数和参考映射。参数或参考图修改后摘要改变，旧确认不再有效。确认缺失或不匹配时，桥接在创建提交回执和调用生成接口之前拒绝视频提交。单个确认可以覆盖已逐项展示的整批视频请求，但每项都应保存对应请求摘要与同一批确认原话。

即梦不同配置严格串行；前一任务完成下载、尺寸和SHA256核对后才处理下一配置。调用神思已有队列，不自行覆盖即梦登录态，不将任何账号设为例外。

## 查询与交付

```powershell
python scripts/shensi_bridge.py status generation-实际任务ID
python scripts/shensi_bridge.py jobs --workspace "绑定记录里的神思工作区路径"
python scripts/shensi_bridge.py export generation-实际任务ID --out-dir output/imagegen/本次资产
```

视频保存到适当的视频输出目录。查询不重新提交。`complete` 才能交付；`failed/retry_required/cancelled/abandoned` 为需处理的终止状态，不能当成功。

后台结果中的 `attachment`／`attachments` 给出 `relativePath`、`sha256` 和尺寸／媒体验收信息。桥接核对工作区边界、来源SHA256、复制后SHA256，不覆盖同名不同内容，不接受目标链接。人物版式、场景结构仍须视觉检查；视频须检查真实时长、镜头顺序、对白、音频与字幕要求。更新本项目资产清单与实际提示词，记录神思配置、任务和输出路径。

## 迁移后的手动成片与持久化检查

白板copy是复制时刻的快照。复制后在原作品提交或完成的任务，成片仍保存到任务的真实原工作区，不会自动出现在新笔记里。迁移、继续任务及刷新验收前，同时核查源和目标的真实任务、附件与图库；保留迁移绑定中的源路径和源documentId，不能只查当前笔记队列。先确认文件是否仍在原工作区，再判断画布显示或保存问题，不能把漏同步的成片说成文件丢失。

`sync_completed_video_results.py` 默认只读发现原scope已完成的视频并校验实际文件SHA256；`--apply`只补复制已完成且尚未显示的成片，复用同SHA的既有结果节点，登记目标白板及本地图库，并持久回读。它不提交、取消、迁移或修改生成任务，不修改17段等原有导航卡或其操作栏。运行中的任务继续留在原scope，在回执记录待同步任务，完成后再补同步。终止失败不能当成待完成或成功。用户已授权迁移、恢复成片时可在该范围内应用；普通发现不自动扩大为新媒体生成。

```powershell
# 使用记录源和目标的本片迁移绑定；先只读，确认发现结果后追加--apply补同步
python scripts/sync_completed_video_results.py --whiteboard-binding output/本片/shensi/notebook-whiteboard-binding.json --out-dir output/本片/shensi/result-sync-check

# 或提供两份工作区绑定及真实白板ID，不改写这两份输入绑定
python scripts/sync_completed_video_results.py --source-binding output/shensi/workspace-binding.json --target-binding output/shensi/notebook-workspace-binding.json --source-document-id "源白板实际ID" --target-document-id "笔记白板实际ID" --out-dir output/本片/shensi/result-sync-check
```

任务ID和`realTarget`保留原scope作为来源记录；补复制不伪装成笔记新任务。独立结果节点持有实际视频文件，图库保留原生成提示词与真实任务来源；`canvas.assets`及`workspaceAssets`中的`sourceNodeId`都指向独立结果节点。导航卡保持文本元数据，不将历史视频重新指回导航卡，否则原生历史恢复会再次把文本卡填成旧视频。对同一导航卡的多次手动生成，按真实文件SHA去重，保留不同版本的独立结果，不覆盖或遗漏早期成片。

补同步还通过`restore_video_result_drafts.py`为独立结果节点建立原生成操作栏：使用该成片对应的不可变真实`job.request`，恢复原提示词、真实配置ID、模型、时长、比例、分辨率、音频和完整引用顺序。原参考附件按实际SHA映射到目标已有共享节点，不能只按名称匹配、复用当前导航卡的新提示词，或省略失配的参考。原配置或同SHA参考缺失时保留原任务信息并报告具体缺项，不写缺引用的草稿。默认只创建缺失的结果草稿，保留用户已有二次编辑；原草稿已经吻合时不重复写。需明确恢复既有结果操作栏的错误默认值时，使用`--apply --drafts-only --repair-existing-result-drafts`，只更新结果key，精确保留所有其他草稿及`active/openSessions`。此动作不会提交生成，原任务时长与本次新分镜时长分别保留，不把旧成片二次生成误导为默认4秒。

用户反馈刷新后成片不见时，先保存新鲜白板、图库、草稿及非秘密任务摘要，查清生成的真实scope、完成时间、附件路径及SHA，再做最小补同步。不得用整板重建修复显示问题，也不改用户手动结果、运行任务、当前提示词或其他文档。整批API修改完成后停止后台写入，再刷新UI一次验收；UI存在旧stateStamp的自动保存冲突时先保留恢复检查点和备份，不能用旧页面强制覆盖新状态。后端fresh复载、实际文件SHA与UI刷新后的结果可见／自动保存都通过，才报告持久化恢复完成。

仅接通或读取配置时，不顺带提交测试图片／视频，不重新生成已有资产。视频必须明确要求并核对本次生成信息后提交，图片遵从Codex内置默认及特殊通道条件，道具图仍需先核对。

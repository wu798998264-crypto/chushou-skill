# 视频画布适配

## 平台路由与真实能力

用户指定的平台、项目、白板和浏览器优先，不能因为已有神思桥接就把其他平台任务改到神思。先使用可用的官方连接器／API／CLI；没有这些能力时才使用宿主支持的浏览器工具。用户禁止电脑控制时不调用桌面输入工具，右侧网页可通过浏览器工具或已核实的本地接口操作。

将四个阶段分开记录：`prepared`（文本与素材齐备）、`bound`（目标原生卡片引用已绑定）、`submitted`（平台已接收真实任务）、`verified`（成片与持久化已检查）。不能用导出成功代替绑定成功，不能用绑定成功代替视频已生成。

连接后读取当前模型的时长、比例、分辨率、图片／音视频上限、音频输入用途、草稿能力和引用格式。平台不支持所需音视频输入时说明缺项，准备兼容方案；不悄悄丢音频、改时长或重配音。参数与引用格式以真实平台为准，不把神思标记当成所有厂商通用协议。

## 所有平台共用的不变量

- 一段对应一个原生生成卡片。完整提示词在该卡片操作栏中，保留所有五项格式、质感、光照及实际台词／歌词，不写“同上”。
- 上游共享素材复用；按实际附件顺序建立图片、视频、音频各自的编号及每次引用序列。上传异步完成可能重排素材，最后按回读顺序重编引用。
- 真正上传文件并核对目标卡片里的就绪状态、时长与文件身份；路径字符串、外部缩略图或上游节点可预览不能证明下游引用已经可用。
- 原片／演唱音频按该段裁切，记录原曲区间、局部起点、采样率、时长与SHA256。不靠文字保证口型，不改变速度、音高或音轨顺序。
- 要求新版本时保留原版，新卡片位于原版右侧并沿用同一段纵坐标；多段按行对齐版本列。生成结果、运行任务和用户的其他内容保留。
- 创建草稿与消耗积分的提交分离。已有授权只覆盖明确范围；提交未知时按真实任务ID找回，不换ID盲目重提。
- 完成后回读正文、参数、引用、位置与版本关系；能刷新时再检查持久化。没有连接能力就交付素材包并说明未迁入。

## 神思

读取 [shensi-generation.md](shensi-generation.md)，使用随包 `scripts/shensi_bridge.py`。端口、工作区类型、真实配置和草稿 key 动态发现，不固定作者电脑路径。已存在的工作区只做最小增量修改，保留 `expectedStateStamp` 冲突检查。仅当目标是神思时应用其 `@「图片N」` 等本地标记和笔记白板契约。

## 即梦画布

1. 读取用户已打开的官方画布，确认当前模型和参考模式；不另开陌生站点代替，不推测私有API。
2. 在目标位置新建视频卡片，填写完整提示词和真实可选参数。上传本段所需图片、对应裁切音频；从画布引用时核对上游文件的真实身份。
3. 等待该卡片所有素材上传完成，查看失败标记；仅恢复失败引用，不删除源素材或旧版卡片。以最终素材顺序建立引用映射。
4. 使用页面真实提供的 `@` 引用选择器插入素材。预览中的 `@图片1`／`@音频1` 只是语义标签，必须经目标控件绑定后才算完成；不声称纯文本已变为原生引用。
5. 对每张卡片回读正文、时长、音频长度和素材状态。遇系统文件框受限时优先使用浏览器支持的文件上传接口；不要不断重开无法操作的弹窗。

## LibTV

使用官方 `libtv` CLI。若宿主已经安装 `libtv-cli` Skill，读取其对应命令文档；否则先读取 `libtv --help` 和目标子命令帮助，以当前版本为准。不要拼私有HTTP请求绕过CLI。

常见已知操作形态（运行前核实当前帮助）：

```text
libtv node list -p <实际画布UUID>
libtv model search --type video
libtv model "实际模型展示名"
libtv upload "本段音频" -p <实际画布UUID> -t audio -f <实际音频文件>
libtv node --x <旧卡右侧X> --y <旧卡Y> create "段落-v2" -p <实际画布UUID> -t video
libtv node "段落-v2" -p <实际画布UUID> --left <真实上游nodeKey> --prompt <完整提示词>
```

模型参数使用实时 schema 的字段及枚举。`--prompt` 中的真实引用为 `{{Node <nodeKey>}}`，节点必须存在并已连到目标入边；同名节点使用 `nodeKey` 消歧。`-s model=...` 使用CLI要求的实际模型名字，不把其他平台模型ID照搬。

准备草稿时不加 `--run`。明确核对并获准提交时才按CLI运行；`--run` 等待本次任务终态，不再套另一套重复提交／轮询逻辑。参数通过进程参数数组或平台支持的结构化输入传递，不把用户提示词拼成 shell 命令。

## 其他画布

沿用上述同一素材计划，读取实际官方能力后映射 `create_node`、`upload_asset`、`bind_reference`、`set_draft`、`readback`。这几个词是操作契约，不是已经存在的厂商 API。缺少位置控制时说明布局限制；缺少音频驱动时交付音频与口型修正方案；缺少接口时导出素材包，不伪造可导入格式。

## 离线素材包格式

准备标准JSON，路径相对于计划文件；`{{asset:ID}}` 只属于触手的离线中间格式：

```json
{
  "schema": 1,
  "assets": [
    {"id": "hero", "kind": "image", "path": "refs/hero.png"},
    {"id": "voice", "kind": "audio", "path": "refs/line.wav", "duration_seconds": 8}
  ],
  "segments": [
    {
      "id": "S01", "title": "演唱", "duration_seconds": 8,
      "aspect_ratio": "16:9", "resolution": "720p",
      "refs": ["hero", "voice"],
      "prompt": "[主要事件]：{{asset:hero}}按{{asset:voice}}的原声演唱。其余五项内容按本段补全。"
    }
  ]
}
```

```text
python scripts/canvas_bundle.py --plan plan.json --out-dir exports --platform generic
python scripts/canvas_bundle.py --plan plan.json --out-dir exports --platform shensi
python scripts/canvas_bundle.py --plan plan.json --out-dir exports --platform jimeng
python scripts/canvas_bundle.py --plan plan.json --out-dir exports --platform libtv --bindings bindings.json
```

每次产生独立子目录，不覆盖旧版本。素材只复制每段实际需要的文件，记录哈希、相对路径、类别、编号和每次引用顺序。WAV时长会从文件头核验；压缩音视频应先用媒体工具测出时长填入计划，打包器不伪称已解码测量。

LibTV绑定文件形如 `{"hero":"实际nodeKey","voice":"实际nodeKey"}`。这些值必须来自CLI读取结果，脚本只做格式编译，不证明节点存在或已连边；输出依旧标记为 `prepared`，绑定和生成状态由平台回读后记录。即梦输出保留语义标签，等待页面原生引用控件绑定。此包不自动提交任何生成。

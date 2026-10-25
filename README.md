# 触手 · Chushou

处理红果短剧下载、逐集转写校对文稿，并把参考视频、原创剧本和歌曲整理为可编辑的分镜提示词，迁入用户指定的 AI 视频画布。安装后调用 **`$chushou`**，中文名称为 **触手**。原名「盗贼的极意」。

## 安装

下载 [最新发布包](https://github.com/wu798998264-crypto/chushou-skill/releases/latest)，解压后把 **`chushou` 文件夹**放进你的 Skills 目录：

- Codex：`~/.codex/skills/chushou/`；Windows 通常为 `%USERPROFILE%\.codex\skills\chushou\`。
- 其他支持 `SKILL.md` 的 Agent：放进该宿主的 Skills 目录，按宿主方式重新加载。

也可以克隆本仓库，仅复制 `chushou/`。不需要复制发布包、Git 元数据或其他用户的项目。

## 使用示例

```text
用触手分析这个参考视频，先出脚本，不生成视频。
用 $chushou 把我的原创脚本整理为最长30秒一段的完整提示词。
把这些提示词和对应参考图、音频迁到即梦画布，先准备卡片。
用触手在 LibTV 创建分段草稿；每段独立写完整质感、光照和参考绑定。
保留原版，新建动作和运镜加强版，放到原版右侧。
给演唱片段加入实际歌词，并裁出对应参考音频。
下载红果《剧名》第1至3集，准备短剧分析素材。
用触手下载整部《剧名》，通过网站逐集转写、校对，按集汇总成TXT和Word。
只校对已有第2、10集转写稿，保留原文与待核项，不重新下载或识别。
```

## 短剧文稿流程

默认使用“水果刀下载 → Transcriber网站逐集识别 → 校对与标点 → 按集文稿”。已有视频或原稿从相应阶段继续，支持只处理选集；不顺带生成分镜、图片或视频。

- 网站运行在使用者自己的浏览器中，需要宿主能操作网页。首次下载模型、内存与广告访问提示可能影响运行；这不是随包无人值守网站驱动。
- 原文不覆盖，校订记录具体位置、前后文字及核对依据；不确定内容保留原文并列待核。标点和排版逐字门禁保留数值、非BMP汉字及语义符号，不等同于ASR零漏句保证。
- 输出一份按真实集次排列、每句一行的校对TXT及对应Word，另存原文和审计报告；缺集或待校对不能冒充完成。
- 神思后台队列仍是可选路线，与网站及本地faster-whisper分别记录，不静默切换。

使用见[短剧流程](chushou/references/drama-transcription.md)、[网站适配](chushou/references/website-transcription.md)与[校对编排](chushou/references/transcript-proofreading.md)。离线工具仅需Python标准库；Word输出额外需要当前解释器中的`python-docx`：

```text
python chushou/scripts/build_drama_documents.py --manifest 项目/transcript-workflow.json --out-dir 项目/文稿 --docx --require-complete
```

本版新增校对编排工具的39项离线测试及原有汇总工具的10项测试均通过，包括换目录运行与实际Word输出检查；网站与画布的在线能力以当前页面及连接为准。

## 画布兼容方式

| 目标 | 接入方式 | 边界 |
| --- | --- | --- |
| 神思 | 随包本地桥接脚本，笔记白板、附件、原生生成草稿 | 需运行兼容的神思版本；端口、配置动态读取 |
| 即梦画布 | 用户已连接的官方页面／浏览器工具，按实际顺序上传并绑定引用 | 不提供虚构的即梦私有 API；必须验证卡片内素材上传成功 |
| LibTV | 官方 `libtv` CLI，查询真实模型 schema、上传资源、连接节点、写入草稿 | 需要用户自己的登录态；命令以当前 CLI 帮助为准 |
| 其他 AI 视频画布 | 官方连接器／API／CLI 优先，其次受支持的浏览器操作 | 先读取模型与附件能力；无可用连接时交付可迁移素材包 |

**兼容指统一制作流程、可迁移素材包和平台适配规则，不代表已在所有网站完成端到端测试。** 各平台登录、会员、积分、API 授权和视频生成额度由使用者自行提供。Skill 本身不绕过平台能力或权限限制。

## 可迁移素材包

`scripts/canvas_bundle.py` 使用 Python 标准库，把一个计划转成按段编号的提示词、实际媒体文件、校验和与引用映射。它不会联网、上传或提交生成。

```text
python chushou/scripts/canvas_bundle.py --plan plan.json --out-dir ./canvas-export --platform generic
```

输入格式与即梦、LibTV、神思的接入方法见 [画布适配说明](chushou/references/canvas-adapters.md)。`libtv` 模式只有收到真实节点绑定时才编译 `{{Node ...}}`；没有绑定就保留待绑定状态，不伪造节点。

## 保留的工作流

- 参考视频下载、真实解码与逐镜动作检查；原创脚本不要求先有参考视频。
- 同一视频的脚本／提示词放在一个可展开、可编辑的操作台；当前显示稿作为后续文本输入，历史正文不混入上下文。
- 保存按钮位于右下角，名称「保存版本」；切换显示版本即切换当前版本。此交互依赖宿主的 `window.openai` 状态桥，其他宿主须使用自己的编辑能力，不能冒称已同步。
- 每个生成卡片独立保留完整质感、光照、台词、歌词和参考用途；实际提交的段长服从模型上限，默认单段不超过30秒。
- 演唱先截对应音频，逐句写明歌词；动作呼应语义并递进，运镜依据表演选择。仅写歌词不保证口型，成片仍须检查。
- 保留旧卡片与运行任务；要求新版本时在旧版右侧创建新卡片。
- 指定小说／章节的正文提取作为独立可选入口保留。

## 依赖与验证

- 脚本：Python 3.11+；小说提取使用 Node.js 18+。
- 红果短剧：首次搜索、解析或下载会自动从公开PyPI安装独立依赖，包含FFmpeg和Pillow。只需宿主提供Python 3.11+、pip及联网能力；无需水果刀、旧技能目录、作者设备或fruitknife私有仓库访问权。
- 可提前运行 `python chushou/scripts/fruitknife_runtime.py --install`；`--check`只检查、不安装。运行时保存在当前技能自己的`runtime/fruitknife/`，按Python、系统、CPU架构隔离，不修改系统Python。使用及选集规则见[红果短剧说明](chushou/references/fruitknife-download.md)。
- 普通播放页下载还需官方 `yt-dlp`；视频工具可继续使用自己的`--ffmpeg`路径，`ffprobe`可选。
- 包内不包含 Python、机器相关运行时、账号、密钥、Cookie、成片或私人项目数据。解压到Skills目录后由当前设备首次初始化；不要复制其他设备的runtime。
- 本地检查：`python -m unittest discover -s chushou/scripts -p "test_*.py"`。

下载兼容性检查：`python chushou/scripts/test_fruitknife_download.py`，只访问本机合成视频服务。随仓库提供Windows、macOS、Linux的CI任务，初始化并运行这组检查；CI配置存在不代表已经在所有平台实测通过。

主要技能代码为MIT License。`scripts/fruitknife_vendor/`保留来源仓库的原始权利说明与校验清单，不自动改授为MIT；详见其中的`UPSTREAM.json`。

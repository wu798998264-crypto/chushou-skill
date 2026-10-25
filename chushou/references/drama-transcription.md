# 短剧下载、网站转写、校对与按集交付

处理红果短剧下载、短剧视频转文字、逐集全文稿、错别字校对和按集整理文档时使用。仅说“完善流程”时只维护技能和工具，不处理具体作品。按本次范围执行；已有源片或转写稿直接从相应阶段续接，不重复下载或识别。剧名存在歧义才核对，不能把上次素材或搜索第一项当作本次指定剧。

默认按“水果刀下载 → 网站逐集识别 → 校对与标点 → 一句一行编排”执行，交付一份按真实集次排序的校对版UTF-8 TXT及对应DOCX，另存原文与校对报告。用户只要原文／某种格式时按要求，不用摘要代替全文。任务不触发分镜、图片或视频生成。

## 默认网站与校对路线

1. 按下文第1节核对作品、真实总集数与本次选集并下载。仅选几集时只处理这些集次，保留原集号，不重新从第1集编号；选集不能称全集。纯文稿任务不加无关抽帧。
2. 按 [website-transcription.md](website-transcription.md) 操作真实Transcriber页面：选择中文和实际可用模型，载入完整原片，每集完成立即保存原始TXT及能取得的JSON／字幕。网站浏览器内推理、本地faster-whisper与神思Fun-ASR后台是不同实现，不混称等价或静默替换。
3. 为每集保留真实集次、video_id、源片路径／SHA、实际送入网站的媒体、真实模型、导出文件及SHA。派生音轨／分段另记源SHA、区间及完整覆盖；不截尾、变速或省略失败分块。已完成集按源SHA及实际导出复用，网页刷新前已有本地备份。
4. 按 [transcript-proofreading.md](transcript-proofreading.md) 建专名词表，结合原音、原片字幕与上下文校对。每项修改记录原始位置、原文、新文、原因与依据。不确定项保留原文并列待核，不全局替换单字、不猜补台词、不因像水印就删句。原片字幕是证据，视频生成“无字幕”约定不限制本文稿提取。
5. 创建该参考规定的 `transcript-workflow.json`，运行下列离线编排工具。校订稿由原文加有据编辑重建，标点及排版阶段逐字核验；保留原文、校订稿、标点稿、报告。缺集／失败／待校对均明确标注，已有不同结果另存版本。

```powershell
python scripts/build_drama_documents.py --manifest "项目/transcript-workflow.json" --out-dir "项目/文稿" --docx --require-complete
```

DOCX依赖当前Python中的 `python-docx`；按宿主隔离环境准备，不能把TXT改扩展名当Word。去掉 `--docx` 可仅生成TXT；未齐时 `--require-complete` 只留报告，不发布冒充最终版的正文。逐字门禁只证明编排没有擅改校订文字，不能证明ASR零漏句。分别报告下载完成数、识别完成数、校对数、缺失集次与待核项；只有实际听过才称“已核听”。不沿用来稿中另一台设备“92集40分钟”的成绩。

第2—4节保留为**可选神思后台及仅原文汇总路线**。用户指定后台或同意换路线时才使用；网站受阻不能静默改为云端上传。后台导出如需校对，将真实JSON中的 `text` 不改写地存为TXT，保留原job JSON及SHA，再接上述第4、5步。若用户明确选择本地faster-whisper，另记录实际引擎、模型及设备；本网站流程不隐式安装本地Whisper或要求CUDA。

## 1. 核对完整目录并下载

读取 [fruitknife-download.md](fruitknife-download.md)，复用随包水果刀入口：

```powershell
python scripts/download_drama.py inspect --source-file output/drama/source.txt --out output/drama/catalog.json
python scripts/download_drama.py download --source-file output/drama/source.txt --all-episodes --quality 1080p --out-dir output/drama/downloads --out output/drama/download-batch.json
```

剧名先搜索，再锁定已核对的series_id。用户已明确“整部”即按原目录下载全部集次，不重复询问全集范围；指定选集将 `--all-episodes` 换为 `--episodes "1,3-5"`。仅音频转写不加`--inspect`，避免不必要的逐帧、联系表与视觉模型消耗。复用随包便携入口，不复制依赖作者水果刀安装目录、只凭MP4非空跳过下载的脚本。

集数以来源目录及batch的`episode`整数字段为准，保留series_id、video_id、实际文件路径、SHA256、下载manifest和状态；不能按文件名字典序或下载完成先后排集。完整下载验收是`selection_complete=true`、`completed_episodes==source_total_episodes`且`failed_episodes=0`，并有真实已校验文件。失败集明确保留，重跑原命令复用有效已下载集，不从头重复下载。

## 2. 可选后台：建立逐集来源与识别任务映射

读取 [audio-transcription.md](audio-transcription.md)，在神思的专用转写笔记本／白板中复用或登记这批已下载视频源，只建必要的来源索引，不建立视频生成卡片。按实际文件SHA复用已有资产，使用原生附件导入与托管路径；超过JSON导入上限的原片用神思真实multipart导入，不把外部绝对路径伪装为附件relativePath。接口与安装位置按 [shensi-generation.md](shensi-generation.md) 的动态发现规则取得。

默认将原视频节点提交给后台，由后台提取完整音轨，因此job.sourceSha256对应原视频。若明确使用预提取音轨，必须分别记录视频SHA与音轨SHA，不能把两者混写。

每集保存以下明确映射，不靠AI从导出文件名猜测：

```json
{
  "series_id": "实际系列ID",
  "episodes": [
    {
      "episode": 1,
      "video_id": "来源目录的视频ID",
      "source_sha256": "download-batch中原视频的SHA256",
      "node_id": "神思原视频节点ID",
      "job_id": "实际转写任务ID",
      "transcript_file": "transcripts/episode-001.json"
    }
  ]
}
```

`transcript_file`指向神思导出的`{"job":{...},"text":"原始识别全文"}`JSON，相对路径按映射JSON所在目录解析。集次、video_id、SHA、node_id和job_id必须真实对应；校验冲突要停止合并，不能将别剧或别集文字塞入当前集。相同源内容被合法去重时可复用同一任务，但仍保留各自集次标题。

## 3. 可选后台：程序批量转写和续跑

用户要求使用本识别服务处理这批作品即按其授权范围提交，不逐集再询问；必要的明确云端上传授权由该流程及CLI确认标志承接。单批最多50项，全集超过50集时按集数分批，保留每批稳定request-id与收据。最后仍合并为一份全集文档，不能因接口分批而交多份互不关联的文档。

```powershell
python scripts/shensi_transcription.py --binding-file output/drama/shensi/workspace-binding.json --document-id "真实白板ID" start --node-id "第1集原视频节点" --node-id "第2集原视频节点" --request-id "series-实际ID-batch-001" --confirm-cloud-upload --out output/drama/asr/batch-001.json --wait --timeout-seconds 86400 --export-dir output/drama/asr/transcripts
```

由神思后台及一个CLI进程完成串行识别、等待与导出；不让Codex逐集转写、逐项打开网页或不断发起模型轮询。默认保留原始文字，不自动润色、补台词、统一同音人名、虚构说话人或时间码。只在用户另要校订时单独生成校订版，原始结果继续保留。

普通等待默认1小时；整剧可显式设置最多24小时，让程序持续等待，不靠模型反复启动新轮次。到期保存状态并按原收据继续，不能把等待到期误认成远端识别失败或重新上传全剧。

下载和识别分别续跑：下载复用大小与SHA均通过的集；识别复用源SHA和模型相同的已完成任务，只重试明确失败／中断项。限流暂停，不自动轮换账号、切付费模型或无上限重提。CLI等待超时后后台任务可能仍运行，先查原任务，不能换新ID重复提交。

新版神思未安装或没有转写接口时报告需要更新，不能声称旧安装版已经具备新功能。公开体验服务可能排队或失效，不承诺免费无限制、小时级已验证或零漏词。

## 4. 可选后台：仅原文自动汇总

用随包合并工具，直接读取完整下载目录与明确任务映射：

```powershell
python scripts/compile_drama_transcripts.py --download-manifest output/drama/download-batch.json --transcript-map output/drama/asr/episode-map.json --out output/drama/全集音频识别原文.txt
```

交付样式：

```text
《剧名》全集音频识别原文
总集数：实际集数
识别状态：完成／未完成草稿

第1集
该集完整原始识别文字……

第2集
该集完整原始识别文字……
```

按整数集次排序，不能出现第1集、第10集、第2集的字典排序。源目录中的每集必须有标题；缺失下载、缺转写或识别失败写明对应集次及状态占位，不默默省略。默认允许交付有明显未完成标记的阶段草稿并返回非0状态；`--require-complete`用于只在全集齐全时写最终TXT，未齐时保留报告和既有成稿。

合并前验证身份，保留原始正文；已有不同文档另存版本，不能覆盖用户校订内容。保存逐集核对报告与真实映射作为内部依据，用户主要拿到按集次排列的一份文档。只有全部源目录集次均下载和转写完成，才宣布“全集完成”。音频识别缺少原生时间戳时不伪造精确字幕。

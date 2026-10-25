# 短剧全文稿校对与离线编排

配合 [drama-transcription.md](drama-transcription.md) 使用。吸收用户提供的drama-transcribe流程中的原文／校订／标点分层、专名词表、逐字门禁及一句一行排版；下载复用触手便携入口，识别默认真实网站，不照搬作者机器路径、GPU假定或工具名。

## 校对规则

- 原始TXT及网站JSON／字幕导出不可原地修改。先通读代表集建立本剧词表，再逐集核对疑点，重点看人名、地名、招式、重叠人声、急促喊话、方言、数字与近音词。词表中的标准写法必须有原音／原片字幕／用户设定等依据；两三集抽查不能冒称全剧逐句校对。
- 同音字、专名和确认为识别幻觉的文字逐项修改。记录对应原片或字幕的实际位置及核对结果。只凭“剧情应该如此”不改；不能用“兵→冰”一类全局规则批量改完整部剧，也不将语气词、重复句或看似水印的字串默认删除。
- 对无把握项保留原文，写入待核清单，不每发现一个字就打断用户。只有缺失信息会实质改变结果且无法从现有素材核实时才局部询问。声称已核听、逐帧或逐句核验必须实际做过；原片没有可用字幕时不能捏造字幕依据。
- 原文到校订：用绑定原文SHA的、有具体原始偏移的编辑清单重建。校订到标点／排版：不得再修改正文字符。汉字（含非BMP）、字母、数字、语义符号等都要保留；小数、负数、百分比等不能在“去标点”时变值。校验只能验证这些阶段的文字一致性，不能证明ASR没有漏句。

## 文件与来源清单

项目建议保留 `raw/`、`review/`、`punct/` 和最终 `文稿/`，名称可随项目调整。每个文件必须在本次清单登记；工具不扫描目录自动拼入旧稿。所有相对路径按清单JSON所在目录解析，不按当前终端目录解析。

创建 `transcript-workflow.json`，下例的路径、ID和SHA必须换为本次真实值：

```json
{
  "schema_version": 1,
  "title": "本次剧名",
  "series_id": "已核对的红果系列ID",
  "total_episodes": 1,
  "download_manifest": "准备/download-batch.json",
  "episodes": [
    {
      "episode": 1,
      "video_id": "源目录真实video_id",
      "source_file": "downloads/实际原视频.mp4",
      "source_sha256": "原视频的64位SHA256",
      "backend": {
        "name": "Transcriber browser",
        "url": "https://transcriber.tools.ryusei.io/en",
        "model": "实际选择的模型名称"
      },
      "raw_file": "raw/episode-001.txt",
      "raw_sha256": "原始TXT的64位SHA256",
      "review_file": "review/episode-001.json",
      "punct_file": "punct/episode-001.txt"
    }
  ]
}
```

- `total_episodes`为真实源目录总集数，不能填已下载数。省略 `selected_episodes` 表示全部；只处理第2、10集时设 `"selected_episodes": [2, 10]`，保留这两集原号。本次范围可为原下载batch的子集，不能扩大到该批未选择的集次；已有全集下载无需为选集校对重下或改造batch。未登记的本次所选集次视为缺失，不忽略。
- 水果刀来源必须保留 `download_manifest`、`series_id` 和 `video_id`，工具核对目录、源ID、所选范围、真实下载状态与SHA。已下载视频的同SHA副本可复用；不能因文件改名把第10集当第2集。
- 纯本地素材可省略 `download_manifest` 和未知的 `series_id`／`video_id`；集次由用户指定范围及明确文件映射确定，不臆造红果来源。已转写文本若拿不到源片，保留实际缺失状态，不能虚构源文件或SHA来通过校验。
- `source_file`始终指原视频。使用派生音频、分块或其他后端时，另保留证据清单中的实际输入媒体／SHA、源区间覆盖、模型、导出文件SHA及映射；工具校验源视频和TXT身份，不自动证明网站实际载入过哪段音轨。神思JSON提取TXT后保留父级job与SHA；本地后端URL可用明确的 `local://faster-whisper`，不伪造网站job。
- `raw_sha256`按原始TXT实际字节计算；UTF-8 BOM、CRLF都属于原始文件。偏移基于 `read_bytes().decode("utf-8")` 后的Unicode码点，不能用JS UTF-16索引或自动换行归一化后的偏移。

## 每集校对记录

`review/episode-001.json` 示例：原文为 `陈海穿来了` 时，索引2处改一个字：

```json
{
  "raw_sha256": "对应原始TXT的64位SHA256",
  "reviewed": true,
  "edits": [
    {
      "start": 2,
      "before": "穿",
      "after": "川",
      "reason": "人名同音误识别",
      "evidence": "本集实际核对的原音／字幕位置及具体依据"
    }
  ],
  "uncertain": [
    {
      "text": "另一处未辨清的原始短句",
      "reason": "实际存在的噪声或证据冲突，保留原文待核"
    }
  ]
}
```

示例只演示结构，不能把示例依据当作已核实事实。`start`从0开始，全部基于未改原文；编辑区间不得重叠，每项 `before` 必须与对应原文精确匹配。`reason`、`evidence`不可空；证据也可为非空结构对象。校订由所有编辑一次应用重建，避免前项替换改变后项偏移或产生连锁替换。需要多处改同一名字时逐个登记真实位置。

`reviewed: true` 表示本次实际完成了该集校对，不是为通过编排而默认填写。确实校对但无改字时保留空 `edits`。未提供校对文件或未标实际完成，工具仅能生成“待校对”草稿。不确定项随报告保留，不通过猜改清零；有待核项的校对版不代表已全部确认。

## 标点、编排与运行

将经校订的文本单独加标点并保存到 `punct_file`；仅增删句读、引号和空白。若已无需额外标点，可省略此字段，工具直接使用经审计校订文本。一句一行排版不删除正文内容；缺失标点应补到标点稿，不能把另一个改写稿当标点稿通过校验。

从技能根目录运行（`python`替换为宿主实际解释器）：

```powershell
python scripts/build_drama_documents.py --manifest "项目/transcript-workflow.json" --out-dir "项目/文稿" --docx --require-complete
```

仅标准库即可生成TXT和审计；`--docx`需 `python-docx`，在宿主隔离环境安装并用同一解释器运行。去掉 `--require-complete` 可生成明确标记的阶段草稿；缺集／待校对仍返回非0，不误报成功。

产物包括：

- `<剧名>_原始识别全文.txt`：原始机器识别按集汇总。
- `<剧名>_校对版.txt/.docx`，未齐时为“校对草稿”：按真实集次排、每句一行；选集在文档内明确标注。
- `episodes/episode-NNN.raw.txt`、`.corrected.txt`、`.formatted.txt`及`.audit.json`：每集原文、重建校订、排版及逐项审计。
- `report.json`：真实来源和hash、后端、完整性、每集状态、实际输出路径、改动与待核项。

严格模式缺集／失败／待校对时保留报告及每集审计材料，不发布汇总正文。错误来源、SHA冲突或标点稿改字会阻止该集进入成功正文。重复运行时相同内容可复用，不同内容保存 `.v002` 等新版本；以后续命令返回的真实路径交付，不误读旧版 `report.json`。

退出码0：本次所选集次均有真实原文并完成校对（仍可能有待核项）；2：缺集／待校对；1：身份／证据／正文校验或写盘失败。工具只验证流程中的可检验约束，不代替听音、视频观察或人工判断。

定向离线测试：`python scripts/test_build_drama_documents.py`，不调用网站、下载作品或耗费生成额度。

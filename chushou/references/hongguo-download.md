# 红果短剧下载

当前唯一短剧下载实现为用户提供的 hongguo-downloader 1.0.4，替换水果刀路线，不再回退旧实现。无需启动附件里的桌面程序。来源、原文件摘要、改动及 GPL-3.0 许可见 [来源清单](../scripts/hongguo_vendor/UPSTREAM.json) 和 [组件许可](../scripts/hongguo_vendor/LICENSE)。该目录提供实际运行的全部组件源码，组件许可独立于技能其他部分。

先搜索核对同名作品，或从分享链接读取标题及完整集表。指定集次才下载指定集次；明确要求全集才选全集。分享地址仅访问红果及番茄已知站点，不借用浏览器Cookie、账号或系统登录。

本地技能需要 Python 3.11+、Node.js 22+ 及 FFmpeg/ffprobe；媒体依赖用 python -B scripts/chushou_runtime.py --install 安装到技能自己的 runtime/media 目录，不写系统Python。神思内置触手使用随包 Python、Node.js 和媒体工具，新用户无需另装水果刀或该附件。

```text
python -B scripts/download_drama.py search --keyword "用户指定剧名" --out "项目/搜索.json"
python -B scripts/download_drama.py inspect --source-file "项目/分享.txt" --out "项目/目录.json"
python -B scripts/download_drama.py download --source-file "项目/分享.txt" --episodes "1,3-5" --out-dir "项目/downloads"
python -B scripts/download_drama.py download --source "已核对ID" --all-episodes --out-dir "项目/downloads"
```

神思对话对应 drama_search、drama_inspect、drama_download；普通 download_video 自动识别红果来源。没有明确集数时仅返回目录和 selection_required，不能冒称完成下载。无需把命令拼接进神思对话。

下载器优先取得源站最佳可解码 HEVC/H264 流，排除私有 bytevc2；quality 是请求标签，当前适配器实际取得源站最佳流，记录真实宽高、actual_quality 和 quality_fallback，不承诺按标签降采样。每集独立目录、SHA256、来源清单、音轨及完整解码验证，只有完整落盘才算成功。重跑只复用大小和哈希相符的已验收集次，不覆盖旧文件；部分失败保留成功结果。下载、校对、创作和生成仍分别执行，不因下载完成自动提交生成任务。

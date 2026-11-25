# 红果短剧下载

短剧取流已更新为水果刀 3.1.2 的红果模块 1.0.11 路径：保留 guoapp 的搜索和完整集表，App 六条线路只有空响应时才依次接续，再使用网站提供的 fallback-play-info 播放地址，无需启动水果刀。连续两轮 App 全空后，本机保存30分钟冷却状态，期间直接用网站线路；到期再探测，App 成功则恢复。来源、附件摘要、改动及 GPL-3.0 许可见 [来源清单](../scripts/hongguo_vendor/UPSTREAM.json) 和 [组件许可](../scripts/hongguo_vendor/LICENSE)。

网站备用流可能只有360p，按实际解码尺寸记录，不把请求的1080p当成结果。未授权、登录不足或限流时停止并准确报错；不自动读取浏览器或水果刀的登录态，不更换匿名设备，不猜付费地址。当前入口仅使用无需登录即可返回的播放地址，需要账号授权的集次不冒称可下载。

先搜索核对同名作品，或从分享链接读取标题及完整集表。指定集次才下载指定集次；明确要求全集才选全集。分享地址仅访问红果及番茄已知站点，不借用浏览器 Cookie、账号或系统登录。首次按原模块注册匿名设备并保存到本机独立 runtime 缓存，不复制原软件的设备或用户资料；拒绝或限流后明确停止，不更换设备重试。缓存不会随安装包发布。

本地技能需要 Python 3.11+、Node.js 22+ 及 FFmpeg/ffprobe；媒体依赖用 python -B scripts/chushou_runtime.py --install 安装到技能自己的 runtime/media 目录，不写系统Python。神思内置触手使用随包 Python、Node.js 和媒体工具，新用户无需另装水果刀或该附件。

```text
python -B scripts/download_drama.py search --keyword "用户指定剧名" --out "项目/搜索.json"
python -B scripts/download_drama.py inspect --source-file "项目/分享.txt" --out "项目/目录.json"
python -B scripts/download_drama.py download --source-file "项目/分享.txt" --episodes "1,3-5" --out-dir "项目/downloads"
python -B scripts/download_drama.py download --source "已核对ID" --all-episodes --out-dir "项目/downloads"
```

神思对话对应 drama_search、drama_inspect、drama_download；普通 download_video 自动识别红果来源。没有明确集数时仅返回目录和 selection_required，不能冒称完成下载。无需把命令拼接进神思对话。

下载器优先取得源站最佳可解码 HEVC/H264 流，排除私有 bytevc2；quality 是请求标签，当前适配器实际取得源站最佳流，记录真实宽高、actual_quality 和 quality_fallback，不承诺按标签降采样。每集独立目录、SHA256、来源清单、音轨及完整解码验证，只有完整落盘才算成功。重跑只复用大小和哈希相符的已验收集次，不覆盖旧文件；部分失败保留成功结果。下载、校对、创作和生成仍分别执行，不因下载完成自动提交生成任务。

# 红果短剧下载：fruitknife移植入口

用于短剧制作前的源片准备。来源为用户指定的私有仓库 `wu798998264-crypto/fruitknife`，锁定提交 `f25f1a4e7367d3e9eb0aa1ab80d0cfc11742d13c`。已从PyQt界面分离搜索、分享链接解析、剧集目录、清晰度选择和逐集下载；无需启动水果刀或神思。现有抖音等平台继续走普通下载入口。

## 使用范围

- 用户给剧名时先搜索，核对标题及`series_id`。同名有实质歧义时才询问；不能直接将搜索第一条当作用户指定作品。
- 分享文案、`novelquickapp.com`、`hongguoduanju.com`链接和纯数字`series_id`均可解析。目录会列出源站实际提供的全部集次，不沿用原GUI的200集截断。
- 用户指定第几集／集数范围时只下载对应集次；明确说整部／全集时才用`--all-episodes`。只给系列链接且范围不明时先展示目录并核对范围。只搜索、只解析、只下载的要求分别执行，不顺带生成图片或视频。
- 默认请求1080p，支持360p、480p、540p、720p、1080p。源站不提供请求档位时记录实际档位及`quality_fallback`；文件实际宽高仍以媒体探测为准，不能把标称档位当作真实分辨率。

## 入口

把分享内容保存为UTF-8文件，避免把外部文案拼接成shell代码。命令中的Python、路径、剧名、ID及集数均换为本次实际值。

```text
# 搜索及下一页；不下载视频
python scripts/download_drama.py search --keyword "用户指定剧名" --out "项目/准备/搜索结果.json"
python scripts/download_drama.py search --keyword "用户指定剧名" --cursor "上次返回的next_cursor"

# 核对标题、集数和真实video_id目录；不下载视频
python scripts/download_drama.py inspect --source-file "项目/source-link.txt" --out "项目/准备/剧集目录.json"

# 指定集次下载，默认只做源片准备
python scripts/download_drama.py download --source-file "项目/source-link.txt" --episodes "1,3-5" --quality 1080p --out-dir "项目/downloads"

# 用户明确要求整部时使用
python scripts/download_drama.py download --source "已核对的series_id" --all-episodes --quality 1080p --out-dir "项目/downloads"

# 下载后还需分析时，加--inspect，生成帧、联系表及可用音轨
python scripts/download_drama.py download --source-file "项目/source-link.txt" --episodes "1-2" --out-dir "项目/downloads" --inspect
```

普通入口同样识别红果来源：

```text
python scripts/download_video.py --source-file "项目/source-link.txt" --episodes "1" --quality 1080p --out-dir "项目/downloads"
```

普通入口默认衔接抽帧和音轨；只下载时用`--download-only`。红果来源未给集数时返回`selection_required`及目录，不冒称已经下载。原普通平台链接不用这些红果参数。

## 完成、续传与后续制作

若任务是“下载整部剧并按集数汇总音频文字”，继续 [drama-transcription.md](drama-transcription.md) 的批量转写与单文档合并流程，不触发分镜、生图或视频生成。下载命令可加`--out`将当前批次摘要写到指定JSON，便于后续使用同一入口续跑；逐集原始manifest仍保留。

输出位于`downloads/hongguo-系列ID/请求清晰度/`。每集独立尝试目录保留`source.mp4`、来源、集次、真实video_id、SHA256、字节数、实际媒体元数据和验收状态；批次JSON列全部请求集次与逐集结果，`progress.jsonl`保留进度。

重复相同命令即可逐集续传：只复用已完成且文件大小、SHA256均仍匹配的素材。失败／未完成／损坏集次新建尝试目录；不覆盖旧文件。这里的续传是**逐集跳过已验收文件**，不是中断文件的字节级拼接。Ctrl+C暂停后重复原命令继续。

只有完整视频及可用音轨实际解码通过，才写`status:downloaded`。任何集次失败则批次为`partial_failure`且退出码非0，不把部分成功说成全集完成。多集结果在`episodes[]`；单集还兼容普通入口的`video/metadata/analysis/inspection`字段。`manifest`指向批次记录，单集真实记录位于`episodes[].manifest`。

拿到真实文件后按主技能继续观察镜头、核对台词和制作提示词；脚本仅生成技术检查材料，不能代替视觉和音轨判断。不因下载完成自动提交平台视频任务。

## 运行依赖与来源边界

下载技能后，首次调用搜索、解析或下载命令会自动运行`scripts/fruitknife_runtime.py`，从公开PyPI安装依赖和自带FFmpeg的imageio-ffmpeg。宿主需提供Python3.11+、pip及联网能力；不要求先安装水果刀、旧版技能、神思或作者电脑上的运行时，也不读取作者的账号、Cookie、GitHub登录或设备环境变量。

安装位置由技能实际位置、Python版本、操作系统和CPU架构共同确定：`runtime/fruitknife/<解释器-系统-架构>/packages`。原生依赖不会在不同Python版本或平台间混用。包内包含下载、签名和初始化源码，首次使用无需克隆或访问fruitknife私有仓库。下载新版本技能时不复制其他人的runtime目录。

可提前准备，或只检查而不安装：

```text
python scripts/fruitknife_runtime.py --install
python scripts/fruitknife_runtime.py --check
```

初始化在独立子进程验证依赖来源、签名模块导入和包内FFmpeg运行；通过后才保存就绪记录。安装中断不冒称就绪，重复命令可重试。无管理员权限要求，不向系统Python写包；运行时不随源码包分发。Windows、macOS和Linux使用各自平台的FFmpeg wheel；无对应wheel或下载失败时明确报错，可自行提供FFmpeg，但不能把未通过的环境说成可用。

使用`--ffmpeg`、`--ffprobe`仍可指定当前用户自己的工具；已有工具继续兼容。Pillow一并安装，用于联系表，不依赖宿主碰巧已有此包。betterproto固定到该签名模块已实测兼容的2.0.0b7；原仓库的`>=2.0`不等于该预发布版本。来源签名文件及校验清单位于`scripts/fruitknife_vendor/UPSTREAM.json`。原仓库标注Private / All rights reserved，保留上游权利说明，不将这些模块自动改授为技能的MIT许可。

沿用源接口正常返回的播放材料，不调用设备注册、不自动轮换设备、不收集浏览器登录凭据，也不处理需要独立DRM许可证或额外付费授权的结果。401／403及原始接口异常会明确失败，不能解释成搜索零结果。自有有效设备配置可由宿主环境的`DUANJU_DEVICE_ID/DUANJU_INSTALL_ID`提供；不在聊天、日志或源片清单打印它们、播放密钥或临时签名URL。

定向离线检查：`python scripts/test_fruitknife_download.py`，需要可发现的FFmpeg，或设置`CHUSHOU_TEST_FFMPEG`。测试只使用本机HTTP服务和合成素材，不访问实际供应商或提交生成。

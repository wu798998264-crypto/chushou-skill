# 云析解析核心与对应源码

上游：tidain/YunX-Desktop v1.3.4，commit 00a030f778a65840e9be65baecb15497a94b04ec。
上游许可证 AGPL-3.0，见 LICENSE；完整对应上游源码和 Gradle 构建文件见 yunx-corresponding-source.zip。
runtime/runtime/legal 保留所附 Java 运行库的许可声明。

desktop.jar 及依赖来自该版本官方 Windows 包，摘要见 UPSTREAM.json。
bridge/ 为本产品可修改的 JSON-lines 适配器与日志适配源码，classes/ 是对应编译结果。
使用 JDK 17，在本目录运行以下命令可重新编译：

```powershell
javac -encoding UTF-8 -cp "runtime/app/*" -d classes bridge/YunxBridge.java bridge/com/yunx/app/util/Log.java bridge/com/yunx/app/util/DiagnosticLog.java
```

此适配器只将输入交给上游解析类；不启动云析桌面界面。集成源码保存在宿主 apps/desktop/yunx-*.mjs、video-transfer.mjs 与 packages/ui/share-link.js，玄念分发保留同样目录。
生成的 runtime-manifest.json 记录随包每个文件的 SHA-256，构建及安装验收必须逐项一致。

本目录不包含用户账号、Cookie、网盘文件或下载记录。

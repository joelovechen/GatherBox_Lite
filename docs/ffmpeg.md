# FFmpeg 裁剪源码与构建

[返回首页](../README.md)

GatherBox Lite 使用 FFmpeg 将本地音视频分片合并到 MP4 等容器中。项目通过独立进程调用 `ffmpeg.exe`，主要使用 `-c copy`，不需要完整的音视频转码组件。

## 源码和许可

- [裁剪源码](../tools/ffmpeg-minimal-src/)，基于源码树内 `RELEASE` 标识的 FFmpeg 8.1.3。
- [FFmpeg 许可说明](../tools/ffmpeg-minimal-src/LICENSE.md)。
- [LGPL v2.1](../tools/ffmpeg-minimal-src/COPYING.LGPLv2.1) 和 [LGPL v3](../tools/ffmpeg-minimal-src/COPYING.LGPLv3)。
- 源码树也保留部分构建工具所引用的 [GPL v2](../tools/ffmpeg-minimal-src/COPYING.GPLv2) 与 [GPL v3](../tools/ffmpeg-minimal-src/COPYING.GPLv3) 文本；单个文件的版权及许可声明保留在原文件中。

FFmpeg 不适用本项目根目录的 MIT 许可证。默认构建参数未启用 `--enable-gpl` 或 `--enable-nonfree`，实际许可范围以源码及所选构建配置为准。重新分发时应随二进制保留许可说明，并提供与二进制对应的源码和构建方式。

## 裁剪范围

保留应用需要的封装、解封装、码流解析、比特流过滤和部分音频探测组件；关闭网络协议、播放器、探测器程序、文档生成及汇编优化。完整配置参数以构建脚本为准。

源码还包含 Windows MSVC 构建适配，包括中文编译器输出识别和头文件依赖解析。该目录是面向当前配置的裁剪源码，不是完整 FFmpeg 分发；其上游 README 中提到的部分组件和示例不在此目录内。

## 编译

在 Windows 上准备：

- Visual Studio 2022 C++ Build Tools 或脚本支持的 VS 2022 安装版本；
- Git for Windows（含 Git Bash）；
- 可在 PATH 中找到的 GNU Make，或脚本支持的 Android NDK Make。

在项目根目录的 PowerShell 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/build_minimal_ffmpeg.ps1
```

构建中间文件写入 `release/ffmpeg_minimal_build/`，默认输出覆盖 `dev/BilibiliApis-master/ffmpeg.exe`。脚本的 `-UltraSlim` 参数会进一步移除音频探测解码器；发布前应验证所需媒体格式。

`scripts/prune_ffmpeg_source.ps1` 用于从完整源码及已有编译对象提取裁剪树，不是正常构建的前置步骤。当前仓库已包含裁剪结果。

发行包中附带的 `ffmpeg.exe` 与其他程序文件一起分发；源码在本目录公开。可从 GitHub 对应提交或标签下载源码，避免用其他版本的源码代替对应构建版本。

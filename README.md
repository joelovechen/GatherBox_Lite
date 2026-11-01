<div align="center">

<img src="dev/bili-app/public/app-icon.png" width="96" alt="GatherBox Lite" />

# GatherBox Lite

**把视频与文章，整理成自己的离线资料库。**

哔哩哔哩音视频下载 · 知乎内容归档 · 多账号管理 · 本地阅读

[简体中文](README.md) | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Windows x64](https://img.shields.io/badge/Platform-Windows%20x64-blue.svg)](#下载与安装)
[![Build](https://github.com/joelovechen/GatherBox_Lite/actions/workflows/build.yml/badge.svg)](https://github.com/joelovechen/GatherBox_Lite/actions/workflows/build.yml)

[下载](https://github.com/joelovechen/GatherBox_Lite/releases) · [使用文档](docs/usage.md) · [开发指南](docs/development.md) · [问题反馈](https://github.com/joelovechen/GatherBox_Lite/issues)

</div>

## 简介

GatherBox Lite 是面向 Windows 的桌面内容归档工具，将哔哩哔哩下载与知乎离线阅读整合到一个界面中。你可以管理账号、整理关注的作者、查看任务进度，并把有权访问的内容保存到本地。

当前发布目标为 **Windows x64**。macOS 和 Linux 暂无经过验证的发行包。

## 功能

| 功能 | 说明 |
|---|---|
| 哔哩哔哩下载 | 视频解析、音视频质量选择、音频单独下载与任务队列 |
| 作者与合集 | 管理关注作者及合集监听，按设置检查新内容 |
| 知乎归档 | 保存文章与回答，管理作者、收藏和本地阅读内容 |
| 多账号管理 | 在应用内切换账号，按平台及知乎账号隔离数据 |
| 下载控制 | 自定义输出位置、并发数量和请求频率 |
| 桌面体验 | 深浅色主题、缩放设置、任务状态及版本更新提示 |

可获取的内容、画质和音质取决于平台响应及账号权限；项目不提供付费内容解锁。

## 下载与安装

前往 [Releases](https://github.com/joelovechen/GatherBox_Lite/releases)，先阅读对应版本的发布说明。

| 文件 | 适用方式 |
|---|---|
| `GatherBox_Lite_v*_x64-setup.exe` | 安装版：运行安装向导，适合日常使用 |
| `GatherBox_Lite_v*_win64_portable.zip` | 便携版：完整解压到可写目录，运行 `GatherBoxLite.exe` |

运行需要 **Microsoft Edge WebView2 Runtime**。安装版附带在线引导程序；便携版请先准备好运行时。不要只复制主程序 EXE，后端目录也是必需的。

标为“测试”的 Release 不代表正式版本。测试标签与包内版本可能不同，以该 Release 的说明为准。

## 快速开始

1. 启动应用，在所需平台添加账号。
2. 添加内容链接或选择关注的作者、合集及收藏内容。
3. 选择下载或归档任务，在任务列表查看进度。
4. 在设置中调整下载位置、并发及请求频率。

应用默认将数据存放在程序目录下的 `download/`。媒体输出位置可单独设置；更新或迁移前请关闭应用并备份数据。更多说明见[使用与备份](docs/usage.md)。

## 从源码运行

需要 Python 3.11、Node.js 22、pnpm、Rust stable、MSVC C++ Build Tools、Windows SDK 和 WebView2 Runtime。

在 PowerShell 中执行：

```powershell
git clone https://github.com/joelovechen/GatherBox_Lite.git
cd GatherBox_Lite
python -m venv dev/.venv
./dev/.venv/Scripts/python.exe -m pip install -r dev/BilibiliApis-master/requirements.txt
cd dev/bili-app
pnpm install --frozen-lockfile
pnpm tauri dev
```

桌面宿主自动启动本机 Python 后端。完整构建、离线测试和发行包命令见[开发指南](docs/development.md)。

## 文档与项目结构

- [使用说明](docs/usage.md)：安装、更新、数据位置、备份与常见问题。
- [开发指南](docs/development.md)：开发环境、构建、测试和发行包。
- [FFmpeg](docs/ffmpeg.md)：裁剪源码、构建方式及许可证。

```text
dev/bili-app/              Vue 前端与 Tauri 桌面宿主
dev/BilibiliApis-master/   Python 本机服务、下载与归档
scripts/                  构建、安装及更新脚本
tools/ffmpeg-minimal-src/  FFmpeg 裁剪源码
docs/                     使用与开发文档
```

## 反馈与贡献

欢迎提交 [Issue](https://github.com/joelovechen/GatherBox_Lite/issues) 或 Pull Request。反馈时请提供应用版本、Windows 版本、复现步骤和脱敏后的错误信息。PR 请说明修改目的与实际验证结果。

请勿公开上传 Cookie、Token、账号数据库、私人归档或包含这些内容的日志。平台接口可能变化；遇到限流或验证提示，请降低请求频率并按平台要求处理。

## 许可证与使用范围

GatherBox Lite 应用代码采用 [MIT License](LICENSE)。`tools/ffmpeg-minimal-src/` 保留 FFmpeg 自身的许可证与版权声明，不适用项目根目录的 MIT 授权；详见 [FFmpeg 说明](docs/ffmpeg.md)。

请仅下载和保存你有权访问、复制及使用的内容，并遵守相关平台规则。项目与哔哩哔哩、知乎无官方关联。

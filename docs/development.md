# 开发与构建

[返回首页](../README.md)

以下命令在 Windows PowerShell 中执行，以仓库根目录为起点。

## 环境

- Python 3.11。
- Node.js 22 系列及 pnpm，安装前端依赖时使用仓库锁文件。
- Rust stable（MSVC 工具链）、Visual Studio C++ Build Tools 和 Windows SDK。
- Microsoft Edge WebView2 Runtime。
- 完整发行包构建另需 Nuitka 和 NSIS；自编译精简 FFmpeg 另需 VS 2022 C++ 工具、Git Bash 与 GNU Make。

Python 依赖采用版本范围，前端和 Rust 使用锁文件。依赖声明与锁定版本以 `requirements.txt`、`package.json`、`pnpm-lock.yaml` 和 `Cargo.lock` 为准。

## 安装依赖与启动

仓库包含构建所需的 WebView2 在线引导程序和精简 `ffmpeg.exe`。WebView2 引导程序不是完整运行时，运行桌面应用前仍需安装 WebView2 Runtime。FFmpeg 对应的裁剪源码和重建方法见 [FFmpeg 说明](ffmpeg.md)。

```powershell
python -m venv dev/.venv
./dev/.venv/Scripts/python.exe -m pip install -r dev/BilibiliApis-master/requirements.txt
Push-Location dev/bili-app
pnpm install --frozen-lockfile
pnpm tauri dev
Pop-Location
```

桌面宿主负责启动 Python 后端并向前端提供本机连接信息。开发时优先使用此入口。

## 目录

| 路径 | 用途 |
|---|---|
| `dev/bili-app/src/` | Vue 前端 |
| `dev/bili-app/src-tauri/` | Rust 桌面宿主与打包配置 |
| `dev/BilibiliApis-master/` | Python 后端、下载与归档逻辑 |
| `scripts/` | 发行包、安装器及相关构建脚本 |
| `version.json` | 应用版本信息 |

## 构建与检查

前端类型检查及生产构建：

```powershell
Push-Location dev/bili-app
pnpm build
Pop-Location
```

Rust 编译（先完成前端构建）：

```powershell
Push-Location dev/bili-app/src-tauri
cargo build --locked
Pop-Location
```

生成包含前端资源的桌面壳：

```powershell
Push-Location dev/bili-app
pnpm desktop:build
Pop-Location
```

此命令不生成独立 Python 后端发行包；源码工作区运行仍需前面创建的虚拟环境。

后端离线回归请在独立 PowerShell 窗口运行，避免临时数据目录影响日常启动：

```powershell
$env:BILI_DATA_DIR = Join-Path $env:TEMP ('gatherbox-test-' + [guid]::NewGuid().ToString('N'))
Push-Location dev/BilibiliApis-master
../.venv/Scripts/python.exe -m unittest test_zhihu_lifecycle test_download_and_update_guards -q
Pop-Location
```

这些测试不替代真实账号访问、安装升级和桌面交互验证。提交 PR 时报告实际执行的命令及结果。

## 完整发行包

先安装构建依赖：

```powershell
./dev/.venv/Scripts/python.exe -m pip install nuitka ordered-set zstandard
```

确认 WebView2 引导安装器、NSIS，以及 `dev/BilibiliApis-master/ffmpeg.exe` 已准备好。缺少 FFmpeg 时，现有发行脚本会调用 `scripts/build_minimal_ffmpeg.ps1`，依赖对应的 `tools/ffmpeg-minimal-src/` 源码树及其编译工具；这些源码和脚本已包含在仓库中。

关闭正在运行的 GatherBox 后，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/build_release_packages.ps1
```

产物写入 `release/dist/`，包括 Windows x64 便携 ZIP 和安装 EXE。首次构建不要使用跳过编译参数。发布时核对 `version.json`、前端包版本、Tauri 配置及 Cargo 包版本，并保留 FFmpeg 许可材料及对应源码链接。

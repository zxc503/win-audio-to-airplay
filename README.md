# Windows to AirPlay

中文 | [English](#english)

把 Windows 系统音频通过 AirPlay / RAOP 推送到 AirPlay兼容设备。已经在Xiaomi Sound上测试通过

本项目基于：

- `PyAudioWPatch`：抓取 Windows WASAPI loopback 系统音频
- `ffmpeg`：把 PCM 编码成 MP3
- `pyatv`：通过 AirPlay / RAOP 发送音频，并调节设备音量

当前提供：

- 命令行工具
- 桌面 GUI

## 功能

- 扫描局域网中的 AirPlay / RAOP 设备
- 将 Windows 系统音频实时推送到一个或多个 HomePod
- 支持选择 Windows 默认输出设备，或指定某个 loopback 设备
- 支持 GUI 勾选设备后点击按钮开始 / 停止投流
- 支持通过 `pyatv` 调节每个已连接设备的音量
- 支持 RAOP 配对和凭据存储

## 工作原理

音频链路如下：

`Windows WASAPI loopback -> ffmpeg(MP3) -> pyatv.stream.stream_file(...) -> HomePod`

多设备模式下，会为每个目标设备单独建立一条编码和推流链路。

## 运行环境

- Windows 10 / 11
- Python `3.11` 到 `3.13`
- FFmpeg
- 目标设备与 Windows 主机位于同一局域网

注意：

- 当前项目显式限制 Python `< 3.14`
- 多设备模式不保证 AirPlay 2 级别的严格同步
- 如果你把当前被抓取的 Windows 输出设备静音，HomePod 也会一起静音

## 安装

### 方式一：下载预编译版本

如果你想直接使用预编译版本，可以从 [v0.1 release](https://github.com/zxc503/win-audio-to-airplay/releases/tag/v0.1) 下载。

预编译版本不需要安装虚拟环境，也不需要单独安装 FFmpeg。

### 方式二：从源码安装

#### 1. 创建虚拟环境

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

#### 2. 安装 FFmpeg

```powershell
winget install -e --id Gyan.FFmpeg
```

确认安装成功：

```powershell
ffmpeg -version
```

## GUI 用法

启动 GUI：

```powershell
win-airplay-gui
```

如果脚本入口还没刷新，也可以直接运行：

```powershell
python -m windows_to_airplay.gui
```

GUI 支持：

- `Discover devices`：扫描 RAOP 设备
- 勾选一个或多个设备作为目标
- `Start stream`：开始推流
- `Stop stream`：停止推流
- `Audio device`：选择 Windows loopback 输入源
- `Bitrate`：选择编码比特率（`192k` / `256k` / `320k`）
- `Latency mode`：切换低延迟预设
- `Volume` 滑块：调用 `pyatv` 调节对应设备音量
- 自动保存并恢复上一次 GUI 配置

延迟预设：

- `Balanced (512 / 32)`：默认，延迟和稳定性折中
- `Ultra low latency (128 / 32)`：更低延迟，但更容易出现丢块或爆音
- `Stable (1024 / 32)`：更稳，但延迟会更大

GUI 默认直接使用系统 `PATH` 中的 `ffmpeg`。如果你需要手动指定 FFmpeg 路径，请使用 CLI 的 `--ffmpeg-path`。
GUI 配置会保存到本地用户目录，下次打开时自动恢复上次选择的设备、音频源、码率、延迟模式和 host filter。

## CLI 用法

### 列出可用的 Windows loopback 设备

```powershell
win-airplay list-devices
```

### 扫描设备

```powershell
win-airplay discover
```

按 IP 扫描：

```powershell
win-airplay discover --host 192.168.1.50
```

按名称过滤：

```powershell
win-airplay discover --name "Bedroom HomePod"
```

### 配对

如果扫描结果显示 `RAOP pairing=Mandatory`，先执行：

```powershell
win-airplay pair --name "Bedroom HomePod" --protocol raop
```

### 推流到单个设备

```powershell
win-airplay stream --name "Bedroom HomePod"
```

### 推流到多个设备

```powershell
win-airplay stream --name "Living Room" --name "Bedroom"
```

混合使用名称和 IP：

```powershell
win-airplay stream --name "Living Room" --host 192.168.1.51
```

### 指定音频设备和 FFmpeg

```powershell
win-airplay stream --name "Bedroom HomePod" --device-index 19 --ffmpeg-path "C:\ffmpeg\bin\ffmpeg.exe"
```

## 项目结构

```text
windows_to_airplay/
├─ airplay.py        # pyatv 扫描、存储、配对辅助
├─ capture.py        # Windows WASAPI loopback 抓音频
├─ ffmpeg.py         # ffmpeg 编码进程封装
├─ cli.py            # 命令行入口
├─ gui_backend.py    # GUI 后台异步运行时
└─ gui.py            # tkinter GUI
```

## 已知限制

- 多设备模式使用多条独立推流链路，CPU 占用会随目标数量增加
- 多设备模式只能做到尽量接近同步，不保证严格同步
- 同一房间同时播放多个目标时，可能听到轻微回声
- GUI 音量滑块只有在设备成功连接后才会启用
- 如果目标设备被别的控制端占用，推流可能失败或中断

## 排障
1. 如果希望电脑本地静音，但 HomePod 继续播，安装一个Steam，将电脑的音频输出设备改为Steam Steaming Speaker

## License

本项目使用 MIT License，见 [LICENSE](LICENSE)。

## Changelog

版本记录见 [CHANGELOG.md](CHANGELOG.md)。

## 致谢

- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)
- [pyatv](https://github.com/postlund/pyatv)
- [FFmpeg](https://ffmpeg.org/)

---

## English

Stream Windows system audio to compatible AirPlay / RAOP speakers. Test passed On Xiaomi Sound

This project is built on:

- `PyAudioWPatch` for Windows WASAPI loopback capture
- `ffmpeg` for PCM to MP3 encoding
- `pyatv` for AirPlay / RAOP audio streaming and device volume control

It currently includes:

- a command-line interface
- a desktop GUI

## Features

- Discover AirPlay / RAOP devices on the local network
- Stream Windows system audio to one or more HomePods
- Use the default Windows output device or select a specific loopback device
- Start and stop streaming from the GUI with checkbox-selected devices
- Control per-device volume via `pyatv`
- Support RAOP pairing and credential storage

## How It Works

The audio pipeline is:

`Windows WASAPI loopback -> ffmpeg(MP3) -> pyatv.stream.stream_file(...) -> HomePod`

In multi-device mode, each target device gets its own encoding and streaming pipeline.

## Requirements

- Windows 10 / 11
- Python `3.11` to `3.13`
- FFmpeg
- The target device and the Windows machine must be on the same LAN

Notes:

- The project explicitly targets Python `< 3.14`
- Multi-device mode does not guarantee strict AirPlay 2-grade synchronization
- If you mute the Windows output device being captured, HomePod output will also be muted

## Installation

### Option 1: Download a prebuilt release

If you want a prebuilt package, download it from the [v0.1 release](https://github.com/zxc503/win-audio-to-airplay/releases/tag/v0.1).

The prebuilt release does not require a virtual environment or a separate FFmpeg installation.

### Option 2: Install from source

#### 1. Create a virtual environment

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

#### 2. Install FFmpeg

```powershell
winget install -e --id Gyan.FFmpeg
```

Verify the installation:

```powershell
ffmpeg -version
```

## GUI Usage

Launch the GUI:

```powershell
win-airplay-gui
```

If the script entry point is not refreshed yet, run:

```powershell
python -m windows_to_airplay.gui
```

The GUI supports:

- `Discover devices` to scan RAOP devices
- selecting one or more target devices
- `Start stream` to begin streaming
- `Stop stream` to stop streaming
- `Audio device` to choose the Windows loopback source
- `Bitrate` to choose the encoding bitrate (`192k` / `256k` / `320k`)
- `Latency mode` to switch between latency presets
- `Volume` sliders to control device volume through `pyatv`
- automatic save and restore of the previous GUI configuration

Latency presets:

- `Balanced (512 / 32)`: default balance between latency and stability
- `Ultra low latency (128 / 32)`: lower latency, but more likely to drop chunks or crackle
- `Stable (1024 / 32)`: more stable, but with higher latency

The GUI always uses `ffmpeg` from your system `PATH`. If you need to specify a custom FFmpeg path, use the CLI with `--ffmpeg-path`.
The GUI stores its configuration in the local user profile and restores the last selected devices, audio source, bitrate, latency mode, and host filter on the next launch.

## CLI Usage

### List available Windows loopback devices

```powershell
win-airplay list-devices
```

### Discover devices

```powershell
win-airplay discover
```

Scan a specific IP:

```powershell
win-airplay discover --host 192.168.1.50
```

Filter by name:

```powershell
win-airplay discover --name "Bedroom HomePod"
```

### Pairing

If discovery shows `RAOP pairing=Mandatory`, run:

```powershell
win-airplay pair --name "Bedroom HomePod" --protocol raop
```

### Stream to a single device

```powershell
win-airplay stream --name "Bedroom HomePod"
```

### Stream to multiple devices

```powershell
win-airplay stream --name "Living Room" --name "Bedroom"
```

Mix names and IPs:

```powershell
win-airplay stream --name "Living Room" --host 192.168.1.51
```

### Specify the audio device and FFmpeg path

```powershell
win-airplay stream --name "Bedroom HomePod" --device-index 19 --ffmpeg-path "C:\ffmpeg\bin\ffmpeg.exe"
```

## Project Structure

```text
windows_to_airplay/
├─ airplay.py        # pyatv discovery, storage, and pairing helpers
├─ capture.py        # Windows WASAPI loopback capture
├─ ffmpeg.py         # ffmpeg encoder process wrapper
├─ cli.py            # command-line entry point
├─ gui_backend.py    # asynchronous backend for the GUI
└─ gui.py            # tkinter GUI
```

## Known Limitations

- Multi-device mode uses separate streaming pipelines, so CPU usage grows with the number of targets
- Multi-device mode aims for near-sync, not strict sync
- You may hear slight echo if multiple targets are played in the same room
- GUI volume sliders are enabled only after a device is connected
- Streaming may fail or get interrupted if a target device is already controlled elsewhere

## Troubleshooting

1.If you want to mute the computer locally while still letting the HomePod play audio, install Steam and set the computer’s audio output device to “Steam Streaming Speaker.”

## License

This project is released under the MIT License. See [LICENSE](LICENSE).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Acknowledgements

- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)
- [pyatv](https://github.com/postlund/pyatv)
- [FFmpeg](https://ffmpeg.org/)

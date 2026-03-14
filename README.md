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

GUI 默认直接使用系统 `PATH` 中的 `ffmpeg`。如果你需要手动指定 FFmpeg 路径，请使用 CLI 的 `--ffmpeg-path`。
GUI 配置会保存到本地用户目录，下次打开时自动恢复上次选择的设备、音频源、码率、延迟模式和 host filter。

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

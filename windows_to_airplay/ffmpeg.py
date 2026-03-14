from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Deque


@dataclass(frozen=True)
class EncoderConfig:
    executable: str = "ffmpeg"
    input_rate: int = 48_000
    input_channels: int = 2
    output_channels: int = 2
    bitrate: str = "192k"


def build_ffmpeg_command(config: EncoderConfig) -> list[str]:
    return [
        config.executable,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-f",
        "s16le",
        "-ac",
        str(config.input_channels),
        "-ar",
        str(config.input_rate),
        "-i",
        "pipe:0",
        "-vn",
        "-flush_packets",
        "1",
        "-ac",
        str(config.output_channels),
        "-codec:a",
        "libmp3lame",
        "-write_xing",
        "0",
        "-b:a",
        config.bitrate,
        "-f",
        "mp3",
        "pipe:1",
    ]


class FfmpegMp3Encoder:
    def __init__(self, config: EncoderConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._stderr_tail: Deque[str] = deque(maxlen=40)
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def stdout(self) -> asyncio.StreamReader:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("ffmpeg process has not been started")
        return self.process.stdout

    async def start(self) -> None:
        command = build_ffmpeg_command(self.config)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"找不到 ffmpeg: {self.config.executable}. 请安装 FFmpeg 或通过 --ffmpeg-path 指定可执行文件。"
            ) from exc

        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def write(self, data: bytes) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("ffmpeg stdin is not available")
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def close_stdin(self) -> None:
        if self.process is None or self.process.stdin is None:
            return
        if self.process.stdin.is_closing():
            return
        self.process.stdin.close()
        try:
            await self.process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            return

    async def wait(self) -> int:
        if self.process is None:
            return 0
        return await self.process.wait()

    async def stop(self) -> int:
        if self.process is None:
            return 0

        await self.close_stdin()

        try:
            code = await asyncio.wait_for(self.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.process.terminate()
            try:
                code = await asyncio.wait_for(self.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.process.kill()
                code = await self.process.wait()

        if self._stderr_task is not None:
            await self._stderr_task

        return code

    def stderr_summary(self) -> str:
        if not self._stderr_tail:
            return ""
        return "\n".join(self._stderr_tail)

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return

        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)

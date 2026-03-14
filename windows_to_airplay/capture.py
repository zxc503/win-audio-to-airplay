from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoopbackDevice:
    index: int
    name: str
    sample_rate: int
    channels: int
    is_default: bool = False


def list_loopback_devices() -> list[LoopbackDevice]:
    pyaudio = _import_pyaudio()
    devices: list[LoopbackDevice] = []

    with pyaudio.PyAudio() as pa:
        default_index = None
        try:
            default_index = int(_resolve_loopback_device(pa, None)["index"])
        except RuntimeError:
            default_index = None

        for info in pa.get_loopback_device_info_generator():
            devices.append(
                LoopbackDevice(
                    index=int(info["index"]),
                    name=str(info["name"]),
                    sample_rate=int(info["defaultSampleRate"]),
                    channels=int(info["maxInputChannels"]),
                    is_default=default_index == int(info["index"]),
                )
            )

    return devices


class WasapiLoopbackCapture:
    def __init__(
        self,
        device_index: int | None = None,
        frames_per_buffer: int = 512,
        queue_chunks: int = 16,
    ) -> None:
        self.device_index = device_index
        self.frames_per_buffer = frames_per_buffer
        self.queue_chunks = queue_chunks
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_chunks)
        self.device: LoopbackDevice | None = None
        self._pa = None
        self._pyaudio = None
        self._stream = None
        self._closing = False
        self._dropped_chunks = 0

    @property
    def dropped_chunks(self) -> int:
        return self._dropped_chunks

    async def start(self) -> "WasapiLoopbackCapture":
        self._pyaudio = _import_pyaudio()
        self._pa = self._pyaudio.PyAudio()
        info = _resolve_loopback_device(self._pa, self.device_index)
        self.device = LoopbackDevice(
            index=int(info["index"]),
            name=str(info["name"]),
            sample_rate=int(info["defaultSampleRate"]),
            channels=int(info["maxInputChannels"]),
        )

        self._stream = self._pa.open(
            format=self._pyaudio.paInt16,
            channels=self.device.channels,
            rate=self.device.sample_rate,
            frames_per_buffer=self.frames_per_buffer,
            input=True,
            input_device_index=self.device.index,
            stream_callback=self._callback,
        )
        return self

    async def stop(self) -> None:
        if self._closing:
            return
        self._closing = True

        if self._stream is not None:
            if self._stream.is_active():
                self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

        self._push_sentinel()

    async def iter_chunks(self):
        while True:
            chunk = await self.queue.get()
            if chunk is None:
                return
            yield chunk

    def _callback(self, in_data, frame_count, time_info, status):
        if self._closing:
            return (in_data, self._pyaudio.paComplete)

        payload = bytes(in_data)

        try:
            self.loop.call_soon_threadsafe(self._enqueue_chunk, payload)
        except RuntimeError:
            return (in_data, self._pyaudio.paAbort)

        return (in_data, self._pyaudio.paContinue)

    def _enqueue_chunk(self, payload: bytes) -> None:
        if self._closing:
            return

        if self.queue.full():
            try:
                self.queue.get_nowait()
                self._dropped_chunks += 1
            except asyncio.QueueEmpty:
                pass

        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            self._dropped_chunks += 1

    def _push_sentinel(self) -> None:
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait(None)
            except asyncio.QueueFull:
                return


def _resolve_loopback_device(pa: Any, device_index: int | None) -> dict[str, Any]:
    if device_index is None:
        if hasattr(pa, "get_default_wasapi_loopback"):
            return pa.get_default_wasapi_loopback()
        return _resolve_default_loopback(pa)

    info = pa.get_device_info_by_index(device_index)
    if info.get("isLoopbackDevice"):
        return info

    if hasattr(pa, "get_wasapi_loopback_analogue_by_index"):
        return pa.get_wasapi_loopback_analogue_by_index(device_index)

    for loopback in pa.get_loopback_device_info_generator():
        if info["name"] in loopback["name"]:
            return loopback

    raise RuntimeError(
        f"设备 {device_index} 不是 loopback 设备，也找不到对应的 WASAPI loopback 设备。"
    )


def _resolve_default_loopback(pa: Any) -> dict[str, Any]:
    try:
        wasapi = pa.get_host_api_info_by_type(_import_pyaudio().paWASAPI)
    except OSError as exc:
        raise RuntimeError("当前系统不可用 WASAPI，无法抓取系统音频。") from exc

    default_output = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if default_output.get("isLoopbackDevice"):
        return default_output

    for loopback in pa.get_loopback_device_info_generator():
        if default_output["name"] in loopback["name"]:
            return loopback

    raise RuntimeError(
        "找不到默认输出设备对应的 loopback 设备。可先运行 list-devices 查看可用设备。"
    )


def _import_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 PyAudioWPatch。请使用受支持的 Python 版本后执行 `python -m pip install -e .`。"
        ) from exc

    return pyaudio

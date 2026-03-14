from __future__ import annotations

import asyncio
import platform
from dataclasses import dataclass
from typing import Any, Callable

from .airplay import apply_raop_password, create_storage, scan_devices
from .capture import LoopbackDevice, WasapiLoopbackCapture, list_loopback_devices
from .ffmpeg import EncoderConfig, FfmpegMp3Encoder

EventCallback = Callable[[str], None]


@dataclass(slots=True)
class DiscoveredAudioDevice:
    name: str
    address: str
    identifier: str | None
    model: str
    raop_pairing: str
    has_credentials: bool
    requires_password: bool


@dataclass(slots=True)
class StreamSession:
    device: Any
    atv: Any
    encoder: FfmpegMp3Encoder
    pcm_queue: asyncio.Queue[bytes | None]
    listener: "SessionListener"
    dropped_chunks: int = 0
    pump_task: asyncio.Task[None] | None = None
    stream_task: asyncio.Task[None] | None = None

    @property
    def address(self) -> str:
        return str(self.device.address)

    @property
    def label(self) -> str:
        return f"{self.device.name} ({self.device.address})"


class SessionListener:
    def __init__(self, emit: Callable[..., None], address: str) -> None:
        self._emit = emit
        self._address = address

    def connection_lost(self, exception: Exception) -> None:
        self._emit(
            "device_status",
            address=self._address,
            status="connection_lost",
            detail=str(exception),
        )

    def connection_closed(self) -> None:
        self._emit(
            "device_status",
            address=self._address,
            status="connection_closed",
            detail="connection closed",
        )

    def volume_update(self, old_level: float, new_level: float) -> None:
        self._emit(
            "volume",
            address=self._address,
            old_level=old_level,
            new_level=new_level,
        )

    def volume_device_update(self, output_device, old_level: float, new_level: float) -> None:
        self._emit(
            "volume",
            address=self._address,
            old_level=old_level,
            new_level=new_level,
        )

    def outputdevices_update(self, old_devices, new_devices) -> None:
        self._emit(
            "device_status",
            address=self._address,
            status="outputs_updated",
            detail=f"{len(new_devices)} output device(s)",
        )


class AudioStreamBackend:
    def __init__(self, emit: Callable[..., None], storage_path: str | None = None) -> None:
        self._emit = emit
        self._storage_path = storage_path
        self._storage = None
        self._discovered_configs: dict[str, Any] = {}
        self._capture: WasapiLoopbackCapture | None = None
        self._sessions: dict[str, StreamSession] = {}
        self._run_task: asyncio.Task[None] | None = None
        self._starting = False

    @property
    def is_streaming(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    async def list_audio_devices(self) -> list[LoopbackDevice]:
        return list_loopback_devices()

    async def discover_devices(self, host: str | None = None) -> list[DiscoveredAudioDevice]:
        storage = await self._ensure_storage()
        loop = asyncio.get_running_loop()
        configs = await scan_devices(loop, storage, host=host)

        devices: list[DiscoveredAudioDevice] = []
        discovered_configs: dict[str, Any] = {}
        for config in configs:
            raop_service = config.get_service(_protocol_enum().RAOP)
            if raop_service is None:
                continue

            address = str(config.address)
            discovered_configs[address] = config
            devices.append(
                DiscoveredAudioDevice(
                    name=config.name,
                    address=address,
                    identifier=config.identifier,
                    model=str(getattr(config.device_info, "model", "")),
                    raop_pairing=getattr(raop_service.pairing, "name", str(raop_service.pairing)),
                    has_credentials=bool(raop_service.credentials),
                    requires_password=bool(getattr(raop_service, "requires_password", False)),
                )
            )

        devices.sort(key=lambda item: (item.name.lower(), item.address))
        self._discovered_configs = discovered_configs
        self._emit("log", level="info", message=f"discovered {len(devices)} RAOP device(s)")
        return devices

    async def start_stream(
        self,
        addresses: list[str],
        *,
        device_index: int | None = None,
        ffmpeg_path: str = "ffmpeg",
        bitrate: str = "192k",
        frames_per_buffer: int = 512,
        queue_chunks: int = 16,
        title: str = "Windows System Audio",
        artist: str | None = None,
        raop_password: str | None = None,
    ) -> None:
        if self.is_streaming or self._starting:
            raise RuntimeError("stream is already running")
        if not addresses:
            raise RuntimeError("no target devices selected")

        self._starting = True
        started = asyncio.Event()
        startup_error: list[Exception] = []
        artist = artist or platform.node() or "Windows"

        async def runner() -> None:
            try:
                await self._run_stream(
                    addresses=addresses,
                    device_index=device_index,
                    ffmpeg_path=ffmpeg_path,
                    bitrate=bitrate,
                    frames_per_buffer=frames_per_buffer,
                    queue_chunks=queue_chunks,
                    title=title,
                    artist=artist,
                    raop_password=raop_password,
                    started=started,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                startup_error.append(exc)
                self._emit("error", message=str(exc))
                if not started.is_set():
                    started.set()
                raise
            finally:
                self._starting = False

        self._run_task = asyncio.create_task(runner(), name="windows_to_airplay_gui_stream")
        await started.wait()

        if startup_error:
            await self.stop_stream()
            raise RuntimeError(str(startup_error[0])) from startup_error[0]

    async def stop_stream(self) -> None:
        if self._run_task is None:
            return
        if not self._run_task.done():
            self._run_task.cancel()
        await asyncio.gather(self._run_task, return_exceptions=True)
        self._run_task = None

    async def set_volume(self, address: str, level: float) -> None:
        session = self._sessions.get(address)
        if session is None:
            raise RuntimeError(f"{address} is not connected")

        await session.atv.audio.set_volume(level)
        self._emit("volume", address=address, old_level=None, new_level=level)

    async def shutdown(self) -> None:
        await self.stop_stream()

    async def _run_stream(
        self,
        *,
        addresses: list[str],
        device_index: int | None,
        ffmpeg_path: str,
        bitrate: str,
        frames_per_buffer: int,
        queue_chunks: int,
        title: str,
        artist: str,
        raop_password: str | None,
        started: asyncio.Event,
    ) -> None:
        from pyatv.interface import MediaMetadata

        capture: WasapiLoopbackCapture | None = None
        sessions: list[StreamSession] = []
        fanout_task: asyncio.Task[None] | None = None

        try:
            target_configs = await self._resolve_configs(addresses)
            storage = await self._ensure_storage()

            for config in target_configs:
                await apply_raop_password(storage, config, raop_password)

            capture = WasapiLoopbackCapture(
                device_index=device_index,
                frames_per_buffer=frames_per_buffer,
                queue_chunks=queue_chunks,
            )
            await capture.start()
            if capture.device is None:
                raise RuntimeError("failed to initialize loopback capture")

            metadata = MediaMetadata(title=title, artist=artist)
            for config in target_configs:
                session = await self._open_stream_session(
                    storage=storage,
                    device=config,
                    sample_rate=capture.device.sample_rate,
                    channels=capture.device.channels,
                    ffmpeg_path=ffmpeg_path,
                    bitrate=bitrate,
                    queue_chunks=queue_chunks,
                    metadata=metadata,
                )
                sessions.append(session)

            self._capture = capture
            self._sessions = {session.address: session for session in sessions}

            labels = ", ".join(session.label for session in sessions)
            self._emit(
                "stream_started",
                targets=[session.address for session in sessions],
                detail=labels,
            )
            self._emit(
                "log",
                level="info",
                message=(
                    f"streaming to {labels} from "
                    f"{capture.device.name} ({capture.device.sample_rate} Hz)"
                ),
            )

            started.set()
            fanout_task = asyncio.create_task(_fanout_capture_to_sessions(capture, sessions))
            await _wait_for_stream_tasks(fanout_task, sessions)
        finally:
            await _cleanup_stream(
                emit=self._emit,
                capture=capture,
                sessions=sessions,
                fanout_task=fanout_task,
            )
            self._capture = None
            self._sessions = {}
            self._emit("stream_stopped")
            if not started.is_set():
                started.set()

    async def _open_stream_session(
        self,
        *,
        storage,
        device,
        sample_rate: int,
        channels: int,
        ffmpeg_path: str,
        bitrate: str,
        queue_chunks: int,
        metadata,
    ) -> StreamSession:
        pyatv = _import_pyatv()
        atv = await pyatv.connect(device, asyncio.get_running_loop(), storage=storage)
        encoder = FfmpegMp3Encoder(
            EncoderConfig(
                executable=ffmpeg_path,
                input_rate=sample_rate,
                input_channels=channels,
                output_channels=2,
                bitrate=bitrate,
            )
        )
        listener = SessionListener(self._emit, str(device.address))
        atv.listener = listener
        atv.audio.listener = listener

        try:
            await encoder.start()
            session = StreamSession(
                device=device,
                atv=atv,
                encoder=encoder,
                pcm_queue=asyncio.Queue(maxsize=queue_chunks),
                listener=listener,
            )
            session.pump_task = asyncio.create_task(_pump_session_queue(session))
            session.stream_task = asyncio.create_task(_stream_session(session, metadata))
            self._emit("device_status", address=session.address, status="connected", detail=session.label)

            try:
                current_volume = float(session.atv.audio.volume)
            except Exception:
                current_volume = 50.0
            self._emit("volume", address=session.address, old_level=None, new_level=current_volume)
            return session
        except Exception:
            code = await encoder.stop()
            if code not in (0, 255):
                self._emit(
                    "log",
                    level="warning",
                    message=f"{device.name}: ffmpeg exited during startup with code {code}",
                )
            pending = atv.close()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise

    async def _resolve_configs(self, addresses: list[str]) -> list[Any]:
        storage = await self._ensure_storage()
        loop = asyncio.get_running_loop()
        configs: list[Any] = []

        for address in addresses:
            config = self._discovered_configs.get(address)
            if config is None:
                scanned = await scan_devices(loop, storage, host=address)
                if not scanned:
                    raise RuntimeError(f"device not found: {address}")
                config = next(
                    (item for item in scanned if str(item.address) == address),
                    scanned[0],
                )
                self._discovered_configs[address] = config
            configs.append(config)

        deduped: dict[str, Any] = {}
        for config in configs:
            deduped[str(config.address)] = config
        return list(deduped.values())

    async def _ensure_storage(self):
        if self._storage is None:
            self._storage = await create_storage(asyncio.get_running_loop(), self._storage_path)
        return self._storage


async def _stream_session(session: StreamSession, metadata) -> None:
    try:
        await session.atv.stream.stream_file(session.encoder.stdout, metadata=metadata)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{session.label} stream failed: {exc}") from exc


async def _fanout_capture_to_sessions(
    capture: WasapiLoopbackCapture,
    sessions: list[StreamSession],
) -> None:
    try:
        async for chunk in capture.iter_chunks():
            for session in sessions:
                _enqueue_session_chunk(session, chunk)
    finally:
        for session in sessions:
            _close_session_queue(session)


def _enqueue_session_chunk(session: StreamSession, payload: bytes) -> None:
    if session.pcm_queue.full():
        try:
            session.pcm_queue.get_nowait()
            session.dropped_chunks += 1
        except asyncio.QueueEmpty:
            pass

    try:
        session.pcm_queue.put_nowait(payload)
    except asyncio.QueueFull:
        session.dropped_chunks += 1


def _close_session_queue(session: StreamSession) -> None:
    try:
        session.pcm_queue.put_nowait(None)
    except asyncio.QueueFull:
        try:
            session.pcm_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            session.pcm_queue.put_nowait(None)
        except asyncio.QueueFull:
            return


async def _pump_session_queue(session: StreamSession) -> None:
    try:
        while True:
            chunk = await session.pcm_queue.get()
            if chunk is None:
                return
            await session.encoder.write(chunk)
    except (BrokenPipeError, ConnectionResetError) as exc:
        raise RuntimeError(f"{session.label} ffmpeg encoder interrupted") from exc
    finally:
        await session.encoder.close_stdin()


async def _wait_for_stream_tasks(
    fanout_task: asyncio.Task[None],
    sessions: list[StreamSession],
) -> None:
    tasks: list[asyncio.Task[Any]] = [fanout_task]
    tasks.extend(session.pump_task for session in sessions if session.pump_task is not None)
    tasks.extend(session.stream_task for session in sessions if session.stream_task is not None)

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    for task in done:
        exception = task.exception()
        if exception is not None:
            raise exception

    await asyncio.gather(*pending)


async def _cleanup_stream(
    *,
    emit: Callable[..., None],
    capture: WasapiLoopbackCapture | None,
    sessions: list[StreamSession],
    fanout_task: asyncio.Task[None] | None,
) -> None:
    if capture is not None:
        await capture.stop()

    if fanout_task is not None and not fanout_task.done():
        fanout_task.cancel()
    if fanout_task is not None:
        await asyncio.gather(fanout_task, return_exceptions=True)

    for session in sessions:
        _close_session_queue(session)

    await asyncio.gather(
        *[session.pump_task for session in sessions if session.pump_task is not None],
        return_exceptions=True,
    )

    for session in sessions:
        if session.stream_task is not None and not session.stream_task.done():
            session.stream_task.cancel()
    await asyncio.gather(
        *[session.stream_task for session in sessions if session.stream_task is not None],
        return_exceptions=True,
    )

    for session in sessions:
        code = await session.encoder.stop()
        if code not in (0, 255):
            stderr = session.encoder.stderr_summary()
            if stderr:
                emit("log", level="warning", message=f"{session.label}: ffmpeg exited {code}: {stderr}")
            else:
                emit("log", level="warning", message=f"{session.label}: ffmpeg exited {code}")

    for session in sessions:
        pending = session.atv.close()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        emit("device_status", address=session.address, status="disconnected", detail=session.label)

    if capture is not None and capture.dropped_chunks:
        emit("log", level="warning", message=f"capture dropped {capture.dropped_chunks} chunk(s)")

    for session in sessions:
        if session.dropped_chunks:
            emit(
                "log",
                level="warning",
                message=f"{session.label} dropped {session.dropped_chunks} fan-out chunk(s)",
            )


def _import_pyatv():
    import pyatv

    return pyatv


def _protocol_enum():
    from pyatv.const import Protocol

    return Protocol

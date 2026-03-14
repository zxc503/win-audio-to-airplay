from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import random
from dataclasses import dataclass
from typing import Any, Sequence

from .airplay import (
    apply_raop_password,
    create_storage,
    find_device,
    format_device_summary,
    protocol_name_map,
    scan_devices,
    service_pairing_name,
)
from .capture import WasapiLoopbackCapture, list_loopback_devices
from .ffmpeg import EncoderConfig, FfmpegMp3Encoder

LOGGER = logging.getLogger("windows_to_airplay")


@dataclass(slots=True)
class StreamSession:
    device: Any
    atv: Any
    encoder: FfmpegMp3Encoder
    pcm_queue: asyncio.Queue[bytes | None]
    dropped_chunks: int = 0
    pump_task: asyncio.Task[None] | None = None
    stream_task: asyncio.Task[None] | None = None

    @property
    def label(self) -> str:
        return f"{self.device.name} ({self.device.address})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="win-airplay",
        description="将 Windows 系统音频通过 AirPlay/RAOP 发送到 HomePod。",
    )
    parser.add_argument(
        "--storage",
        help="pyatv 凭据存储文件。默认使用 pyatv 的默认存储路径。",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="输出调试日志。",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="扫描 AirPlay / RAOP 设备。")
    discover.add_argument("--host", help="只扫描指定 IP。")
    discover.add_argument("--name", help="只显示指定名称。")
    discover.set_defaults(handler=cmd_discover)

    pair = subparsers.add_parser("pair", help="对目标设备做协议配对。")
    _add_single_target_arguments(pair)
    pair.add_argument(
        "--protocol",
        choices=["raop", "airplay"],
        default="raop",
        help="要配对的协议，HomePod 音频推流通常使用 raop。",
    )
    pair.add_argument("--pin", type=int, help="显式提供 PIN。")
    pair.add_argument("--raop-password", help="目标设备需要的 RAOP 密码。")
    pair.set_defaults(handler=cmd_pair)

    devices = subparsers.add_parser("list-devices", help="列出可用的 WASAPI loopback 设备。")
    devices.set_defaults(handler=cmd_list_devices)

    stream = subparsers.add_parser("stream", help="将 Windows 系统音频实时推到一个或多个 HomePod。")
    _add_multi_target_arguments(stream)
    stream.add_argument("--device-index", type=int, help="指定 Windows loopback 设备索引。")
    stream.add_argument(
        "--frames-per-buffer",
        type=int,
        default=512,
        help="PyAudio 每次读取的 frame 数，默认 512。",
    )
    stream.add_argument(
        "--queue-chunks",
        type=int,
        default=16,
        help="采集队列和每个设备 fan-out 队列的块数。队列满时会丢弃旧块以控制延迟。",
    )
    stream.add_argument(
        "--bitrate",
        default="192k",
        help="ffmpeg MP3 比特率，默认 192k。",
    )
    stream.add_argument(
        "--ffmpeg-path",
        default="ffmpeg",
        help="ffmpeg 可执行文件路径，默认直接使用 PATH 中的 ffmpeg。",
    )
    stream.add_argument(
        "--title",
        default="Windows System Audio",
        help="推流给 HomePod 时显示的标题。",
    )
    stream.add_argument(
        "--artist",
        default=platform.node() or "Windows",
        help="推流给 HomePod 时显示的艺术家/来源。",
    )
    stream.add_argument("--raop-password", help="目标设备需要的 RAOP 密码。")
    stream.set_defaults(handler=cmd_stream)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        result = args.handler(args)
        if asyncio.iscoroutine(result):
            return asyncio.run(result)
        return int(result or 0)
    except KeyboardInterrupt:
        LOGGER.info("已停止。")
        return 130
    except RuntimeError as exc:
        LOGGER.error(str(exc))
        return 1


def cmd_list_devices(args: argparse.Namespace) -> int:
    devices = list_loopback_devices()
    if not devices:
        raise RuntimeError("没有找到可用的 WASAPI loopback 设备。")

    for device in devices:
        marker = "*" if device.is_default else " "
        print(
            f"{marker} [{device.index}] {device.name} | "
            f"{device.sample_rate} Hz | {device.channels} ch"
        )
    return 0


async def cmd_discover(args: argparse.Namespace) -> int:
    loop = asyncio.get_running_loop()
    storage = await create_storage(loop, args.storage)
    devices = await scan_devices(loop, storage, host=args.host)

    if args.name:
        devices = [device for device in devices if device.name == args.name]

    if not devices:
        raise RuntimeError("没有发现匹配的设备。")

    for index, device in enumerate(devices, start=1):
        if index > 1:
            print()
        print(format_device_summary(device))

    return 0


async def cmd_pair(args: argparse.Namespace) -> int:
    _validate_single_target_args(args)
    loop = asyncio.get_running_loop()
    storage = await create_storage(loop, args.storage)
    device = await find_device(
        loop,
        storage,
        name=args.name,
        identifier=args.identifier,
        host=args.host,
    )
    await apply_raop_password(storage, device, args.raop_password)

    protocol = protocol_name_map()[args.protocol]
    pairing_state = service_pairing_name(device, protocol)

    if pairing_state == "NotNeeded":
        LOGGER.info("%s 的 %s 不需要配对。", device.name, args.protocol.upper())
        return 0

    if pairing_state in {"Disabled", "Unsupported"}:
        raise RuntimeError(
            f"{device.name} 的 {args.protocol.upper()} 当前为 {pairing_state}，无法配对。"
        )

    pyatv = _import_pyatv()
    pairing = await pyatv.pair(device, protocol, loop, storage=storage)

    try:
        await pairing.begin()

        if pairing.device_provides_pin:
            pin = args.pin
            if pin is None:
                pin = int(input("请输入设备上显示的 PIN: "))
            pairing.pin(pin)
        else:
            pin = args.pin or random.randint(1000, 9999)
            pairing.pin(pin)
            input(f"请在设备上输入 PIN {pin}，然后按回车继续...")

        await pairing.finish()
        if not pairing.has_paired:
            raise RuntimeError("配对流程结束，但 pyatv 没有拿到有效凭据。")
    finally:
        await pairing.close()

    await storage.save()
    LOGGER.info("配对完成，凭据已保存到 pyatv storage。")
    return 0


async def cmd_stream(args: argparse.Namespace) -> int:
    _validate_multi_target_args(args)
    loop = asyncio.get_running_loop()
    storage = await create_storage(loop, args.storage)
    devices = await _resolve_stream_devices(loop, storage, args)

    for device in devices:
        await apply_raop_password(storage, device, args.raop_password)

    try:
        from pyatv.interface import MediaMetadata
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 pyatv。请先创建受支持的 Python 环境并执行 `python -m pip install -e .`。"
        ) from exc

    capture = WasapiLoopbackCapture(
        device_index=args.device_index,
        frames_per_buffer=args.frames_per_buffer,
        queue_chunks=args.queue_chunks,
    )
    sessions: list[StreamSession] = []
    fanout_task: asyncio.Task[None] | None = None

    try:
        await capture.start()
        if capture.device is None:
            raise RuntimeError("未能初始化 loopback 采集设备。")

        metadata = MediaMetadata(title=args.title, artist=args.artist)
        for device in devices:
            session = await _open_stream_session(
                loop=loop,
                storage=storage,
                device=device,
                sample_rate=capture.device.sample_rate,
                channels=capture.device.channels,
                ffmpeg_path=args.ffmpeg_path,
                bitrate=args.bitrate,
                queue_chunks=args.queue_chunks,
                metadata=metadata,
            )
            sessions.append(session)

        target_labels = ", ".join(session.label for session in sessions)
        LOGGER.info(
            "开始推流: targets=%s, input=[%s] %s, rate=%s, channels=%s",
            target_labels,
            capture.device.index,
            capture.device.name,
            capture.device.sample_rate,
            capture.device.channels,
        )
        if len(sessions) > 1:
            LOGGER.info("多设备模式不保证严格同步。")
        LOGGER.info("按 Ctrl+C 停止。")

        fanout_task = asyncio.create_task(_fanout_capture_to_sessions(capture, sessions))
        await _wait_for_stream_tasks(fanout_task, sessions)
    except asyncio.CancelledError:
        LOGGER.info("正在停止推流...")
        raise
    finally:
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
                    LOGGER.warning("%s 的 ffmpeg 退出码 %s:\n%s", session.label, code, stderr)
                else:
                    LOGGER.warning("%s 的 ffmpeg 非正常退出: %s", session.label, code)

        for session in sessions:
            pending = session.atv.close()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if capture.dropped_chunks:
            LOGGER.warning("音频采集过程中丢弃了 %s 个缓冲块以保持低延迟。", capture.dropped_chunks)

        for session in sessions:
            if session.dropped_chunks:
                LOGGER.warning("%s 丢弃了 %s 个 fan-out 缓冲块。", session.label, session.dropped_chunks)

    return 0


async def _open_stream_session(
    *,
    loop: asyncio.AbstractEventLoop,
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
    atv = await pyatv.connect(device, loop, storage=storage)
    encoder = FfmpegMp3Encoder(
        EncoderConfig(
            executable=ffmpeg_path,
            input_rate=sample_rate,
            input_channels=channels,
            output_channels=2,
            bitrate=bitrate,
        )
    )

    try:
        await encoder.start()
        session = StreamSession(
            device=device,
            atv=atv,
            encoder=encoder,
            pcm_queue=asyncio.Queue(maxsize=queue_chunks),
        )
        session.pump_task = asyncio.create_task(_pump_session_queue(session))
        session.stream_task = asyncio.create_task(_stream_session(session, metadata))
        return session
    except Exception:
        code = await encoder.stop()
        if code not in (0, 255):
            LOGGER.debug("%s 的 ffmpeg 在初始化阶段退出: %s", device.name, code)
        pending = atv.close()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        raise


async def _stream_session(session: StreamSession, metadata) -> None:
    try:
        await session.atv.stream.stream_file(session.encoder.stdout, metadata=metadata)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{session.label} 推流失败: {exc}") from exc


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
        raise RuntimeError(f"{session.label} 的 ffmpeg 编码进程意外中断。") from exc
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


async def _resolve_stream_devices(
    loop: asyncio.AbstractEventLoop,
    storage,
    args: argparse.Namespace,
):
    lookups = [find_device(loop, storage, name=name) for name in (args.name or [])]
    lookups.extend(
        find_device(loop, storage, identifier=identifier)
        for identifier in (args.identifier or [])
    )
    lookups.extend(find_device(loop, storage, host=host) for host in (args.host or []))

    devices = await asyncio.gather(*lookups)

    deduped: dict[str, Any] = {}
    for device in devices:
        deduped[str(device.address)] = device
    return list(deduped.values())


def _add_single_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", help="设备名称。")
    parser.add_argument("--id", dest="identifier", help="设备标识符。")
    parser.add_argument("--host", help="设备 IP。")


def _add_multi_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--name",
        action="append",
        help="设备名称。可重复传入以指定多个目标。",
    )
    parser.add_argument(
        "--id",
        dest="identifier",
        action="append",
        help="设备标识符。可重复传入以指定多个目标。",
    )
    parser.add_argument(
        "--host",
        action="append",
        help="设备 IP。可重复传入以指定多个目标。",
    )


def _validate_single_target_args(args: argparse.Namespace) -> None:
    if not any([args.name, args.identifier, args.host]):
        raise RuntimeError("必须至少提供 --name、--id 或 --host 之一。")


def _validate_multi_target_args(args: argparse.Namespace) -> None:
    if not any([args.name, args.identifier, args.host]):
        raise RuntimeError("必须至少提供一个 --name、--id 或 --host。")


def _import_pyatv():
    try:
        import pyatv
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 pyatv。请先创建受支持的 Python 环境并执行 `python -m pip install -e .`。"
        ) from exc

    return pyatv

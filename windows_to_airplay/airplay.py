from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


def protocol_name_map() -> dict[str, Any]:
    Protocol = _pyatv_protocol_enum()
    return {
        "raop": Protocol.RAOP,
        "airplay": Protocol.AirPlay,
    }


async def create_storage(loop: asyncio.AbstractEventLoop, filename: str | None):
    FileStorage = _pyatv_file_storage()
    storage = (
        FileStorage(str(Path(filename).expanduser()), loop)
        if filename
        else FileStorage.default_storage(loop)
    )
    await storage.load()
    return storage


async def scan_devices(
    loop: asyncio.AbstractEventLoop,
    storage,
    *,
    identifier: str | None = None,
    host: str | None = None,
):
    pyatv = _import_pyatv()
    kwargs: dict[str, Any] = {"storage": storage}
    if identifier:
        kwargs["identifier"] = identifier
    if host:
        kwargs["hosts"] = [host]
    return await pyatv.scan(loop, **kwargs)


async def find_device(
    loop: asyncio.AbstractEventLoop,
    storage,
    *,
    name: str | None = None,
    identifier: str | None = None,
    host: str | None = None,
):
    devices = await scan_devices(loop, storage, identifier=identifier, host=host)

    if name:
        devices = [device for device in devices if device.name == name]

    if not devices:
        selector = name or identifier or host or "unknown target"
        raise RuntimeError(f"没有找到匹配设备: {selector}")

    if len(devices) > 1:
        names = ", ".join(f"{device.name} ({device.address})" for device in devices)
        raise RuntimeError(f"匹配到多个设备，请改用 --id 或 --host 精确指定: {names}")

    return devices[0]


def format_device_summary(device) -> str:
    lines = [
        f"Name: {device.name}",
        f"Address: {device.address}",
    ]

    model = getattr(device.device_info, "model", None)
    os_version = getattr(device.device_info, "version", None)
    if model or os_version:
        lines.append(f"Model/SW: {model} {os_version}".strip())

    services = sorted(device.services, key=lambda service: service.protocol.name)
    for service in services:
        credentials = "set" if service.credentials else "none"
        pairing = getattr(service.pairing, "name", str(service.pairing))
        requires_password = getattr(service, "requires_password", False)
        lines.append(
            "  "
            + ", ".join(
                [
                    f"{service.protocol.name}",
                    f"port={service.port}",
                    f"pairing={pairing}",
                    f"credentials={credentials}",
                    f"password={'yes' if requires_password else 'no'}",
                ]
            )
        )

    return "\n".join(lines)


async def apply_raop_password(storage, device, password: str | None) -> None:
    if not password:
        return
    settings = await storage.get_settings(device)
    settings.raop.password = password
    await storage.save()


def service_pairing_name(device, protocol) -> str:
    service = device.get_service(protocol)
    if service is None:
        raise RuntimeError(f"设备不支持 {protocol.name}")
    return getattr(service.pairing, "name", str(service.pairing))


def _import_pyatv():
    try:
        import pyatv
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 pyatv。请执行 `python -m pip install -e .` 安装依赖。"
        ) from exc

    return pyatv


def _pyatv_protocol_enum():
    try:
        from pyatv.const import Protocol
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 pyatv。请执行 `python -m pip install -e .` 安装依赖。"
        ) from exc

    return Protocol


def _pyatv_file_storage():
    try:
        from pyatv.storage.file_storage import FileStorage
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 pyatv。请执行 `python -m pip install -e .` 安装依赖。"
        ) from exc

    return FileStorage

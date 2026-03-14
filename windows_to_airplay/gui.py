from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from .capture import LoopbackDevice
from .gui_backend import AudioStreamBackend, DiscoveredAudioDevice


@dataclass(frozen=True, slots=True)
class LatencyPreset:
    label: str
    frames_per_buffer: int
    queue_chunks: int


LATENCY_PRESETS = [
    LatencyPreset("Balanced (512 / 32)", 512, 32),
    LatencyPreset("Ultra low latency (128 / 32)", 128, 32),
    LatencyPreset("Stable (1024 / 32)", 1024, 32),
]
BITRATE_CHOICES = ("192k", "256k", "320k")


@dataclass(frozen=True, slots=True)
class GuiState:
    host_filter: str = ""
    bitrate: str = "192k"
    latency_mode: str = LATENCY_PRESETS[0].label
    audio_device_index: int | None = None
    selected_addresses: tuple[str, ...] = ()


@dataclass(slots=True)
class DeviceRow:
    device: DiscoveredAudioDevice
    frame: ttk.Frame
    selected_var: tk.BooleanVar
    selected_button: ttk.Checkbutton
    status_var: tk.StringVar
    status_label: ttk.Label
    info_label: ttk.Label
    volume_var: tk.DoubleVar
    volume_scale: ttk.Scale
    volume_value_var: tk.StringVar
    volume_value_label: ttk.Label
    volume_after_id: str | None = None


def _gui_state_path(base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        root = Path(base_dir)
    else:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "windows-to-airplay" / "gui-state.json"


def load_gui_state(path: Path | None = None) -> GuiState:
    state_path = path or _gui_state_path()

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return GuiState()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return GuiState()

    bitrate = raw.get("bitrate", GuiState.bitrate)
    if bitrate not in BITRATE_CHOICES:
        bitrate = GuiState.bitrate

    latency_mode = raw.get("latency_mode", GuiState.latency_mode)
    valid_latency_labels = {preset.label for preset in LATENCY_PRESETS}
    if latency_mode not in valid_latency_labels:
        latency_mode = GuiState.latency_mode

    audio_device_index = raw.get("audio_device_index")
    if not isinstance(audio_device_index, int):
        audio_device_index = None

    selected_addresses_raw = raw.get("selected_addresses", [])
    if not isinstance(selected_addresses_raw, list):
        selected_addresses_raw = []
    selected_addresses = tuple(
        address for address in selected_addresses_raw if isinstance(address, str)
    )

    host_filter = raw.get("host_filter", "")
    if not isinstance(host_filter, str):
        host_filter = ""

    return GuiState(
        host_filter=host_filter,
        bitrate=bitrate,
        latency_mode=latency_mode,
        audio_device_index=audio_device_index,
        selected_addresses=selected_addresses,
    )


def save_gui_state(state: GuiState, path: Path | None = None) -> None:
    state_path = path or _gui_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host_filter": state.host_filter,
        "bitrate": state.bitrate,
        "latency_mode": state.latency_mode,
        "audio_device_index": state.audio_device_index,
        "selected_addresses": list(state.selected_addresses),
    }
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class BackendWorker:
    def __init__(self, post_event) -> None:
        self._post_event = post_event
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._backend: AudioStreamBackend | None = None
        self._closed = False
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._backend = AudioStreamBackend(self._emit)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._backend.shutdown())
            self._loop.close()

    def _emit(self, kind: str, **payload: Any) -> None:
        self._post_event({"kind": kind, **payload})

    def submit(self, action: str, coro: Any) -> None:
        if self._closed or self._loop is None:
            return

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def done_callback(done_future) -> None:
            try:
                result = done_future.result()
                self._post_event({"kind": "action_result", "action": action, "result": result})
            except Exception as exc:
                self._post_event({"kind": "action_error", "action": action, "error": str(exc)})

        future.add_done_callback(done_callback)

    def discover_devices(self, host: str | None) -> None:
        assert self._backend is not None
        self.submit("discover_devices", self._backend.discover_devices(host=host or None))

    def list_audio_devices(self) -> None:
        assert self._backend is not None
        self.submit("list_audio_devices", self._backend.list_audio_devices())

    def start_stream(
        self,
        addresses: list[str],
        *,
        device_index: int | None,
        bitrate: str,
        frames_per_buffer: int,
        queue_chunks: int,
    ) -> None:
        assert self._backend is not None
        self.submit(
            "start_stream",
            self._backend.start_stream(
                addresses,
                device_index=device_index,
                bitrate=bitrate,
                frames_per_buffer=frames_per_buffer,
                queue_chunks=queue_chunks,
            ),
        )

    def stop_stream(self) -> None:
        assert self._backend is not None
        self.submit("stop_stream", self._backend.stop_stream())

    def set_volume(self, address: str, level: float) -> None:
        assert self._backend is not None
        self.submit("set_volume", self._backend.set_volume(address, level))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._backend is not None and self._loop is not None:
            future = asyncio.run_coroutine_threadsafe(self._backend.shutdown(), self._loop)
            try:
                future.result(timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)

        self._thread.join(timeout=5)


class AirPlayGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Windows to AirPlay")
        self.geometry("1120x760")
        self.minsize(980, 620)

        self._saved_state = load_gui_state()
        self._event_queue: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._worker = BackendWorker(self._event_queue.put)
        self._device_rows: dict[str, DeviceRow] = {}
        self._devices_by_address: dict[str, DiscoveredAudioDevice] = {}
        self._audio_devices: list[LoopbackDevice] = []
        self._device_label_to_index: dict[str, int | None] = {}
        self._latency_presets = {preset.label: preset for preset in LATENCY_PRESETS}
        self._saved_selected_addresses = set(self._saved_state.selected_addresses)
        self._state_ready = False
        self._selected_audio_device = tk.StringVar(value="Default output")
        self._selected_latency_mode = tk.StringVar(value=self._saved_state.latency_mode)
        self._bitrate = tk.StringVar(value=self._saved_state.bitrate)
        self._host_filter = tk.StringVar(value=self._saved_state.host_filter)
        self._status_var = tk.StringVar(value="Ready")
        self._stream_running = False

        self._build_ui()
        self._bind_state_tracking()
        self._state_ready = True
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)
        self.after(50, self._initial_refresh)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        root.rowconfigure(3, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, sticky="ew")
        for index in range(9):
            toolbar.columnconfigure(index, weight=0)
        toolbar.columnconfigure(8, weight=1)

        ttk.Label(toolbar, text="Host filter").grid(row=0, column=0, sticky="w")
        ttk.Entry(toolbar, textvariable=self._host_filter, width=18).grid(row=0, column=1, padx=(8, 16), sticky="w")
        self._discover_button = ttk.Button(toolbar, text="Discover devices", command=self._discover_devices)
        self._discover_button.grid(row=0, column=2, padx=(0, 8))
        self._start_button = ttk.Button(toolbar, text="Start stream", command=self._start_stream)
        self._start_button.grid(row=0, column=3, padx=(0, 8))
        self._stop_button = ttk.Button(toolbar, text="Stop stream", command=self._stop_stream, state=tk.DISABLED)
        self._stop_button.grid(row=0, column=4)

        settings = ttk.LabelFrame(root, text="Stream settings", padding=12)
        settings.grid(row=1, column=0, sticky="ew", pady=(12, 12))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        ttk.Label(settings, text="Audio device").grid(row=0, column=0, sticky="w")
        self._audio_device_combo = ttk.Combobox(
            settings,
            textvariable=self._selected_audio_device,
            state="readonly",
            width=46,
        )
        self._audio_device_combo.grid(row=0, column=1, sticky="ew", padx=(8, 20))

        ttk.Label(settings, text="Bitrate").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            settings,
            textvariable=self._bitrate,
            state="readonly",
            values=BITRATE_CHOICES,
            width=12,
        ).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(settings, text="Latency mode").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Combobox(
            settings,
            textvariable=self._selected_latency_mode,
            state="readonly",
            values=list(self._latency_presets.keys()),
        ).grid(row=1, column=1, sticky="ew", padx=(8, 20), pady=(12, 0))

        devices_frame = ttk.LabelFrame(root, text="Discovered RAOP devices", padding=0)
        devices_frame.grid(row=2, column=0, sticky="nsew")
        devices_frame.rowconfigure(0, weight=1)
        devices_frame.columnconfigure(0, weight=1)

        self._devices_canvas = tk.Canvas(devices_frame, highlightthickness=0)
        self._devices_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(devices_frame, orient=tk.VERTICAL, command=self._devices_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._devices_canvas.configure(yscrollcommand=scrollbar.set)

        self._devices_inner = ttk.Frame(self._devices_canvas, padding=12)
        self._devices_window = self._devices_canvas.create_window((0, 0), window=self._devices_inner, anchor="nw")
        self._devices_inner.bind("<Configure>", self._on_devices_frame_configure)
        self._devices_canvas.bind("<Configure>", self._on_devices_canvas_configure)

        log_frame = ttk.LabelFrame(root, text="Status", padding=12)
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log_text = tk.Text(log_frame, height=10, wrap="word", state=tk.DISABLED)
        self._log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self._log_text.configure(yscrollcommand=log_scrollbar.set)

        status_bar = ttk.Label(root, textvariable=self._status_var, anchor="w")
        status_bar.grid(row=4, column=0, sticky="ew", pady=(10, 0))

    def _initial_refresh(self) -> None:
        self._worker.list_audio_devices()
        self._discover_devices()

    def _discover_devices(self) -> None:
        self._status_var.set("Discovering devices...")
        self._discover_button.configure(state=tk.DISABLED)
        self._worker.discover_devices(self._host_filter.get().strip())

    def _start_stream(self) -> None:
        addresses = [address for address, row in self._device_rows.items() if row.selected_var.get()]
        if not addresses:
            messagebox.showwarning("No device selected", "Select at least one device before starting the stream.")
            return

        self._persist_gui_state()
        self._set_stream_controls(starting=True)
        self._status_var.set("Starting stream...")
        self._worker.start_stream(
            addresses,
            device_index=self._selected_audio_device_index(),
            bitrate=self._bitrate.get().strip() or "192k",
            frames_per_buffer=self._selected_latency_preset().frames_per_buffer,
            queue_chunks=self._selected_latency_preset().queue_chunks,
        )

    def _stop_stream(self) -> None:
        self._status_var.set("Stopping stream...")
        self._worker.stop_stream()
        self._stop_button.configure(state=tk.DISABLED)

    def _schedule_volume_change(self, address: str, value: str) -> None:
        row = self._device_rows[address]
        row.volume_value_var.set(f"{float(value):.0f}%")
        if row.volume_scale.instate(("disabled",)):
            return

        if row.volume_after_id is not None:
            self.after_cancel(row.volume_after_id)
        row.volume_after_id = self.after(
            180,
            lambda: self._send_volume_change(address, row.volume_var.get()),
        )

    def _send_volume_change(self, address: str, value: float) -> None:
        row = self._device_rows.get(address)
        if row is None:
            return
        row.volume_after_id = None
        self._worker.set_volume(address, float(value))

    def _selected_audio_device_index(self) -> int | None:
        return self._device_label_to_index.get(self._selected_audio_device.get(), None)

    def _selected_latency_preset(self) -> LatencyPreset:
        return self._latency_presets.get(
            self._selected_latency_mode.get(),
            LATENCY_PRESETS[0],
        )

    def _bind_state_tracking(self) -> None:
        self._host_filter.trace_add("write", self._persist_gui_state_trace)
        self._bitrate.trace_add("write", self._persist_gui_state_trace)
        self._selected_latency_mode.trace_add("write", self._persist_gui_state_trace)
        self._selected_audio_device.trace_add("write", self._persist_gui_state_trace)

    def _persist_gui_state_trace(self, *_args) -> None:
        self._persist_gui_state()

    def _persist_gui_state(self) -> None:
        if not self._state_ready:
            return

        selected_addresses = tuple(
            sorted(
                address
                for address, row in self._device_rows.items()
                if row.selected_var.get()
            )
        )
        state = GuiState(
            host_filter=self._host_filter.get().strip(),
            bitrate=self._bitrate.get() if self._bitrate.get() in BITRATE_CHOICES else GuiState.bitrate,
            latency_mode=self._selected_latency_preset().label,
            audio_device_index=self._selected_audio_device_index(),
            selected_addresses=selected_addresses,
        )
        save_gui_state(state)
        self._saved_state = state
        self._saved_selected_addresses = set(state.selected_addresses)

    def _refresh_audio_device_combo(self, devices: list[LoopbackDevice]) -> None:
        self._audio_devices = devices
        values = ["Default output"]
        label_to_index: dict[str, int | None] = {"Default output": None}

        for device in devices:
            prefix = "* " if device.is_default else "  "
            label = f"{prefix}[{device.index}] {device.name} - {device.sample_rate} Hz"
            values.append(label)
            label_to_index[label] = device.index

        self._device_label_to_index = label_to_index
        self._audio_device_combo.configure(values=values)
        saved_index = self._saved_state.audio_device_index
        if saved_index is not None:
            matching_label = next(
                (label for label, index in label_to_index.items() if index == saved_index),
                None,
            )
            if matching_label is not None:
                self._selected_audio_device.set(matching_label)
                self._persist_gui_state()
                return

        if self._selected_audio_device.get() not in values:
            self._selected_audio_device.set("Default output")
        self._persist_gui_state()

    def _replace_device_rows(self, devices: list[DiscoveredAudioDevice]) -> None:
        selected_addresses = {
            address for address, row in self._device_rows.items() if row.selected_var.get()
        } or set(self._saved_selected_addresses)

        for row in self._device_rows.values():
            row.frame.destroy()

        self._devices_by_address = {device.address: device for device in devices}
        self._device_rows = {}

        for row_index, device in enumerate(devices):
            row_frame = ttk.Frame(self._devices_inner, padding=(0, 0, 0, 10))
            row_frame.grid(row=row_index, column=0, sticky="ew")
            row_frame.columnconfigure(1, weight=1)
            row_frame.columnconfigure(2, weight=1)

            selected_var = tk.BooleanVar(value=device.address in selected_addresses)
            selected_var.trace_add("write", self._persist_gui_state_trace)
            selected_button = ttk.Checkbutton(row_frame, variable=selected_var)
            selected_button.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 10))

            status_var = tk.StringVar(value=f"Pairing: {device.raop_pairing}")
            ttk.Label(row_frame, text=device.name, font=("Segoe UI", 10, "bold")).grid(
                row=0,
                column=1,
                sticky="w",
            )
            info_parts = [device.address]
            if device.identifier:
                info_parts.append(device.identifier)
            if device.model:
                info_parts.append(device.model)
            info_parts.append(f"pairing={device.raop_pairing}")
            if device.has_credentials:
                info_parts.append("credentials=yes")
            if device.requires_password:
                info_parts.append("password=yes")
            info_label = ttk.Label(row_frame, text=" | ".join(info_parts))
            info_label.grid(row=1, column=1, sticky="w")

            status_label = ttk.Label(row_frame, textvariable=status_var)
            status_label.grid(row=0, column=2, sticky="e", padx=(12, 0))

            slider_frame = ttk.Frame(row_frame)
            slider_frame.grid(row=1, column=2, sticky="e", padx=(12, 0))
            ttk.Label(slider_frame, text="Volume").grid(row=0, column=0, padx=(0, 8))
            volume_var = tk.DoubleVar(value=50.0)
            volume_scale = ttk.Scale(
                slider_frame,
                from_=0,
                to=100,
                orient=tk.HORIZONTAL,
                variable=volume_var,
                command=lambda value, address=device.address: self._schedule_volume_change(address, value),
                length=180,
            )
            volume_scale.grid(row=0, column=1, sticky="ew")
            volume_scale.state(["disabled"])
            volume_value_var = tk.StringVar(value="50%")
            volume_value_label = ttk.Label(slider_frame, textvariable=volume_value_var, width=5, anchor="e")
            volume_value_label.grid(row=0, column=2, padx=(8, 0))

            self._device_rows[device.address] = DeviceRow(
                device=device,
                frame=row_frame,
                selected_var=selected_var,
                selected_button=selected_button,
                status_var=status_var,
                status_label=status_label,
                info_label=info_label,
                volume_var=volume_var,
                volume_scale=volume_scale,
                volume_value_var=volume_value_var,
                volume_value_label=volume_value_label,
            )

        self._status_var.set(f"Found {len(devices)} device(s)")
        self._persist_gui_state()

    def _set_stream_controls(self, *, starting: bool = False) -> None:
        if starting:
            self._start_button.configure(state=tk.DISABLED)
            self._stop_button.configure(state=tk.DISABLED)
            self._discover_button.configure(state=tk.DISABLED)
            return

        if self._stream_running:
            self._start_button.configure(state=tk.DISABLED)
            self._stop_button.configure(state=tk.NORMAL)
            self._discover_button.configure(state=tk.DISABLED)
        else:
            self._start_button.configure(state=tk.NORMAL)
            self._stop_button.configure(state=tk.DISABLED)
            self._discover_button.configure(state=tk.NORMAL)

    def _set_row_connected(self, address: str, connected: bool, detail: str | None = None) -> None:
        row = self._device_rows.get(address)
        if row is None:
            return

        if connected:
            row.status_var.set(detail or "Connected")
            row.volume_scale.state(["!disabled"])
        else:
            row.status_var.set(detail or "Disconnected")
            row.volume_scale.state(["disabled"])

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event["kind"]

        if kind == "action_result":
            self._handle_action_result(event["action"], event.get("result"))
            return

        if kind == "action_error":
            self._handle_action_error(event["action"], event["error"])
            return

        if kind == "log":
            self._append_log(event["message"])
            return

        if kind == "error":
            self._append_log(f"Error: {event['message']}")
            self._status_var.set(event["message"])
            return

        if kind == "stream_started":
            self._stream_running = True
            self._status_var.set("Streaming")
            self._append_log(
                f"Stream started: {event['detail']} | latency={self._selected_latency_preset().label}"
            )
            self._set_stream_controls()
            for row in self._device_rows.values():
                row.selected_button.configure(state=tk.DISABLED)
            return

        if kind == "stream_stopped":
            self._stream_running = False
            self._status_var.set("Stopped")
            self._append_log("Stream stopped")
            self._set_stream_controls()
            for row in self._device_rows.values():
                row.selected_button.configure(state=tk.NORMAL)
                row.volume_scale.state(["disabled"])
            return

        if kind == "device_status":
            self._handle_device_status(event)
            return

        if kind == "volume":
            self._handle_volume_event(event)
            return

    def _handle_action_result(self, action: str, result: Any) -> None:
        if action == "discover_devices":
            self._replace_device_rows(result or [])
            self._discover_button.configure(state=tk.NORMAL if not self._stream_running else tk.DISABLED)
            return

        if action == "list_audio_devices":
            self._refresh_audio_device_combo(result or [])
            return

        if action == "start_stream":
            self._status_var.set("Streaming")
            return

        if action == "stop_stream":
            self._status_var.set("Stopped")
            return

    def _handle_action_error(self, action: str, error: str) -> None:
        self._append_log(f"{action} failed: {error}")
        self._status_var.set(error)
        if action in {"discover_devices", "start_stream", "stop_stream"}:
            self._set_stream_controls()
        if action == "start_stream":
            for row in self._device_rows.values():
                row.selected_button.configure(state=tk.NORMAL)

    def _handle_device_status(self, event: dict[str, Any]) -> None:
        address = event["address"]
        status = event["status"]
        detail = event.get("detail")

        mapping = {
            "connected": "Connected",
            "disconnected": "Disconnected",
            "connection_closed": "Connection closed",
            "connection_lost": "Connection lost",
            "outputs_updated": "Outputs updated",
        }
        text = mapping.get(status, status)
        if detail and status != "connected":
            text = f"{text}: {detail}"
        self._set_row_connected(address, status == "connected", text)
        self._append_log(f"{address}: {text}")

    def _handle_volume_event(self, event: dict[str, Any]) -> None:
        address = event["address"]
        row = self._device_rows.get(address)
        if row is None:
            return

        level = float(event["new_level"])
        row.volume_var.set(level)
        row.volume_value_var.set(f"{level:.0f}%")

    def _on_devices_frame_configure(self, _event) -> None:
        self._devices_canvas.configure(scrollregion=self._devices_canvas.bbox("all"))

    def _on_devices_canvas_configure(self, event) -> None:
        self._devices_canvas.itemconfigure(self._devices_window, width=event.width)

    def _on_close(self) -> None:
        if self._stream_running:
            if not messagebox.askyesno("Quit", "A stream is still running. Stop it and exit?"):
                return
        self._status_var.set("Shutting down...")
        self.update_idletasks()
        self._persist_gui_state()
        self._worker.close()
        self.destroy()


def main() -> int:
    app = AirPlayGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

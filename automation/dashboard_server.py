from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

# Add repo root to path so pipeline/data packages are importable
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


DEFAULT_HOST = os.environ.get("HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("PORT", "8765"))
DAY_OPTIONS = [
	"monday",
	"tuesday",
	"wednesday",
	"thursday",
	"friday",
	"saturday",
	"sunday",
]
DAY_INDEX = {day_name: index for index, day_name in enumerate(DAY_OPTIONS)}
SCHEDULE_POLL_SECONDS = 10
SCHEDULE_TRIGGER_WINDOW_SECONDS = 75


def workspace_root() -> Path:
	# In a PyInstaller-frozen build, __file__ resolves inside the bundle, not
	# somewhere config.json/data/ can live persistently — use the folder that
	# holds the running .exe instead (where the build script places them).
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parents[1]


def isoformat_or_none(value: datetime | None) -> str | None:
	if value is None:
		return None
	return value.astimezone().isoformat(timespec="seconds")


def split_odin_summary(summary_text: str) -> list[str]:
	if not summary_text:
		return []
	cleaned = [line.strip() for line in summary_text.splitlines() if line.strip()]
	if cleaned:
		return cleaned
	return [summary_text.strip()]


def normalize_artifact_relative_path(value: Any) -> str:
	raw_value = str(value or "").strip().replace("\\", "/")
	if not raw_value:
		raise ValueError("Artifact path is unavailable for deletion.")
	pure_path = PurePosixPath(raw_value)
	parts = [part for part in pure_path.parts if part not in {"", "."}]
	if not parts or any(part == ".." for part in parts):
		raise ValueError("Artifact path is invalid.")
	return "/".join(parts)


class AutomationManager:
	def __init__(self, root: Path) -> None:
		self.root = root
		self.dashboard_server_path = self.root / "automation" / "dashboard_server.py"
		# A packaged build ships the runner/resolver as their own sibling .exe
		# (see build/ script) instead of .py files run under a python.exe.
		if getattr(sys, "frozen", False):
			self.tools_path = self.root / "RedFlagged-Runner.exe"
			self.incident_resolved_path = self.root / "RedFlagged-IncidentResolver.exe"
		else:
			self.tools_path = self.root / "pipeline" / "runner.py"
			self.incident_resolved_path = self.root / "pipeline" / "incident_resolver.py"
		self.schedule_path = self.root / "automation" / "system_config_schedule.json"
		self.artifacts_root = self.root / "artifacts"
		self.artifact_inventory_path = self.root / "automation" / "artifacts_inventory.json"
		self.artifact_cache_path = self.root / "js" / "artifactData.js"
		self.lock = threading.RLock()
		self.server_started_at = datetime.now().astimezone()
		self.process: subprocess.Popen[str] | None = None
		self.incident_resolve_process: subprocess.Popen[str] | None = None
		self.incident_resolve_process_started_at: datetime | None = None
		self.incident_resolve_process_stopped_at: datetime | None = None
		self.incident_resolve_process_incident_id: str | None = None
		self.incident_resolve_last_exit_code: int | None = None
		self.incident_resolve_last_error = ""
		self.incident_resolve_status_message = "incident_resolved.py has not been started yet."
		self.process_started_at: datetime | None = None
		self.process_stopped_at: datetime | None = None
		self.termination_requested = False
		self.last_exit_code: int | None = None
		self.last_error = ""
		self.status_message = "Automation backend ready."
		self.log_history: deque[str] = deque(maxlen=400)
		self.subscribers: set[queue.Queue[tuple[str, str]]] = set()
		self.schedule = self._load_schedule()
		self.last_schedule_trigger_keys: dict[str, str | None] = {
			"auto_run": None,
			"auto_stop": None,
		}
		self.scheduler_stop_event = threading.Event()
		self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
		self.scheduler_thread.start()

	def _default_schedule(self) -> dict[str, Any]:
		return {
			"auto_run": {
				"enabled": False,
				"frequency": "daily",
				"time": "08:00",
				"day_of_week": "monday",
			},
			"auto_stop": {
				"enabled": False,
				"frequency": "daily",
				"time": "18:00",
				"day_of_week": "monday",
			},
		}

	def _load_schedule(self) -> dict[str, Any]:
		default_schedule = self._default_schedule()
		if not self.schedule_path.is_file():
			return default_schedule
		try:
			with self.schedule_path.open("r", encoding="utf-8") as file_obj:
				payload = json.load(file_obj)
		except (OSError, json.JSONDecodeError):
			return default_schedule
		try:
			return self._normalize_schedule_payload(payload)
		except ValueError:
			return default_schedule

	def _normalize_time(self, value: Any, field_name: str) -> str:
		text = str(value or "").strip()
		parts = text.split(":")
		if len(parts) != 2:
			raise ValueError(f"{field_name} must be in HH:MM format.")
		try:
			hours = int(parts[0])
			minutes = int(parts[1])
		except ValueError as exc:
			raise ValueError(f"{field_name} must be a valid time.") from exc
		if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
			raise ValueError(f"{field_name} must be a valid 24-hour time.")
		return f"{hours:02d}:{minutes:02d}"

	def _normalize_schedule_entry(self, payload: Any, entry_name: str) -> dict[str, Any]:
		default_entry = self._default_schedule()[entry_name]
		entry = payload if isinstance(payload, dict) else {}
		enabled = bool(entry.get("enabled", default_entry["enabled"]))
		frequency = str(entry.get("frequency", default_entry["frequency"]) or default_entry["frequency"]).strip().lower()
		if frequency not in {"daily", "weekdays", "weekly"}:
			raise ValueError(f"{entry_name} frequency is invalid.")
		day_of_week = str(entry.get("day_of_week", default_entry["day_of_week"]) or default_entry["day_of_week"]).strip().lower()
		if day_of_week not in DAY_INDEX:
			raise ValueError(f"{entry_name} day of week is invalid.")
		time_value = self._normalize_time(entry.get("time", default_entry["time"]), f"{entry_name} time")
		return {
			"enabled": enabled,
			"frequency": frequency,
			"time": time_value,
			"day_of_week": day_of_week,
		}

	def _normalize_schedule_payload(self, payload: Any) -> dict[str, Any]:
		data = payload if isinstance(payload, dict) else {}
		return {
			"auto_run": self._normalize_schedule_entry(data.get("auto_run"), "auto_run"),
			"auto_stop": self._normalize_schedule_entry(data.get("auto_stop"), "auto_stop"),
		}

	def _persist_schedule(self) -> None:
		self.schedule_path.parent.mkdir(parents=True, exist_ok=True)
		with self.schedule_path.open("w", encoding="utf-8") as file_obj:
			json.dump(self.schedule, file_obj, indent=2)

	def _is_process_running_locked(self) -> bool:
		return self.process is not None and self.process.poll() is None

	def _has_enabled_schedule_locked(self) -> bool:
		return bool(self.schedule["auto_run"]["enabled"] or self.schedule["auto_stop"]["enabled"])

	def _current_idle_status_locked(self) -> str:
		if self.last_error:
			return "Error"
		if self._has_enabled_schedule_locked():
			return "Scheduled"
		return "Stopped"

	def _system_state_locked(self) -> str:
		if self.termination_requested:
			return "Stopping"
		if self._is_process_running_locked():
			return "Running"
		if self.last_error:
			return "Stopped with error"
		if self._has_enabled_schedule_locked():
			return "Idle"
		return "Stopped"

	def _broadcast_event(self, event_name: str, payload: str) -> None:
		with self.lock:
			subscribers = list(self.subscribers)
		for subscriber in subscribers:
			try:
				subscriber.put_nowait((event_name, payload))
			except queue.Full:
				continue

	def _broadcast_log_line(self, line: str) -> None:
		cleaned = line.rstrip("\r\n")
		if not cleaned:
			return
		with self.lock:
			self.log_history.append(cleaned)
		self._broadcast_event("message", cleaned)

	def _relative_script_path(self, path: Path) -> str:
		try:
			return path.relative_to(self.root).as_posix()
		except ValueError:
			return path.as_posix()

	def _monitor_status_locked(
		self,
		*,
		is_running: bool,
		last_error: str,
		last_exit_code: int | None,
		has_started: bool,
		pending_when_idle: bool = False,
	) -> str:
		if is_running:
			return "Inprogress"
		if last_error or (last_exit_code is not None and last_exit_code != 0):
			return "Error"
		if pending_when_idle or not has_started:
			return "Pending"
		return "Terminated"

	def _tools_monitor_message_locked(self, is_running: bool, has_started: bool) -> str:
		if is_running:
			return self.status_message or "tools.py is running."
		if self.last_error:
			return self.last_error
		if self._has_enabled_schedule_locked():
			return "Waiting for the next scheduled tools.py run."
		if not has_started:
			return "tools.py has not been started yet."
		if self.last_exit_code == 0:
			return "tools.py completed successfully."
		return "tools.py is not running."

	def _incident_resolve_monitor_message_locked(self, is_running: bool, has_started: bool) -> str:
		if is_running:
			return self.incident_resolve_status_message or "incident_resolved.py is running."
		if self.incident_resolve_last_error:
			return self.incident_resolve_last_error
		if not has_started:
			return "incident_resolved.py has not been started yet."
		if self.incident_resolve_last_exit_code == 0:
			return self.incident_resolve_status_message or "incident_resolved.py completed successfully."
		return self.incident_resolve_status_message or "incident_resolved.py is not running."

	def _monitoring_entries_locked(self) -> list[dict[str, Any]]:
		tools_is_running = self._is_process_running_locked()
		tools_has_started = any(
			value is not None for value in (self.process_started_at, self.process_stopped_at, self.last_exit_code)
		)
		incident_is_running = self.incident_resolve_process is not None and self.incident_resolve_process.poll() is None
		incident_has_started = any(
			value is not None
			for value in (
				self.incident_resolve_process_started_at,
				self.incident_resolve_process_stopped_at,
				self.incident_resolve_last_exit_code,
			)
		) or bool(self.incident_resolve_process_incident_id)

		return [
			{
				"file": self._relative_script_path(self.dashboard_server_path),
				"status": "Inprogress",
				"pid": os.getpid(),
				"started_at": isoformat_or_none(self.server_started_at),
				"stopped_at": None,
				"last_exit_code": None,
				"message": "Dashboard backend server is responding.",
			},
			{
				"file": self._relative_script_path(self.tools_path),
				"status": self._monitor_status_locked(
					is_running=tools_is_running,
					last_error=self.last_error,
					last_exit_code=self.last_exit_code,
					has_started=tools_has_started,
					pending_when_idle=self._has_enabled_schedule_locked(),
				),
				"pid": self.process.pid if tools_is_running and self.process else None,
				"started_at": isoformat_or_none(self.process_started_at),
				"stopped_at": isoformat_or_none(self.process_stopped_at),
				"last_exit_code": self.last_exit_code,
				"message": self._tools_monitor_message_locked(tools_is_running, tools_has_started),
			},
			{
				"file": self._relative_script_path(self.incident_resolved_path),
				"status": self._monitor_status_locked(
					is_running=incident_is_running,
					last_error=self.incident_resolve_last_error,
					last_exit_code=self.incident_resolve_last_exit_code,
					has_started=incident_has_started,
				),
				"pid": self.incident_resolve_process.pid if incident_is_running and self.incident_resolve_process else None,
				"started_at": isoformat_or_none(self.incident_resolve_process_started_at),
				"stopped_at": isoformat_or_none(self.incident_resolve_process_stopped_at),
				"last_exit_code": self.incident_resolve_last_exit_code,
				"message": self._incident_resolve_monitor_message_locked(incident_is_running, incident_has_started),
				"incident_id": self.incident_resolve_process_incident_id,
			},
		]

	def _broadcast_notification(self, title: str, text: str, status: str) -> None:
		payload = json.dumps(
			{
				"title": title,
				"text": text,
				"status": status,
				"source": "System Configuration",
			}
		)
		self._broadcast_event("notification", payload)

	def _broadcast_status(self) -> None:
		payload = json.dumps(self.get_status_payload())
		self._broadcast_event("status", payload)

	def add_subscriber(self) -> tuple[queue.Queue[tuple[str, str]], list[str], dict[str, Any]]:
		subscriber: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=200)
		with self.lock:
			self.subscribers.add(subscriber)
			backlog = list(self.log_history)
			status_payload = self.get_status_payload()
		return subscriber, backlog, status_payload

	def remove_subscriber(self, subscriber: queue.Queue[tuple[str, str]]) -> None:
		with self.lock:
			self.subscribers.discard(subscriber)

	def _process_command(self) -> list[str]:
		if getattr(sys, "frozen", False):
			return [str(self.tools_path)]
		return [sys.executable, "-u", str(self.tools_path)]

	def _incident_resolve_command(
		self, incident_id: str, status: str, classification: str, determination: str, comment: str,
		assigned_to: str = "", snow_tickets: str = "", resolution_code: str = "", caller_email: str = "",
	) -> list[str]:
		if getattr(sys, "frozen", False):
			command = [str(self.incident_resolved_path), "--incident-id", incident_id]
		else:
			command = [
				sys.executable,
				"-u",
				str(self.incident_resolved_path),
				"--incident-id",
				incident_id,
			]
		if status:
			command.extend(["--status", status])
		if classification:
			command.extend(["--classification", classification])
		if determination:
			command.extend(["--determination", determination])
		if comment:
			command.extend(["--comment", comment])
		if assigned_to:
			command.extend(["--assigned-to", assigned_to])
		if snow_tickets:
			command.extend(["--snow-tickets", snow_tickets])
		if resolution_code:
			command.extend(["--resolution-code", resolution_code])
		if caller_email:
			command.extend(["--caller-email", caller_email])
		return command

	def start_process(self, source: str = "manual") -> tuple[bool, dict[str, Any]]:
		with self.lock:
			if not self.tools_path.is_file():
				self.last_error = f"tools.py not found: {self.tools_path}"
				self.status_message = self.last_error
				payload = self.get_status_payload()
				self._broadcast_status()
				return False, payload
			if self._is_process_running_locked():
				payload = self.get_status_payload()
				payload["message"] = "tools.py is already running."
				return False, payload

			environment = os.environ.copy()
			environment["PYTHONUNBUFFERED"] = "1"
			creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
			self.last_error = ""
			self.last_exit_code = None
			self.termination_requested = False
			self.process_started_at = datetime.now().astimezone()
			self.process_stopped_at = None
			self.status_message = f"tools.py started via {source}."
			self.process = subprocess.Popen(
				self._process_command(),
				cwd=str(self.root),
				stdout=subprocess.PIPE,
				stderr=subprocess.STDOUT,
				stdin=subprocess.DEVNULL,
				text=True,
				encoding="utf-8",
				errors="replace",
				bufsize=1,
				env=environment,
				creationflags=creation_flags,
			)
			process = self.process

		threading.Thread(target=self._consume_process_output, args=(process,), daemon=True).start()
		threading.Thread(target=self._wait_for_process_exit, args=(process,), daemon=True).start()
		self._broadcast_notification("Automation Started", self.status_message, "Success")
		self._broadcast_status()
		return True, self.get_status_payload()

	def _consume_process_output(self, process: subprocess.Popen[str]) -> None:
		assert process.stdout is not None
		try:
			for line in iter(process.stdout.readline, ""):
				self._broadcast_log_line(line)
		except (OSError, ValueError):
			return

	def start_incident_resolve_process(
		self,
		incident_id: Any,
		*,
		source: str = "resolve modal",
		status: Any = "Resolved",
		classification: Any = "",
		determination: Any = "",
		comment: Any = "",
		assigned_to: Any = "",
		snow_tickets: Any = "",
		resolution_code: Any = "",
		caller_email: Any = "",
	) -> tuple[bool, dict[str, Any]]:
		normalized_incident_id = str(incident_id or "").strip()
		if not normalized_incident_id:
			raise ValueError("Incident ID is required.")
		normalized_status = str(status or "Resolved").strip() or "Resolved"
		normalized_classification = str(classification or "").strip()
		normalized_determination = str(determination or "").strip()
		normalized_comment = str(comment or "").strip()
		normalized_assigned_to = str(assigned_to or "").strip()
		normalized_snow_tickets = str(snow_tickets or "").strip()
		normalized_resolution_code = str(resolution_code or "").strip()
		normalized_caller_email = str(caller_email or "").strip()

		with self.lock:
			if not self.incident_resolved_path.is_file():
				return False, {
					"ok": False,
					"status": "missing_script",
					"message": f"incident_resolved.py not found: {self.incident_resolved_path}",
				}
			if self.incident_resolve_process is not None and self.incident_resolve_process.poll() is None:
				active_incident_id = self.incident_resolve_process_incident_id or "unknown incident"
				return False, {
					"ok": False,
					"status": "already_running",
					"message": f"incident_resolved.py is already running for {active_incident_id}.",
					"incident_id": active_incident_id,
					"pid": self.incident_resolve_process.pid,
				}

			environment = os.environ.copy()
			environment["PYTHONUNBUFFERED"] = "1"
			creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
			self.incident_resolve_last_exit_code = None
			self.incident_resolve_last_error = ""
			process = subprocess.Popen(
				self._incident_resolve_command(
					normalized_incident_id,
					normalized_status,
					normalized_classification,
					normalized_determination,
					normalized_comment,
					normalized_assigned_to,
					normalized_snow_tickets,
					normalized_resolution_code,
					normalized_caller_email,
				),
				cwd=str(self.root),
				stdout=subprocess.PIPE,
				stderr=subprocess.STDOUT,
				stdin=subprocess.DEVNULL,
				text=True,
				encoding="utf-8",
				errors="replace",
				bufsize=1,
				env=environment,
				creationflags=creation_flags,
			)
			self.incident_resolve_process = process
			self.incident_resolve_process_started_at = datetime.now().astimezone()
			self.incident_resolve_process_stopped_at = None
			self.incident_resolve_process_incident_id = normalized_incident_id
			self.incident_resolve_status_message = (
				f"incident_resolved.py started for incident {normalized_incident_id} via {source}."
			)
			payload = {
				"ok": True,
				"status": "started",
				"message": f"incident_resolved.py started for incident {normalized_incident_id} via {source}.",
				"incident_id": normalized_incident_id,
				"incident_status": normalized_status,
				"incident_classification": normalized_classification,
				"incident_comment": normalized_comment,
				"pid": process.pid,
				"started_at": isoformat_or_none(self.incident_resolve_process_started_at),
			}

		threading.Thread(
			target=self._consume_incident_resolve_output,
			args=(process, normalized_incident_id),
			daemon=True,
		).start()
		threading.Thread(
			target=self._wait_for_incident_resolve_exit,
			args=(process, normalized_incident_id),
			daemon=True,
		).start()
		self._broadcast_notification(
			"Resolve Automation Started",
			f"incident_resolved.py started for incident {normalized_incident_id}.",
			"Success",
		)
		self._broadcast_status()
		return True, payload

	def _consume_incident_resolve_output(self, process: subprocess.Popen[str], incident_id: str) -> None:
		assert process.stdout is not None
		prefix = f"[Incident Resolve {incident_id}] "
		try:
			for line in iter(process.stdout.readline, ""):
				cleaned = line.rstrip("\r\n")
				if cleaned:
					self._broadcast_log_line(prefix + cleaned)
		except (OSError, ValueError):
			return

	def _wait_for_incident_resolve_exit(self, process: subprocess.Popen[str], incident_id: str) -> None:
		exit_code = process.wait()
		with self.lock:
			self.incident_resolve_last_exit_code = exit_code
			self.incident_resolve_process_stopped_at = datetime.now().astimezone()
			if self.incident_resolve_process is process:
				self.incident_resolve_process = None
			if exit_code == 0:
				self.incident_resolve_last_error = ""
				self.incident_resolve_status_message = (
					f"incident_resolved.py finished for incident {incident_id}."
				)
			else:
				self.incident_resolve_last_error = (
					f"incident_resolved.py exited with code {exit_code} for incident {incident_id}."
				)
				self.incident_resolve_status_message = self.incident_resolve_last_error
		if process.stdout is not None:
			try:
				process.stdout.close()
			except (OSError, ValueError):
				pass
		if exit_code == 0:
			self._broadcast_notification(
				"Resolve Automation Completed",
				f"incident_resolved.py finished for incident {incident_id}.",
				"Success",
			)
			return
		self._broadcast_notification(
			"Resolve Automation Failed",
			f"incident_resolved.py exited with code {exit_code} for incident {incident_id}.",
			"Error",
		)
		self._broadcast_status()

	def _wait_for_process_exit(self, process: subprocess.Popen[str]) -> None:
		exit_code = process.wait()
		with self.lock:
			if self.process is not process:
				return
			self.last_exit_code = exit_code
			self.process = None
			self.process_stopped_at = datetime.now().astimezone()
			if self.termination_requested:
				self.termination_requested = False
				self.last_error = ""
				self.status_message = "tools.py terminated."
			elif exit_code == 0:
				self.last_error = ""
				self.status_message = "tools.py completed successfully."
			else:
				self.last_error = f"tools.py exited with code {exit_code}."
				self.status_message = self.last_error
		if process.stdout is not None:
			try:
				process.stdout.close()
			except (OSError, ValueError):
				pass
		status_payload = self.get_status_payload()
		self._broadcast_event("done", json.dumps(status_payload))
		if exit_code == 0:
			self._broadcast_notification("Automation Stopped", self.status_message, "Success")
		else:
			self._broadcast_notification("Automation Error", self.status_message, "Error")
		self._broadcast_status()

	def terminate_process(self, source: str = "manual") -> tuple[bool, dict[str, Any]]:
		with self.lock:
			if not self._is_process_running_locked() or self.process is None:
				payload = self.get_status_payload()
				payload["message"] = "tools.py is not running."
				return False, payload
			if self.termination_requested:
				payload = self.get_status_payload()
				payload["message"] = "tools.py is already terminating."
				return False, payload
			self.termination_requested = True
			self.status_message = f"Stopping tools.py and browser via {source}."
			process = self.process

		threading.Thread(target=self._terminate_worker, args=(process,), daemon=True).start()
		self._broadcast_notification("Automation Terminating", self.status_message, "Warning")
		self._broadcast_status()
		return True, self.get_status_payload()

	def _request_graceful_shutdown(self, process: subprocess.Popen[str]) -> None:
		if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
			process.send_signal(signal.CTRL_BREAK_EVENT)
			return
		process.terminate()

	def _force_kill_process_tree(self, process: subprocess.Popen[str]) -> None:
		if process.poll() is not None:
			return
		if os.name == "nt":
			subprocess.run(
				["taskkill", "/PID", str(process.pid), "/T", "/F"],
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				check=False,
			)
			return
		process.kill()

	def _terminate_worker(self, process: subprocess.Popen[str]) -> None:
		try:
			self._request_graceful_shutdown(process)
			deadline = time.time() + 15
			while time.time() < deadline:
				if process.poll() is not None:
					return
				time.sleep(0.25)
			self._force_kill_process_tree(process)
			deadline = time.time() + 5
			while time.time() < deadline:
				if process.poll() is not None:
					return
				time.sleep(0.25)
		except OSError as exc:
			with self.lock:
				if self.process is process:
					self.last_error = f"Failed to terminate tools.py: {exc}"
					self.termination_requested = False
					self.status_message = self.last_error
			self._broadcast_notification("Termination Failed", str(exc), "Error")
			self._broadcast_status()

	def save_schedule(self, payload: Any) -> dict[str, Any]:
		normalized = self._normalize_schedule_payload(payload)
		with self.lock:
			self.schedule = normalized
			self._persist_schedule()
			if not self._is_process_running_locked() and not self.last_error:
				self.status_message = (
					"Automation schedule saved."
					if self._has_enabled_schedule_locked()
					else "Automation schedule cleared."
				)
		self._broadcast_notification(
			"Schedule Saved",
			"Automatic run and termination settings were updated.",
			"Success",
		)
		self._broadcast_status()
		return self.get_status_payload()

	def _parse_time_parts(self, value: str) -> tuple[int, int]:
		hour_text, minute_text = value.split(":", 1)
		return int(hour_text), int(minute_text)

	def _next_occurrence_for_entry(self, entry: dict[str, Any], now: datetime | None = None) -> datetime | None:
		if not entry.get("enabled"):
			return None
		now_value = now or datetime.now().astimezone()
		hour, minute = self._parse_time_parts(entry["time"])
		candidate = now_value.replace(hour=hour, minute=minute, second=0, microsecond=0)
		frequency = entry["frequency"]
		if frequency == "daily":
			if candidate <= now_value:
				candidate += timedelta(days=1)
			return candidate
		if frequency == "weekdays":
			while candidate <= now_value or candidate.weekday() >= 5:
				candidate += timedelta(days=1)
			return candidate
		day_index = DAY_INDEX[entry["day_of_week"]]
		days_ahead = (day_index - now_value.weekday()) % 7
		candidate = candidate + timedelta(days=days_ahead)
		if candidate <= now_value:
			candidate += timedelta(days=7)
		return candidate

	def _due_schedule_key(self, entry_name: str, now: datetime) -> tuple[str | None, datetime | None]:
		entry = self.schedule[entry_name]
		if not entry.get("enabled"):
			return None, None
		hour, minute = self._parse_time_parts(entry["time"])
		candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
		frequency = entry["frequency"]
		if frequency == "weekdays" and now.weekday() >= 5:
			return None, None
		if frequency == "weekly" and now.weekday() != DAY_INDEX[entry["day_of_week"]]:
			return None, None
		delta_seconds = (now - candidate).total_seconds()
		if delta_seconds < 0 or delta_seconds > SCHEDULE_TRIGGER_WINDOW_SECONDS:
			return None, None
		return f"{entry_name}:{candidate.isoformat(timespec='minutes')}", candidate

	def _scheduler_loop(self) -> None:
		while not self.scheduler_stop_event.wait(SCHEDULE_POLL_SECONDS):
			now = datetime.now().astimezone()
			run_key, _ = self._due_schedule_key("auto_run", now)
			stop_key, _ = self._due_schedule_key("auto_stop", now)

			if run_key and self.last_schedule_trigger_keys["auto_run"] != run_key:
				self.last_schedule_trigger_keys["auto_run"] = run_key
				started, _ = self.start_process(source="automatic schedule")
				if not started:
					self._broadcast_notification(
						"Scheduled Run Skipped",
						"Automatic run was due, but tools.py is already active or unavailable.",
						"Warning",
					)

			if stop_key and self.last_schedule_trigger_keys["auto_stop"] != stop_key:
				self.last_schedule_trigger_keys["auto_stop"] = stop_key
				stopped, _ = self.terminate_process(source="automatic schedule")
				if not stopped:
					self._broadcast_notification(
						"Scheduled Termination Skipped",
						"Automatic termination was due, but tools.py is not currently running.",
						"Warning",
					)

	def get_status_payload(self) -> dict[str, Any]:
		with self.lock:
			is_running = self._is_process_running_locked()
			system_state = self._system_state_locked()
			if self.termination_requested:
				status = "Terminating"
			elif is_running:
				status = "Running"
			else:
				status = self._current_idle_status_locked()
			return {
				"ok": True,
				"status": status,
				"system_state": system_state,
				"message": self.status_message,
				"is_running": is_running,
				"termination_requested": self.termination_requested,
				"pid": self.process.pid if is_running and self.process else None,
				"started_at": isoformat_or_none(self.process_started_at),
				"stopped_at": isoformat_or_none(self.process_stopped_at),
				"last_exit_code": self.last_exit_code,
				"last_error": self.last_error,
				"schedule": self.schedule,
				"next_run_at": isoformat_or_none(self._next_occurrence_for_entry(self.schedule["auto_run"])),
				"next_stop_at": isoformat_or_none(self._next_occurrence_for_entry(self.schedule["auto_stop"])),
				"monitoring_updated_at": isoformat_or_none(datetime.now().astimezone()),
				"monitoring": self._monitoring_entries_locked(),
			}

	def build_report_payload(self) -> dict[str, Any]:
		db_info_path = self.root / "automation" / "db_info.txt"
		if not db_info_path.is_file():
			return {"ok": False}
		try:
			with db_info_path.open("r", encoding="utf-8") as file_obj:
				payload = json.load(file_obj)
		except (OSError, json.JSONDecodeError):
			return {"ok": False}

		rows = payload.get("purview") if isinstance(payload, dict) else []
		if not isinstance(rows, list):
			return {"ok": False}

		selected_row = next(
			(
				row
				for row in rows
				if isinstance(row, dict)
				and (
					str(row.get("Incident ID") or "").strip()
					or str(row.get("Alert Name") or "").strip()
				)
			),
			None,
		)
		if not selected_row:
			return {"ok": False}

		location = str(selected_row.get("Location") or "").strip()
		odin_summary = split_odin_summary(str(selected_row.get("Odin Summary") or "").strip())
		return {
			"ok": True,
			"incident_id": str(selected_row.get("Incident ID") or "").strip(),
			"service_now_ticket": str(selected_row.get("ServiceNow Ticket") or "").strip(),
			"service_now_link": str(selected_row.get("ServiceNow Link") or "").strip(),
			"status": str(selected_row.get("Status") or "In Progress").strip() or "In Progress",
			"assignee": str(selected_row.get("Assigned To") or "Security Managed Services").strip() or "Security Managed Services",
			"source": f"Microsoft Purview - {location}" if location else "Microsoft Purview + Sentinel",
			"odin_summary": odin_summary,
		}

	def _artifact_inventory_payload(self) -> dict[str, Any]:
		default_payload = {"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "files": []}
		if not self.artifact_inventory_path.is_file():
			return default_payload
		try:
			with self.artifact_inventory_path.open("r", encoding="utf-8") as file_obj:
				payload = json.load(file_obj)
		except (OSError, json.JSONDecodeError):
			return default_payload
		files = payload.get("files") if isinstance(payload, dict) else []
		return {
			"updated_at": str(payload.get("updated_at") or default_payload["updated_at"]).strip(),
			"files": files if isinstance(files, list) else [],
		}

	def _persist_artifact_inventory_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
		normalized_payload = {
			"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
			"files": payload.get("files") if isinstance(payload.get("files"), list) else [],
		}
		self.artifact_inventory_path.parent.mkdir(parents=True, exist_ok=True)
		self.artifact_cache_path.parent.mkdir(parents=True, exist_ok=True)
		with self.artifact_inventory_path.open("w", encoding="utf-8") as file_obj:
			json.dump(normalized_payload, file_obj, indent=2)
			file_obj.write("\n")
		with self.artifact_cache_path.open("w", encoding="utf-8") as file_obj:
			file_obj.write(f"window.__REDFLAGGED_ARTIFACTS__ = {json.dumps(normalized_payload, indent=2)};\n")
		return normalized_payload

	def delete_artifact(self, relative_path: Any) -> dict[str, Any]:
		normalized_relative_path = normalize_artifact_relative_path(relative_path)
		artifacts_root = self.artifacts_root.resolve()
		artifact_path = (artifacts_root / Path(*normalized_relative_path.split("/"))).resolve()
		if artifacts_root not in artifact_path.parents and artifact_path != artifacts_root:
			raise ValueError("Artifact path is invalid.")
		if not artifact_path.is_file():
			raise FileNotFoundError(f"Artifact not found: {normalized_relative_path}")

		artifact_path.unlink()
		current_directory = artifact_path.parent
		while current_directory != artifacts_root:
			try:
				current_directory.rmdir()
			except OSError:
				break
			current_directory = current_directory.parent

		# Remove from DB (best-effort — does not fail if DB is unavailable)
		try:
			import data.repository as repository
			repository.delete_artifact(normalized_relative_path)
		except Exception:
			pass

		payload = self._artifact_inventory_payload()
		payload["files"] = [
			entry
			for entry in payload["files"]
			if normalize_artifact_relative_path(entry.get("relative_path")) != normalized_relative_path
		]
		persisted_payload = self._persist_artifact_inventory_payload(payload)
		return {
			"ok": True,
			"message": "Artifact deleted.",
			"relative_path": normalized_relative_path,
			"inventory": persisted_payload,
		}


def parse_request_body(handler: SimpleHTTPRequestHandler) -> Any:
	try:
		content_length = int(handler.headers.get("Content-Length", "0") or "0")
	except (ValueError, TypeError):
		content_length = 0
	if content_length <= 0:
		return {}
	raw_body = handler.rfile.read(content_length)
	if not raw_body:
		return {}
	return json.loads(raw_body.decode("utf-8"))


# ── Flask API proxy base URL ─────────────────────────────────────────────────
# Override via FLASK_API_BASE_URL env var for production / Render deployment.
_FLASK_BASE = os.environ.get("FLASK_API_BASE_URL", "http://127.0.0.1:5051").rstrip("/")
_LOCAL_BASE: str = ""  # set in main() to http://127.0.0.1:<port>
import requests as _req  # used by analysis background thread + proxy routes


def _start_keepalive(base_url: str) -> None:
    """Ping the remote Flask API every 4.5 min to prevent Render free-tier sleep (15 min threshold)."""
    if "127.0.0.1" in base_url or "localhost" in base_url:
        return

    def _ping() -> None:
        while True:
            time.sleep(270)
            try:
                _req.get(f"{base_url}/api/incidents", timeout=10)
            except Exception:
                pass

    t = threading.Thread(target=_ping, daemon=True, name="api-keepalive")
    t.start()
    print(f"[Dashboard] Keep-alive ping started for {base_url} (every 4.5 min).")


def _load_flask_api_creds() -> None:
    """Populate config + client modules with Azure / ServiceNow credentials.

    Local dev: reads api-experiments/config.json (active env).
    Production (Render / no config.json): falls back to environment variables.
    """
    import json as _json
    import config as _c
    import auth.token_manager as _tm2
    from clients import servicenow_client as _snow2

    _env_cfg: dict = {}

    def _read_config_json(p: Path) -> dict:
        try:
            _raw = _json.loads(p.read_text(encoding="utf-8"))
            _active = _raw.get("active_env", "tenant")
            return _raw.get("envs", {}).get(_active, _raw)
        except Exception:
            return {}

    # Try root/config.json first (written by the Settings page)
    _root_cfg = workspace_root() / "config.json"
    if _root_cfg.is_file():
        _env_cfg = _read_config_json(_root_cfg)

    # Fall back to api-experiments/config.json if root had nothing useful
    if not _env_cfg.get("TENANT_ID"):
        _api_cfg_path = Path(__file__).parent.parent.parent / "api-experiments" / "config.json"
        if _api_cfg_path.is_file():
            _env_cfg = _read_config_json(_api_cfg_path)

    # Environment variables always win; config.json used only when env var absent
    _c.TENANT_ID         = os.environ.get("TENANT_ID")         or _env_cfg.get("TENANT_ID",      "")
    _c.CLIENT_ID         = os.environ.get("CLIENT_ID")         or _env_cfg.get("CLIENT_ID",      "")
    _c.CLIENT_SECRET     = os.environ.get("CLIENT_SECRET")     or _env_cfg.get("CLIENT_SECRET",  "")
    _c.SNOW_INSTANCE_URL = (os.environ.get("SNOW_INSTANCE_URL") or os.environ.get("SNOW_INSTANCE")
                            or _env_cfg.get("SNOW_URL") or _env_cfg.get("SNOW_INSTANCE", ""))
    _c.SNOW_USERNAME     = (os.environ.get("SNOW_USERNAME") or os.environ.get("SNOW_USER")
                            or _env_cfg.get("SNOW_USER", ""))
    _c.SNOW_PASSWORD     = (os.environ.get("SNOW_PASSWORD")
                            or _env_cfg.get("SNOW_PASSWORD", ""))

    if _c.TENANT_ID and _c.CLIENT_ID and _c.CLIENT_SECRET:
        _tm2.init(_c.TENANT_ID, _c.CLIENT_ID, _c.CLIENT_SECRET)
    _snow2.init()


def _snow_user_map_path(root: Path) -> Path:
    return root / "data" / "servicenow_user_map.json"


def _load_snow_user_map(root: Path) -> dict:
    p = _snow_user_map_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except Exception:
        return {}


def _save_snow_user_map(root: Path, mapping: dict) -> None:
    p = _snow_user_map_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")


def _snow_identity_for_user(root: Path, current_user) -> tuple[str, str]:
    """Return (snow_email, defender_email) for a RedFlagged session user.

    An admin-configured override (data/servicenow_user_map.json, set from the
    User Management → ServiceNow Determination panel) takes priority — it
    lets an admin point a RedFlagged login at a different ServiceNow account
    (e.g. the two use different email addresses). Falling back from there:
    developer-role sessions, and the "Microsoft CDX" demo-tenant admin identity
    (not a real individual analyst), use the shared RedFlagged Service ServiceNow
    account instead of showing up as themselves on tickets. Defender assignment
    always uses the session's own Microsoft account.
    """
    _override = (_load_snow_user_map(root).get((current_user.email or "").strip().lower(), "") or "").strip()
    if _override:
        return _override, current_user.email

    use_service_account = (
        current_user.role == "developer"
        or (current_user.display_name or "").strip().lower() == "microsoft cdx"
    )
    if use_service_account:
        _svc_file = root / "data" / "system_settings.json"
        try:
            _svc_data = json.loads(_svc_file.read_text(encoding="utf-8")) if _svc_file.is_file() else {}
            snow_email = _svc_data.get("developer_service_account_email", "rf_service@redflagged.local")
        except Exception:
            snow_email = "rf_service@redflagged.local"
    else:
        snow_email = current_user.email
    return snow_email, current_user.email


# ── ServiceNow ticket persistence (per-alert, written by "Add to Database") ───
def _snow_report_db_path(root: Path) -> Path:
    return root / "redflagged.db"


def _snow_report_db_connect(root: Path):
    import sqlite3
    con = sqlite3.connect(str(_snow_report_db_path(root)))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS snow_report_tickets (
            alert_id            TEXT PRIMARY KEY,
            incident_id         TEXT,
            snow_ticket         TEXT,
            snow_sys_id         TEXT,
            snow_parent_ticket  TEXT,
            snow_parent_sys_id  TEXT,
            ticket_type         TEXT,
            added_at            TEXT
        )
        """
    )
    try:
        con.execute("ALTER TABLE snow_report_tickets ADD COLUMN ticket_type TEXT")
    except Exception:
        pass  # column already exists
    con.execute("UPDATE snow_report_tickets SET ticket_type = 'child' WHERE ticket_type IS NULL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS dlp_report (
            alert_id           TEXT PRIMARY KEY,
            incident_id        TEXT,
            category           TEXT,
            severity           TEXT,
            ticket_status      TEXT,
            title              TEXT,
            description        TEXT,
            created_at         TEXT,
            resolved_at        TEXT,
            assigned_to        TEXT,
            dlp_policy         TEXT,
            classification     TEXT,
            determination      TEXT,
            user_name          TEXT,
            user_email         TEXT,
            egress_channel     TEXT,
            resolution_notes   TEXT,
            updated_at         TEXT
        )
        """
    )
    con.commit()
    return con


def _format_snow_datetime(iso_str: str) -> str:
    """'2026-04-02T10:31:00Z' -> 'Apr 2, 2026 10:31:00 AM' (best-effort; raw string on parse failure)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return f"{dt.strftime('%b')} {dt.day}, {dt.year} {dt.strftime('%I:%M:%S %p')}"
    except Exception:
        return str(iso_str)


def _full_name_from_login(login: str) -> str:
    """'grace.pelingon' -> 'Grace Pelingon'. Only used when Entra ID has no surname on file."""
    parts = [p for p in login.replace("_", ".").split(".") if p]
    if len(parts) < 2:
        return ""
    return " ".join(p.capitalize() for p in parts)


def _snow_alert_policy_and_user(alert: dict) -> tuple[str, str]:
    """Extract (policy_title, user_display_name) from a Graph alert object's evidence."""
    policy_title = (alert.get("additionalData") or {}).get("AlertPolicyTitle") or alert.get("title") or alert.get("displayName") or ""
    user_name = ""
    for ev in (alert.get("evidence") or []):
        if "userevidence" in (ev.get("@odata.type") or "").lower():
            ua = ev.get("userAccount") or {}
            display_name = ua.get("displayName") or ""
            # Entra ID's displayName is sometimes just a first name (no surname on file) —
            # fall back to deriving "First Last" from the account's firstname.lastname login.
            if display_name and " " not in display_name.strip():
                login = ua.get("accountName") or (ua.get("userPrincipalName") or "").split("@")[0]
                derived = _full_name_from_login(login)
                user_name = derived or display_name
            else:
                user_name = display_name or ua.get("accountName") or ua.get("userPrincipalName") or ""
            break
    return policy_title, user_name


def _snow_incident_description(policy_title: str, detected_dt_raw: str, severity: str, user: str) -> str:
    policy_title = policy_title or ""
    detected_dt = _format_snow_datetime(detected_dt_raw)
    return (
        f"DLP policy: {policy_title}\n\n"
        f"Details:\n"
        f"Source: Microsoft Defender\n"
        f"Incident Type: Data Loss Prevention (DLP)\n"
        f"Detection Method: Microsoft Purview DLP Policy\n"
        f"Date/Time Detected: {detected_dt}\n"
        f"Policy Triggered: DLP policy ({policy_title})\n"
        f"Severity: {(severity or '').capitalize()}\n"
        f"User Involved: {user or 'Unknown'}"
    )


# ── Microsoft OAuth state tokens (CSRF protection) ────────────────────────────
_oauth_states: dict[str, float] = {}   # state_token → created_at (epoch)
_oauth_states_lock = threading.Lock()

# ── SIT Analysis helpers (module-level, shared across requests) ───────────────
_analysis_jobs: dict[str, dict] = {}
_analysis_lock = threading.Lock()

# Local PII regex patterns — fallback when Graph API metadata has no SIT data
import re as _re
_LOCAL_PII_PATTERNS: list[tuple[str, Any, str]] = [
    ("U.S. Social Security Number (SSN)", _re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "high"),
    ("Credit Card Number",               _re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|6011|3[47]\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{3,4}\b"), "high"),
    ("U.S. Bank Account Number",         _re.compile(r"\b\d{8,17}\b"), "high"),
    ("Date of Birth",                    _re.compile(r"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:19|20)\d{2}\b"), "medium"),
    ("Phone Number",                     _re.compile(r"\b(?:\+1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"), "medium"),
    ("Email Address",                    _re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "low"),
    ("IBAN",                             _re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b"), "medium"),
    ("IP Address",                       _re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "low"),
]

# SIT keyword → canonical name (for title-based inference)
_SIT_TITLE_KEYWORDS: list[tuple[str, str]] = [
    ("credit card",           "Credit Card Number"),
    ("ssn",                   "U.S. Social Security Number (SSN)"),
    ("social security",       "U.S. Social Security Number (SSN)"),
    ("spi",                   "Sensitive Personal Information"),
    ("sensitive personal",    "Sensitive Personal Information"),
    ("pii",                   "Personally Identifiable Information"),
    ("passport",              "Passport Number"),
    ("driver",                "Driver's License Number"),
    ("bank account",          "Bank Account Number"),
    ("iban",                  "IBAN"),
    ("health",                "Health Data"),
    ("medical",               "Medical Records"),
    ("date of birth",         "Date of Birth"),
    ("dob",                   "Date of Birth"),
    ("phone",                 "Phone Number"),
    ("ip address",            "IP Address"),
]


def _run_local_pii_scan(text: str) -> list[tuple[str, str, int]]:
    """Return list of (name, confidence, count) for each local PII pattern found in text."""
    results = []
    for name, pattern, confidence in _LOCAL_PII_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            results.append((name, confidence, len(matches)))
    return results


def _scan_text_pii(text: str) -> list:
    """Scan text with all local PII patterns; return [{name, confidence, count, samples}]."""
    results = []
    for name, pattern, confidence in _LOCAL_PII_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            unique = list(dict.fromkeys(matches))
            results.append({"name": name, "confidence": confidence,
                            "count": len(matches), "samples": unique[:5]})
    return results


def _infer_sits_from_title(title: str) -> list[tuple[str, str]]:
    """Return (name, confidence) pairs inferred from alert title keywords."""
    tl = title.lower()
    found = []
    seen = set()
    for kw, sit_name in _SIT_TITLE_KEYWORDS:
        if kw in tl and sit_name not in seen:
            seen.add(sit_name)
            found.append((sit_name, "medium"))
    return found


def _extract_sit_patterns_for_analysis(additional_data: dict, evidence: list) -> list:
	patterns: list = []
	seen: set = set()

	def add_pat(name: Any, conf: Any, count: Any, source: str) -> None:
		n = str(name or "").strip()
		if not n or n in seen:
			return
		seen.add(n)
		if isinstance(conf, (int, float)):
			c = "high" if conf >= 85 else "medium" if conf >= 75 else "low"
		else:
			c = str(conf or "medium").lower() or "medium"
		patterns.append({"name": n, "confidence": c, "count": int(count or 1), "source": source})

	for key in ("SensitiveInfoTypeSummary", "sensitiveInfoTypeSummary",
				"SensitiveInfoTypeMatches", "sensitiveInfoTypeMatches"):
		raw = additional_data.get(key)
		if not raw:
			continue
		try:
			arr = json.loads(raw) if isinstance(raw, str) else raw
			items = arr if isinstance(arr, list) else (arr.get("SensitiveTypes") or arr.get("sensitiveTypes") or [])
			for sit in items:
				add_pat(
					sit.get("SensitiveType") or sit.get("Name") or sit.get("name") or sit.get("sensitiveInfoType"),
					sit.get("Confidence") or sit.get("confidence") or "medium",
					sit.get("Count") or sit.get("count") or sit.get("UniqueCount") or 1,
					"purview",
				)
			if patterns:
				break
		except Exception:
			pass

	if not patterns:
		raw = additional_data.get("OriginalSensitiveInfoTypeScanResultData")
		if raw:
			try:
				sd = json.loads(raw) if isinstance(raw, str) else raw
				for sit in (sd.get("SensitiveTypes") or sd.get("sensitiveTypes") or []):
					add_pat(sit.get("SensitiveType") or sit.get("Name") or sit.get("name"), "medium", sit.get("Count") or 1, "purview-scan")
			except Exception:
				pass

	if not patterns:
		for ns in (additional_data.get("SensitiveInfoTypeName"), additional_data.get("sensitiveInfoTypeName")):
			if ns:
				for n in ([ns] if not isinstance(ns, list) else ns):
					add_pat(n, "medium", 1, "dlp-alert")

	for ev in evidence:
		t = (ev.get("@odata.type") or "").lower()
		if "dlp" in t:
			for s in (ev.get("sensitiveInfoTypeData") or []):
				add_pat(s.get("sensitiveInfoTypeName") or s.get("SensitiveInfoTypeName") or s.get("name"),
						s.get("confidenceLevel") or s.get("confidence") or "medium",
						s.get("count") or s.get("Count") or 1, "defender-dlp")
		for sit in (ev.get("matchedSensitiveInformationTypes") or []):
			nm = sit if isinstance(sit, str) else (sit.get("name") or sit.get("Name") or "")
			add_pat(nm, (sit.get("confidence") if isinstance(sit, dict) else "medium"),
					(sit.get("count") if isinstance(sit, dict) else 1), "defender")

	return patterns


def _detect_workload_for_analysis(alert: dict) -> str:
	title = (alert.get("title") or "").lower()
	evidence = alert.get("evidence") or []
	if "exchange" in title or "email" in title or "mail" in title:
		return "EMAIL"
	if "sharepoint" in title:
		return "SHAREPOINT"
	if "onedrive" in title or "one drive" in title:
		return "ONEDRIVE"
	if "device" in title or "endpoint" in title:
		return "ENDPOINT"
	for e in evidence:
		t = (e.get("@odata.type") or "").lower()
		if "analyzedmessage" in t:
			return "EMAIL"
		if "sharepoint" in t:
			return "SHAREPOINT"
		if "onedrive" in t:
			return "ONEDRIVE"
	return "ENDPOINT"


def _get_classification_context_for_analysis(evidence_item: dict, sit_name: str) -> str:
	for ca in (evidence_item.get("classificationAttributes") or []):
		msit = ca.get("matchedSensitiveInformationType") or {}
		if msit.get("displayName") == sit_name:
			tmd = ca.get("textMatchData") or {}
			return (tmd.get("surroundingText") or tmd.get("matchedText") or "")[:200]
	return ""


# ── Story helpers ────────────────────────────────────────────────────────────

_ACTIVITY_DESCRIPTIONS: dict[tuple[str, str], tuple[str, str]] = {
	("EMAIL",      "share"):    ("Shared email containing sensitive data externally", "📧"),
	("EMAIL",      "forward"):  ("Forwarded sensitive email to external recipient", "📧"),
	("EMAIL",      "send"):     ("Sent email containing sensitive information", "📧"),
	("EMAIL",      "default"):  ("Sent email flagged for sensitive content", "📧"),
	("SHAREPOINT", "share"):    ("Shared SharePoint file with external parties", "📁"),
	("SHAREPOINT", "upload"):   ("Uploaded file with sensitive data to SharePoint", "📁"),
	("SHAREPOINT", "copy"):     ("Copied file containing sensitive data", "📁"),
	("SHAREPOINT", "move"):     ("Moved file containing sensitive data", "📁"),
	("SHAREPOINT", "download"): ("Downloaded sensitive file from SharePoint", "📁"),
	("SHAREPOINT", "default"):  ("Accessed SharePoint file containing sensitive data", "📁"),
	("ONEDRIVE",   "share"):    ("Shared OneDrive file with external parties", "☁️"),
	("ONEDRIVE",   "upload"):   ("Uploaded file with sensitive data to OneDrive", "☁️"),
	("ONEDRIVE",   "download"): ("Downloaded sensitive file from OneDrive", "☁️"),
	("ONEDRIVE",   "copy"):     ("Copied file containing sensitive data", "☁️"),
	("ONEDRIVE",   "default"):  ("Accessed OneDrive file containing sensitive data", "☁️"),
	("ENDPOINT",   "usb"):      ("Copied sensitive file to USB drive", "💾"),
	("ENDPOINT",   "print"):    ("Printed document containing sensitive data", "🖨️"),
	("ENDPOINT",   "clipboard"):("Copied sensitive data to clipboard", "📋"),
	("ENDPOINT",   "default"):  ("Sensitive data activity on endpoint device", "💻"),
}


def _detect_story_activity(alert: dict, workload: str) -> tuple[str, str]:
	title = (alert.get("title") or alert.get("displayName") or "").lower()
	category = (alert.get("category") or "").lower()
	clue = "default"
	for kw in ("share", "forward", "send", "upload", "download", "copy", "move", "usb", "print", "clipboard"):
		if kw in title or kw in category:
			clue = kw
			break
	key = (workload, clue)
	if key not in _ACTIVITY_DESCRIPTIONS:
		key = (workload, "default")
	return _ACTIVITY_DESCRIPTIONS.get(key, ("DLP policy violation detected", "🔴"))


def _fmt_story_time(iso: str) -> str:
	if not iso:
		return "—"
	try:
		from datetime import datetime as _dt
		dt = _dt.fromisoformat(iso.replace("Z", "+00:00"))
		return dt.strftime("%d %b %Y, %H:%M UTC")
	except Exception:
		return (iso[:16].replace("T", " ") if len(iso) >= 16 else iso)


def _build_exfiltration_story(alert: dict) -> dict:
	add = alert.get("additionalData") or {}
	evidence = alert.get("evidence") or []
	workload = _detect_workload_for_analysis(alert)

	user_upn, user_display = "", ""
	for ev in evidence:
		t = (ev.get("@odata.type") or "").lower()
		if "user" in t:
			user_upn = ev.get("userPrincipalName") or ev.get("accountName") or ""
			user_display = ev.get("displayName") or user_upn
			break
	if not user_upn:
		for ev in evidence:
			t = (ev.get("@odata.type") or "").lower()
			if "message" in t or "analyzedmessage" in t:
				p1 = ev.get("p1Sender") or {}
				user_upn = p1.get("emailAddress") or p1.get("address") or ""
				user_display = p1.get("displayName") or user_upn
				break
	if not user_upn:
		user_upn = add.get("Upn") or add.get("UserPrincipalName") or ""
		user_display = user_upn

	file_artifacts: list[dict] = []
	for ev in evidence:
		t = (ev.get("@odata.type") or "").lower()
		if "file" in t:
			fd = ev.get("fileDetails") or {}
			name = fd.get("fileName") or ""
			if name:
				file_artifacts.append({"name": name, "path": fd.get("filePath") or ""})

	message: dict = {}
	for ev in evidence:
		t = (ev.get("@odata.type") or "").lower()
		if "message" in t or "analyzedmessage" in t:
			rec_emails: list[str] = []
			for r in (ev.get("recipients") or []):
				e = r.get("emailAddress") or r.get("address") or ""
				if e:
					rec_emails.append(e)
			message = {"subject": ev.get("subject") or "", "recipients": rec_emails}
			break

	sit_types: list[str] = []
	found_sits: set[str] = set()
	for ev in evidence:
		for ca in (ev.get("classificationAttributes") or []):
			msit = ca.get("matchedSensitiveInformationType") or {}
			name = msit.get("displayName") or ""
			if name and name not in found_sits:
				found_sits.add(name)
				sit_types.append(name)

	activity, icon = _detect_story_activity(alert, workload)
	event_time = alert.get("firstActivityDateTime") or alert.get("createdDateTime") or ""
	end_time = alert.get("lastActivityDateTime") or ""

	if workload == "EMAIL":
		source = f"Sender: {user_upn}"
		recs = message.get("recipients") or []
		destination = "Recipients: " + ", ".join(recs[:3]) + (" +more" if len(recs) > 3 else "")
		artifact_name = message.get("subject") or (file_artifacts[0]["name"] if file_artifacts else "—")
	elif workload in ("SHAREPOINT", "ONEDRIVE"):
		prefix = "SharePoint" if workload == "SHAREPOINT" else "OneDrive"
		site = add.get("SiteCollectionUrl") or add.get("SiteUrl") or ""
		source = f"{prefix}: {site or user_upn}"
		destination = "External share" if "share" in activity.lower() else source
		artifact_name = file_artifacts[0]["name"] if file_artifacts else "—"
	elif workload == "ENDPOINT":
		source = f"Endpoint: {add.get('DeviceName') or '—'}"
		destination = "—"
		artifact_name = file_artifacts[0]["name"] if file_artifacts else "—"
	else:
		source = user_upn or "—"
		destination = "—"
		artifact_name = file_artifacts[0]["name"] if file_artifacts else "—"

	policy = add.get("AlertPolicyTitle") or alert.get("title") or "—"
	risk = (alert.get("severity") or "medium").upper()
	outcome_map = {
		"newAlert":   "Alert generated — pending review",
		"inProgress": "Under investigation",
		"resolved":   "Resolved",
		"dismissed":  "Dismissed",
	}
	outcome = outcome_map.get(alert.get("status") or "", "Alert generated — pending review")

	sits_str = ", ".join(sit_types[:3]) or "sensitive information"
	if workload == "EMAIL":
		recs = message.get("recipients") or []
		summary = (
			f"On {_fmt_story_time(event_time)}, {user_display or user_upn or 'a user'} sent an email"
			+ (f" with subject \"{message.get('subject')}\"" if message.get("subject") else "")
			+ f" containing {sits_str} to {len(recs)} recipient(s). "
			+ f"This triggered the DLP policy \"{policy}\" and generated a {risk.title()} severity alert."
		)
	elif workload in ("SHAREPOINT", "ONEDRIVE"):
		summary = (
			f"On {_fmt_story_time(event_time)}, {user_display or user_upn or 'a user'} "
			+ f"{activity.lower()} (\"{artifact_name}\") containing {sits_str}. "
			+ f"This triggered the DLP policy \"{policy}\" and generated a {risk.title()} severity alert."
		)
	elif workload == "ENDPOINT":
		summary = (
			f"On {_fmt_story_time(event_time)}, {user_display or user_upn or 'a user'} performed a "
			+ f"{activity.lower()} on device {add.get('DeviceName') or '—'} "
			+ f"involving \"{artifact_name}\" with {sits_str}. "
			+ f"This triggered the DLP policy \"{policy}\" and generated a {risk.title()} severity alert."
		)
	else:
		summary = (
			f"On {_fmt_story_time(event_time)}, a DLP policy violation was detected. "
			+ f"User: {user_display or user_upn or '—'}. Policy: \"{policy}\". Severity: {risk.title()}."
		)

	event: dict = {
		"activity":             activity,
		"activity_icon":        icon,
		"date_time":            _fmt_story_time(event_time),
		"date_time_end":        _fmt_story_time(end_time) if end_time and end_time != event_time else "",
		"date_iso":             event_time,
		"user_upn":             user_upn,
		"user_display":         user_display,
		"source":               source,
		"destination":          destination,
		"artifact":             artifact_name,
		"additional_artifacts": [a["name"] for a in file_artifacts[1:5]],
		"sit_types":            sit_types,
		"policy":               policy,
		"risk":                 risk,
		"outcome":              outcome,
		"workload":             workload,
		"technical": {
			"alert_id":       alert.get("id") or "",
			"created":        alert.get("createdDateTime") or "",
			"evidence_count": len(evidence),
			"category":       alert.get("category") or "",
			"incident_id":    (alert.get("relatedIncidentIds") or [None])[0],
		},
	}

	return {
		"events":         [event],
		"summary":        summary,
		"user_upn":       user_upn,
		"workload":       workload,
		"event_time_iso": event_time,
		"policy":         policy,
	}


def _build_story_hunt_kql(user_upn: str, event_time_iso: str, workload: str,
                           window_min: int, hunt_type: str) -> str:
	from datetime import datetime as _dt, timedelta as _td
	window = max(15, min(int(window_min), 120))
	try:
		dt = _dt.fromisoformat(event_time_iso.replace("Z", "+00:00"))
		t_start = (dt - _td(minutes=window)).strftime("%Y-%m-%dT%H:%M:%SZ")
		t_end   = (dt + _td(minutes=window)).strftime("%Y-%m-%dT%H:%M:%SZ")
	except Exception:
		return ""

	# Available tables in this tenant: AlertInfo, AlertEvidence, DeviceEvents,
	# DeviceProcessEvents, EmailEvents, BehaviorInfo, BehaviorEntities.
	# CloudAppEvents and Identity* tables require additional licenses.

	upn_filter = f"| where AccountUpn =~ '{user_upn}'\n" if user_upn else ""
	email_upn_filter = f"| where SenderFromAddress =~ '{user_upn}'\n" if user_upn else ""

	# Shared: alert evidence lookup for this user in the window
	alert_evidence_sub = (
		f"AlertEvidence\n"
		f"| where Timestamp between (datetime({t_start}) .. datetime({t_end}))\n"
		f"| where EntityType == 'User'\n"
		f"{upn_filter}"
		f"| distinct AlertId"
	)

	if hunt_type in ("file_shared_with", "file_activities"):
		# DLP / file-related alerts involving this user
		return (
			f"let target_alerts = ({alert_evidence_sub});\n"
			f"AlertInfo\n"
			f"| where Timestamp between (datetime({t_start}) .. datetime({t_end}))\n"
			f"| where AlertId in (target_alerts)\n"
			f"| project Timestamp, AlertId, Title, Severity, Category, ServiceSource, DetectionSource\n"
			f"| order by Timestamp desc\n"
			f"| take 50"
		)
	elif hunt_type == "email_sent":
		# Emails sent by this user in the window
		return (
			f"EmailEvents\n"
			f"| where Timestamp between (datetime({t_start}) .. datetime({t_end}))\n"
			f"{email_upn_filter}"
			f"| project Timestamp, SenderFromAddress, RecipientEmailAddress, Subject,\n"
			f"    DeliveryAction, ThreatTypes, NetworkMessageId\n"
			f"| order by Timestamp desc\n"
			f"| take 50"
		)
	elif hunt_type == "sign_in_activity":
		# Device logon events for this user's account in the window
		return (
			f"DeviceEvents\n"
			f"| where Timestamp between (datetime({t_start}) .. datetime({t_end}))\n"
			f"| where ActionType in ('LogonSuccess', 'LogonFailed', 'LogonAttempted')\n"
			f"| where InitiatingProcessAccountUpn =~ '{user_upn}'\n"
			f"| project Timestamp, DeviceName, ActionType, InitiatingProcessAccountUpn,\n"
			f"    InitiatingProcessAccountDomain, RemoteIP, RemotePort, LocalIP\n"
			f"| order by Timestamp desc\n"
			f"| take 50"
		) if user_upn else ""
	return ""


def _compute_sit_analysis_result(alert: dict, job: dict) -> dict:
	additional_data = alert.get("additionalData") or {}
	evidence = alert.get("evidence") or []

	def set_stage(stage: str, idx: int) -> None:
		with _analysis_lock:
			job["stage"] = stage
			job["stage_index"] = idx

	set_stage("Extracting evidence", 1)
	set_stage("Detecting sensitive information", 2)
	sit_patterns = _extract_sit_patterns_for_analysis(additional_data, evidence)

	# Fallback 1: infer SIT names from alert title keywords
	if not sit_patterns:
		title = alert.get("title") or alert.get("displayName") or ""
		for sit_name, confidence in _infer_sits_from_title(title):
			sit_patterns.append({"name": sit_name, "confidence": confidence, "count": 1, "source": "title-inferred"})

	# Fallback 2: local regex scan on title + description
	if not sit_patterns:
		scan_text = " ".join(filter(None, [
			alert.get("title") or "",
			alert.get("description") or "",
			additional_data.get("AlertPolicyTitle") or "",
		]))
		for sit_name, confidence, count in _run_local_pii_scan(scan_text):
			sit_patterns.append({"name": sit_name, "confidence": confidence, "count": count, "source": "local-scan"})

	set_stage("Identifying artifact types", 3)
	workload = _detect_workload_for_analysis(alert)

	files = [e for e in evidence if any(k in (e.get("@odata.type") or "").lower() for k in ("fileevidence", "file"))]
	messages = [e for e in evidence if any(k in (e.get("@odata.type") or "").lower() for k in ("analyzedmessage", "message"))]

	# ── Email body scan: fetch real content from Flask to get actual PII matches ──
	email_body_patterns: dict[str, dict] = {}
	_scan_debug: dict = {}
	if workload == "EMAIL":
		if not messages:
			_scan_debug["skip_reason"] = "no analyzedMessageEvidence in alert evidence"
			_scan_debug["evidence_types"] = [e.get("@odata.type", "") for e in evidence]
		else:
			msg_ev = messages[0]
			p1 = msg_ev.get("p1Sender") or {}
			sender_upn = (p1.get("emailAddress") or p1.get("address") or "").strip()
			msg_id = (msg_ev.get("internetMessageId") or msg_ev.get("networkMessageId") or "").strip()
			_scan_debug["p1Sender_raw"] = dict(p1)
			_scan_debug["sender_upn"] = sender_upn
			_scan_debug["msg_id"] = msg_id
			_scan_debug["msg_ev_keys"] = list(msg_ev.keys())
			if not sender_upn or not msg_id:
				_scan_debug["skip_reason"] = f"missing sender_upn={sender_upn!r} or msg_id={msg_id!r}"
			else:
				set_stage("Scanning email content", 2)
				try:
					r = _req.get(f"{_LOCAL_BASE}/api/email-body",
								 params={"user_upn": sender_upn, "internet_message_id": msg_id},
								 timeout=30)
					_scan_debug["flask_status"] = r.status_code
					if r.ok:
						body_data = r.json()
						_scan_debug["flask_ok"] = body_data.get("ok")
						_scan_debug["flask_error"] = body_data.get("error")
						flask_patterns = body_data.get("patterns") or []
						_scan_debug["flask_pattern_count"] = len(flask_patterns)
						_scan_debug["flask_pattern_names"] = [p.get("name") for p in flask_patterns]
						for fp in flask_patterns:
							name = fp.get("name", "").strip()
							if not name:
								continue
							matches = fp.get("matches") or []
							samples = fp.get("samples") or []
							ctx_parts = []
							for m in matches[:5]:
								val = m.get("matched") or ""
								loc = m.get("location") or "Email Body"
								surrounding = m.get("surrounding") or ""
								if val:
									ctx_parts.append(f"[{loc}] {val}" + (f"\n  …{surrounding}…" if surrounding else ""))
							evidence_ctx = "\n".join(ctx_parts) if ctx_parts else (samples[0] if samples else "")
							email_body_patterns[name] = {
								"confidence": fp.get("confidence") or "medium",
								"count": fp.get("count") or len(matches) or 1,
								"evidence_context": evidence_ctx,
								"match_at": (matches[0].get("location") or "Email Body") if matches else "Email Body",
								"source": "email-body-scan",
							}
						# Real scan results override title/desc fallbacks
						if email_body_patterns:
							sit_patterns = [
								{"name": n, "confidence": v["confidence"], "count": v["count"], "source": v["source"]}
								for n, v in email_body_patterns.items()
							]
					else:
						_scan_debug["flask_error"] = r.text[:300]
				except Exception as exc:
					_scan_debug["exception"] = str(exc)

	set_stage("Mapping artifact locations", 4)
	artifacts: list = []

	if workload == "EMAIL":
		msg = messages[0] if messages else {}
		subject = msg.get("subject") or alert.get("title") or "Unknown Email"
		artifact_name = f"Email — {subject[:60]}"
		email_findings = []
		for sit in sit_patterns:
			body_info = email_body_patterns.get(sit["name"]) or {}
			ctx = body_info.get("evidence_context") or ""
			match_at = body_info.get("match_at") or "N/A"
			if not ctx:
				for ev_item in evidence:
					ctx = _get_classification_context_for_analysis(ev_item, sit["name"])
					if ctx:
						break
			email_findings.append({"sit_name": sit["name"], "confidence": sit["confidence"],
									"count": sit["count"], "match_at": match_at,
									"found_at": "Email → Body", "evidence_context": ctx})
		artifacts.append({"name": artifact_name, "type": "EMAIL",
						   "found_at": "Email → Body", "status": "complete",
						   "findings": email_findings})
		for att in [f for f in files if not (f.get("fileDetails") or {}).get("filePath")]:
			fd = att.get("fileDetails") or {}
			fname = fd.get("fileName") or "Attachment"
			att_findings = [{"sit_name": s["name"], "confidence": s["confidence"],
							 "count": s["count"], "match_at": "N/A",
							 "found_at": f"Email → Attachment → {fname}",
							 "evidence_context": _get_classification_context_for_analysis(att, s["name"])}
							for s in sit_patterns]
			artifacts.append({"name": fname, "type": "ATTACHMENT",
							   "found_at": f"Email → Attachment → {fname}",
							   "status": "complete", "findings": att_findings})

	elif workload in ("SHAREPOINT", "ONEDRIVE", "ENDPOINT"):
		prefix = {"SHAREPOINT": "SharePoint", "ONEDRIVE": "OneDrive", "ENDPOINT": "Endpoint"}[workload]
		alert_id_str = str(alert.get("id") or "")
		for file_ev in files:
			fd = file_ev.get("fileDetails") or {}
			fname = fd.get("fileName") or "Unknown"
			fpath = fd.get("filePath") or ""
			if workload == "ENDPOINT":
				found_at_base = f"Endpoint → Evidence File → {fname}"
			else:
				found_at_base = f"{prefix} → {fpath or fname}"

			# Try real content scan for SharePoint / OneDrive
			file_scan_patterns: dict[str, dict] = {}
			if workload in ("SHAREPOINT", "ONEDRIVE") and alert_id_str:
				file_url = fpath if fpath.startswith("http") else ""
				try:
					params: dict = {"alert_id": alert_id_str, "filename": fname}
					if file_url:
						params["file_url"] = file_url
					r = _req.get(f"{_LOCAL_BASE}/api/file-content", params=params, timeout=30)
					if r.ok:
						fc = r.json()
						for fp in (fc.get("patterns") or []):
							name = fp.get("name", "").strip()
							if not name:
								continue
							matches = fp.get("matches") or []
							samples = fp.get("samples") or []
							ctx_parts = []
							for m in matches[:5]:
								val = m.get("matched") or ""
								loc = m.get("location") or fname
								surrounding = m.get("surrounding") or ""
								if val:
									ctx_parts.append(f"[{loc}] {val}" + (f"\n  …{surrounding}…" if surrounding else ""))
							evidence_ctx = "\n".join(ctx_parts) if ctx_parts else (samples[0] if samples else "")
							file_scan_patterns[name] = {
								"confidence": fp.get("confidence") or "medium",
								"count": fp.get("count") or len(matches) or 1,
								"evidence_context": evidence_ctx,
								"match_at": (matches[0].get("location") or fname) if matches else fname,
								"source": "file-content-scan",
							}
					if file_scan_patterns:
						sit_patterns = [
							{"name": n, "confidence": v["confidence"], "count": v["count"], "source": v["source"]}
							for n, v in file_scan_patterns.items()
						]
				except Exception:
					pass

			file_findings = []
			for sit in sit_patterns:
				scan_info = file_scan_patterns.get(sit["name"]) or {}
				ctx = scan_info.get("evidence_context") or _get_classification_context_for_analysis(file_ev, sit["name"])
				match_at = scan_info.get("match_at") or "N/A"
				file_findings.append({"sit_name": sit["name"], "confidence": sit["confidence"],
									  "count": sit["count"], "match_at": match_at,
									  "found_at": found_at_base, "evidence_context": ctx})
			is_xl = any(fname.lower().endswith(ext) for ext in (".xlsx", ".xls", ".csv"))
			art_type = "SPREADSHEET" if is_xl else ("ENDPOINT" if workload == "ENDPOINT" else "DOCUMENT")
			artifacts.append({"name": fname, "type": art_type,
							   "found_at": found_at_base, "status": "complete",
							   "findings": file_findings})

	if not artifacts:
		artifacts = [{"name": alert.get("title") or "Alert", "type": "UNKNOWN",
					  "found_at": "N/A", "status": "unable",
					  "error": "No artifact evidence found in alert data.",
					  "findings": [{"sit_name": s["name"], "confidence": s["confidence"],
									"count": s["count"], "match_at": "N/A",
									"found_at": "N/A", "evidence_context": ""}
								   for s in sit_patterns]}]

	all_findings = [f for a in artifacts for f in (a.get("findings") or [])]
	sit_type_names = {f["sit_name"] for f in all_findings if f.get("sit_name")}

	# ── GoHunt: KQL query scoped to the alert's detection day ──
	set_stage("Running threat hunt query", 5)
	go_hunt_result: dict | None = None
	detected_time = alert.get("createdDateTime") or alert.get("firstActivityDateTime") or ""
	if detected_time:
		try:
			from datetime import datetime as _dt, timedelta as _td
			dt = _dt.fromisoformat(detected_time.replace("Z", "+00:00"))
			day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
			day_end = day_start + _td(days=1)
			ds_str = day_start.strftime("%Y-%m-%dT%H:%M:%SZ")
			de_str = day_end.strftime("%Y-%m-%dT%H:%M:%SZ")
			kql = (
				f"AlertInfo\n"
				f"| where Timestamp between (datetime({ds_str}) .. datetime({de_str}))\n"
				f"| where Title contains 'DLP' or ServiceSource contains 'DLP'\n"
				f"| project Timestamp, Title, Severity, ServiceSource, AlertId\n"
				f"| order by Timestamp desc\n"
				f"| take 20"
			)
			from clients import defender_client as _dc_an
			gh = _dc_an.run_hunting_query(kql)
			if gh.get("ok"):
				go_hunt_result = {
					"ok": True,
					"date_range": f"{ds_str[:10]}",
					"rows": gh.get("results") or [],
					"schema": gh.get("schema") or [],
					"kql": kql,
				}
			else:
				go_hunt_result = {"ok": False, "error": gh.get("error", "Query failed")}
		except Exception as exc:
			go_hunt_result = {"ok": False, "error": str(exc)}

	set_stage("Preparing results", 6)
	# Transform to frontend-expected schema
	_art_out = []
	for _a in artifacts:
		_raw_finds = _a.get("findings") or []
		_sits_out = [{"type": f["sit_name"], "confidence": f.get("confidence", "medium"),
		              "count": f.get("count") or 1, "match_at": f.get("match_at", ""),
		              "found_at": f.get("found_at", ""), "evidence_context": f.get("evidence_context", "")}
		             for f in _raw_finds]
		_art_risk = "high" if any(s["confidence"] == "high" for s in _sits_out) else ("medium" if any(s["confidence"] == "medium" for s in _sits_out) else "low")
		_art_out.append({"name": _a.get("name", ""), "type": _a.get("type", ""), "found_at": _a.get("found_at", ""), "status": _a.get("status", ""), "risk": _art_risk, "sits": _sits_out})
	_overall_risk = "High" if any(a["risk"] == "high" for a in _art_out) else ("Medium" if any(a["risk"] == "medium" for a in _art_out) else ("Low" if sit_type_names else "—"))
	_top_sit = max(sit_type_names, key=lambda n: sum(f.get("count") or 1 for f in all_findings if f.get("sit_name") == n), default=None) if sit_type_names else None
	_top_sit_count = sum(f.get("count") or 1 for f in all_findings if f.get("sit_name") == _top_sit) if _top_sit else 0
	result: dict = {
		"artifacts": _art_out,
		"summary": {
			"total_sit_types": len(sit_type_names),
			"total_matches": sum(f.get("count") or 1 for f in all_findings),
			"artifacts_analyzed": len(artifacts),
			"overall_risk": _overall_risk,
			"top_sit": _top_sit,
			"top_sit_count": _top_sit_count,
		},
		"_scan_debug": _scan_debug,
	}
	if go_hunt_result is not None:
		result["go_hunt"] = go_hunt_result
	return result


def _expire_analysis_job(job_id: str, delay: int = 600) -> None:
	time.sleep(delay)
	with _analysis_lock:
		_analysis_jobs.pop(job_id, None)


def _run_analysis_job(job_id: str, alert: dict, alert_id: str) -> None:
	try:
		result = _compute_sit_analysis_result(alert, _analysis_jobs[job_id])
		with _analysis_lock:
			_analysis_jobs[job_id].update({"status": "complete", "stage": "Analysis Complete",
											"stage_index": 7, "result": result})
	except Exception as exc:
		with _analysis_lock:
			_analysis_jobs[job_id].update({"status": "error", "error": str(exc)})
	threading.Thread(target=_expire_analysis_job, args=(job_id,), daemon=True).start()


def make_handler(manager: AutomationManager, root: Path, auth_service):
	class DashboardHandler(SimpleHTTPRequestHandler):
		def __init__(self, *args, **kwargs):
			super().__init__(*args, directory=str(root), **kwargs)

		def log_message(self, format: str, *args) -> None:
			return

		def end_headers(self) -> None:
			self.send_header("Access-Control-Allow-Origin", "*")
			self.send_header("Access-Control-Allow-Headers", "Content-Type")
			self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
			self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
			self.send_header("Pragma", "no-cache")
			super().end_headers()

		def do_OPTIONS(self) -> None:
			self.send_response(HTTPStatus.NO_CONTENT)
			self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
			self.end_headers()

		def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
			encoded = json.dumps(payload).encode("utf-8")
			self.send_response(status_code)
			self.send_header("Content-Type", "application/json; charset=utf-8")
			self.send_header("Content-Length", str(len(encoded)))
			self.end_headers()
			self.wfile.write(encoded)

		def _get_session_token(self) -> str:
			for part in self.headers.get("Cookie", "").split(";"):
				name, _, value = part.strip().partition("=")
				if name.strip() == "rf_session":
					return value.strip()
			return ""

		def _get_current_user(self):
			return auth_service.get_user(self._get_session_token())

		def _source_ip(self) -> str:
			return self.headers.get("X-Forwarded-For", self.client_address[0])

		def _redirect_login(self) -> None:
			self.send_response(HTTPStatus.FOUND)
			self.send_header("Location", "/login")
			self.end_headers()

		def _require_auth_api(self):
			"""For API routes: return user or write 401 and return None."""
			user = self._get_current_user()
			if user is None:
				self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "Authentication required.", "redirect": "/login"})
			return user

		def _require_permission_api(self, user, permission: str):
			"""For API routes: check permission or write 403 and return False."""
			from auth.authorizer import has_permission
			if not has_permission(user.role, permission):
				self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "message": f"Your role ({user.role!r}) does not permit this action."})
				return False
			return True

		def _write_sse(self, event_name: str, payload: str) -> None:
			if event_name == "message":
				message = "\n".join(f"data: {line}" for line in payload.splitlines() or [""])
			else:
				message = f"event: {event_name}\n" + "\n".join(
					f"data: {line}" for line in payload.splitlines() or [""]
				)
			self.wfile.write((message + "\n\n").encode("utf-8"))
			self.wfile.flush()

		def _handle_stream(self) -> None:
			subscriber, backlog, status_payload = manager.add_subscriber()
			self.send_response(HTTPStatus.OK)
			self.send_header("Content-Type", "text/event-stream; charset=utf-8")
			self.send_header("Cache-Control", "no-cache")
			self.send_header("Connection", "keep-alive")
			self.end_headers()
			try:
				self._write_sse("status", json.dumps(status_payload))
				for backlog_line in backlog:
					self._write_sse("message", backlog_line)
				while True:
					try:
						event_name, payload = subscriber.get(timeout=15)
					except queue.Empty:
						self.wfile.write(b": keep-alive\n\n")
						self.wfile.flush()
						continue
					self._write_sse(event_name, payload)
			except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
				pass
			finally:
				manager.remove_subscriber(subscriber)

		def do_GET(self) -> None:
			path = urlparse(self.path).path

			# Login page
			if path in {"/login", "/login.html"}:
				login_file = root / "login.html"
				if login_file.is_file():
					content = login_file.read_bytes()
					self.send_response(HTTPStatus.OK)
					self.send_header("Content-Type", "text/html; charset=utf-8")
					self.send_header("Content-Length", str(len(content)))
					self.end_headers()
					self.wfile.write(content)
				else:
					self.send_response(HTTPStatus.NOT_FOUND)
					self.end_headers()
				return

			# ── Microsoft OAuth: start ─────────────────────────────────────────────
			if path == "/auth/microsoft":
				import secrets as _sec, time as _time
				from auth import ms_oauth as _mso
				_load_flask_api_creds()
				import config as _cfg_ms
				tenant_id   = _cfg_ms.TENANT_ID
				client_id   = _cfg_ms.CLIENT_ID
				if not tenant_id or not client_id:
					self.send_response(HTTPStatus.FOUND)
					self.send_header("Location", "/login?error=oauth_failed")
					self.end_headers()
					return
				# Derive redirect_uri from request Host header (works locally and on Render)
				host = self.headers.get("Host", "localhost:8765")
				scheme = "https" if "onrender.com" in host or os.environ.get("FORCE_HTTPS") else "http"
				redirect_uri = f"{scheme}://{host}/auth/callback"
				state = _sec.token_urlsafe(24)
				with _oauth_states_lock:
					_oauth_states[state] = _time.time()
					# Clean states older than 10 minutes
					cutoff = _time.time() - 600
					for k in [k for k, v in _oauth_states.items() if v < cutoff]:
						del _oauth_states[k]
				url = _mso.build_auth_url(tenant_id, client_id, redirect_uri, state)
				self.send_response(HTTPStatus.FOUND)
				self.send_header("Location", url)
				self.end_headers()
				return

			# ── Microsoft OAuth: callback ──────────────────────────────────────────
			if path == "/auth/callback":
				import time as _time2
				from urllib.parse import parse_qs as _pqs
				from auth import ms_oauth as _mso2
				qs = _pqs(urlparse(self.path).query)
				code  = (qs.get("code")  or [""])[0]
				state = (qs.get("state") or [""])[0]
				error = (qs.get("error") or [""])[0]

				if error:
					self.send_response(HTTPStatus.FOUND)
					self.send_header("Location", f"/login?error={error}")
					self.end_headers()
					return

				# Validate CSRF state
				with _oauth_states_lock:
					valid = state in _oauth_states and (_time2.time() - _oauth_states.get(state, 0)) < 600
					if valid:
						del _oauth_states[state]
				if not valid:
					self.send_response(HTTPStatus.FOUND)
					self.send_header("Location", "/login?error=oauth_failed")
					self.end_headers()
					return

				_load_flask_api_creds()
				import config as _cfg_cb
				tenant_id2   = _cfg_cb.TENANT_ID
				client_id2   = _cfg_cb.CLIENT_ID
				client_secret2 = _cfg_cb.CLIENT_SECRET
				host2 = self.headers.get("Host", "localhost:8765")
				scheme2 = "https" if "onrender.com" in host2 or os.environ.get("FORCE_HTTPS") else "http"
				redirect_uri2 = f"{scheme2}://{host2}/auth/callback"

				try:
					token_resp = _mso2.exchange_code(tenant_id2, client_id2, client_secret2, code, redirect_uri2)
				except Exception as exc:
					print(f"[OAuth] Token exchange failed: {exc}")
					self.send_response(HTTPStatus.FOUND)
					self.send_header("Location", "/login?error=oauth_failed")
					self.end_headers()
					return

				id_token = token_resp.get("id_token", "")
				claims   = _mso2.decode_id_token(id_token)
				email    = _mso2.get_user_email(claims)
				name     = _mso2.get_display_name(claims)

				if not email:
					self.send_response(HTTPStatus.FOUND)
					self.send_header("Location", "/login?error=no_email")
					self.end_headers()
					return

				# Access control: ALLOWED_DOMAINS and INVITE_ONLY checks
				import os as _os_ac
				_allowed_domains = [d.strip().lower() for d in _os_ac.environ.get("ALLOWED_DOMAINS", "").split(",") if d.strip()]
				_invite_only = _os_ac.environ.get("INVITE_ONLY", "").strip().lower() in ("1", "true", "yes")
				if _allowed_domains:
					email_domain = email.split("@")[-1] if "@" in email else ""
					if email_domain not in _allowed_domains:
						self.send_response(HTTPStatus.FOUND)
						self.send_header("Location", "/login?error=access_denied")
						self.end_headers()
						return

				# Look up role from users.json; auto-create analyst if not found
				users_file = root / "data" / "users.json"
				role = "analyst"
				try:
					if users_file.is_file():
						udata = json.loads(users_file.read_text(encoding="utf-8"))
						users_list = udata.get("users", [])
						matched_u = next((u for u in users_list if u.get("email", "").strip().lower() == email), None)
						if matched_u:
							if not matched_u.get("active", True):
								self.send_response(HTTPStatus.FOUND)
								self.send_header("Location", "/login?error=access_denied")
								self.end_headers()
								return
							role = matched_u.get("role", "analyst")
							if not name:
								name = matched_u.get("display_name", email)
						elif _invite_only:
							# Invite-only mode: deny users not pre-added in User Management
							self.send_response(HTTPStatus.FOUND)
							self.send_header("Location", "/login?error=access_denied")
							self.end_headers()
							return
						else:
							# Auto-provision new user from Microsoft as analyst
							import uuid as _uuid2
							new_user = {"id": f"ms-{_uuid2.uuid4().hex[:8]}", "email": email, "display_name": name or email, "role": "analyst", "active": True}
							users_list.append(new_user)
							udata["users"] = users_list
							users_file.write_text(json.dumps(udata, indent=2), encoding="utf-8")
				except Exception as exc:
					print(f"[OAuth] User lookup error: {exc}")

				session_token = auth_service.create_ms_session(email, name or email, role, source_ip=self._source_ip())
				self.send_response(HTTPStatus.FOUND)
				self.send_header("Set-Cookie", f"rf_session={session_token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800")
				self.send_header("Location", "/")
				self.end_headers()
				return

			# Auth: current user info
			if path == "/api/auth/me":
				user = self._get_current_user()
				if user is None:
					self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "Not authenticated."})
					return
				self._write_json(HTTPStatus.OK, {
					"ok": True,
					"user": {
						"id": user.id,
						"email": user.email,
						"display_name": user.display_name,
						"role": user.role,
					}
				})
				return

			if path in {"/status", "/api/system-config/status"}:
				self._write_json(HTTPStatus.OK, manager.get_status_payload())
				return
			if path in {"/api/system-config/schedule"}:
				self._write_json(HTTPStatus.OK, {"ok": True, "schedule": manager.get_status_payload()["schedule"]})
				return
			if path == "/report-data":
				self._write_json(HTTPStatus.OK, manager.build_report_payload())
				return
			if path == "/api/system-config/assignment-group":
				_ag_file = root / "data" / "system_settings.json"
				try:
					_ag_data = json.loads(_ag_file.read_text(encoding="utf-8")) if _ag_file.is_file() else {}
				except Exception:
					_ag_data = {}
				self._write_json(HTTPStatus.OK, {
					"ok": True,
					"group_name": _ag_data.get("default_assignment_group", ""),
				})
				return

			if path == "/api/policy-rules":
				_pr_file = root / "data" / "policy_rules.json"
				try:
					_pr = json.loads(_pr_file.read_text(encoding="utf-8")) if _pr_file.is_file() else {"rules": []}
				except Exception:
					_pr = {"rules": []}
				self._write_json(HTTPStatus.OK, _pr)
				return

			if path == "/api/defender-sit":
				# Fetch full SIT match details from Defender alert evidence (classificationAttributes)
				try:
					from urllib.parse import parse_qs as _pqs_dsit
					_qs_dsit = _pqs_dsit(urlparse(self.path).query)
					_alert_id_dsit = (_qs_dsit.get("alert_id") or [""])[0].strip()
					if not _alert_id_dsit:
						self._write_json(HTTPStatus.OK, {"ok": False, "sits": [], "error": "alert_id required"})
						return
					_load_flask_api_creds()
					import auth.token_manager as _tm_dsit
					import requests as _req_dsit
					_tok_dsit = _tm_dsit.get_graph_token()
					_ar = _req_dsit.get(
						f"https://graph.microsoft.com/v1.0/security/alerts_v2/{_alert_id_dsit}",
						headers={"Authorization": f"Bearer {_tok_dsit}"}, timeout=15,
					)
					if not _ar.ok:
						self._write_json(HTTPStatus.OK, {"ok": False, "sits": [], "error": f"Graph {_ar.status_code}"})
						return
					_alert_dsit = _ar.json()
					_evidence_dsit = _alert_dsit.get("evidence") or []
					# Collect all classification attributes across all evidence items
					_sit_map: dict = {}
					for _ev_dsit in _evidence_dsit:
						# Primary SIT list from sensitiveInfoTypeData
						for _s in (_ev_dsit.get("sensitiveInfoTypeData") or []):
							_sn = (_s.get("sensitiveInfoTypeName") or _s.get("SensitiveInfoTypeName") or _s.get("name") or "").strip()
							if not _sn:
								continue
							if _sn not in _sit_map:
								_sit_map[_sn] = {
									"name": _sn,
									"confidence": _s.get("confidenceLevel") or _s.get("confidence") or "medium",
									"count": int(_s.get("count") or _s.get("Count") or 1),
									"matches": [],
								}
						# matchedSensitiveInformationTypes fallback
						for _sit in (_ev_dsit.get("matchedSensitiveInformationTypes") or []):
							_sn2 = (_sit if isinstance(_sit, str) else (_sit.get("name") or _sit.get("Name") or "")).strip()
							if _sn2 and _sn2 not in _sit_map:
								_sit_map[_sn2] = {
									"name": _sn2,
									"confidence": _sit.get("confidence") if isinstance(_sit, dict) else "medium",
									"count": int(_sit.get("count") or 1) if isinstance(_sit, dict) else 1,
									"matches": [],
								}
						# classificationAttributes — matched text + surrounding context
						for _ca in (_ev_dsit.get("classificationAttributes") or []):
							_msit = _ca.get("matchedSensitiveInformationType") or {}
							_sn3 = (_msit.get("displayName") or _msit.get("name") or "").strip()
							if not _sn3:
								continue
							_tmd = _ca.get("textMatchData") or {}
							_matched = (_tmd.get("matchedText") or "").strip()
							_surround = (_tmd.get("surroundingText") or "").strip()
							if _sn3 not in _sit_map:
								_sit_map[_sn3] = {"name": _sn3, "confidence": "medium", "count": 1, "matches": []}
							if _matched or _surround:
								_sit_map[_sn3]["matches"].append({
									"value": _matched[:200],
									"context": _surround[:300],
								})
					_sits_dsit = list(_sit_map.values())
					self._write_json(HTTPStatus.OK, {"ok": True, "sits": _sits_dsit})
				except Exception as _e_dsit:
					self._write_json(HTTPStatus.OK, {"ok": False, "sits": [], "error": str(_e_dsit)})
				return

			if path == "/api/dlp-notification-sit":
				# Search admin mailbox for DLP notification emails, extract SIT matches
				try:
					from urllib.parse import parse_qs as _pqs_dlp
					_qs_dlp = _pqs_dlp(urlparse(self.path).query)
					_policy_dlp = (_qs_dlp.get("policy_title") or [""])[0].strip()
					_alert_date_dlp = (_qs_dlp.get("alert_date") or [""])[0].strip()

					_load_flask_api_creds()
					import auth.token_manager as _tm_dlp
					import requests as _req_dlp
					import re as _re_dlp

					_tok_dlp = _tm_dlp.get_graph_token()
					_hdrs_dlp = {"Authorization": f"Bearer {_tok_dlp}", "ConsistencyLevel": "eventual"}
					_admin_upn = "admin@m365x73224247.onmicrosoft.com"

					# Always search "Rule detected" — these are the structured DLP notification emails
					# with SIT match data. Then filter by distinctive policy title keywords.
					_generic_kw = {'dlp','policy','data','spi','rule','for','the','and','or','ban','ssn'}
					_policy_kw = [kw for kw in _policy_dlp.split() if len(kw) > 3 and kw.lower() not in _generic_kw] if _policy_dlp else []

					_found_sits = []
					_found_email_subject = ""
					_found_email_date = ""
					_found_body_text = ""

					for _term in ["Rule detected", "DLP Policy"]:
						_sr = _req_dlp.get(
							f"https://graph.microsoft.com/v1.0/users/{_admin_upn}/messages"
							f"?$search=\"{_term}\""
							"&$select=id,subject,receivedDateTime,from,bodyPreview"
							"&$top=25",
							headers=_hdrs_dlp, timeout=15,
						)
						if not _sr.ok:
							continue
						_msgs_dlp = _sr.json().get("value", [])

						# Filter to structured DLP rule notification emails only
						# (subject starts with "Rule detected" — these contain SIT match data)
						for _msg in _msgs_dlp:
							_subj = _msg.get("subject", "")
							if "rule detected" not in _subj.lower():
								continue
							# If policy title given, try to match keywords against subject
							if _policy_kw:
								_kw_match = any(kw.lower() in _subj.lower() for kw in _policy_kw)
								if not _kw_match:
									continue
							# Fetch full body
							_br = _req_dlp.get(
								f"https://graph.microsoft.com/v1.0/users/{_admin_upn}/messages/{_msg['id']}?$select=body,subject,receivedDateTime",
								headers={"Authorization": f"Bearer {_tok_dlp}"}, timeout=15,
							)
							if not _br.ok:
								continue
							import html as _html_dlp
							_body_html = _br.json().get("body", {}).get("content", "")
							_body_text = _re_dlp.sub(r'<[^>]+>', ' ', _body_html)
							_body_text = _html_dlp.unescape(_body_text)
							_body_text = _re_dlp.sub(r'\s+', ' ', _body_text).strip()

							_body_text_raw = _body_text  # keep for context extraction

							# Pattern 1: "{Name} Count: N, Confidence level: High confidence"
							# (Low-severity alert email format)
							_pat1 = r'([\w][\w\s\(\)\.\-]+?)\s+Count:\s*(\d+),\s*Confidence\s+level:\s*((?:High|Medium|Low)\s+confidence(?:\s+level)?)'
							# Extract full Detected blocks with optional Location + Context
							# Handles both email formats:
							#  Exchange: "Detected: Name, Count: N, Confidence Level: High confidence Location: file Context: "text""
							#  Teams:    "Detected: Name, Count: N, Confidence Level: High"
							_pat_block = (
								r'Detected:\s*([^,]+),\s*Count:\s*(\d+),\s*(?:Unique Count:\s*\d+,\s*)?'
								r'Confidence\s+Level:\s*((?:High|Medium|Low)(?:\s+confidence(?:\s+level)?)?)'
								r'(?:\s+Location:\s*(\S+))?'
								r'(?:\s+Context:\s*"([^"]{0,300})")?'
							)

							_sit_blacklist = {'unique', 'count', 'total', 'summary', 'none',
							                  'high', 'medium', 'low', 'click', 'here', 'view',
							                  'detected', 'matched', 'policy', 'rule'}

							_m1 = [(s, c, cf) for s, c, cf in _re_dlp.findall(_pat1, _body_text, _re_dlp.IGNORECASE)
							       if s.strip().lower() not in _sit_blacklist and len(s.strip()) > 3]

							_m2 = [(s, c, cf, loc, ctx) for s, c, cf, loc, ctx in _re_dlp.findall(_pat_block, _body_text, _re_dlp.IGNORECASE)
							       if s.strip().lower() not in _sit_blacklist and len(s.strip()) > 3]

							# Teams format: "Context {text}" — no colon, no quotes (separate pass)
							_pat_teams_ctx = (
								r'Detected:\s*([^,\n]+),\s*Count:\s*\d+,\s*(?:Unique Count:\s*\d+,\s*)?'
								r'Confidence\s+Level:\s*(?:High|Medium|Low)\s+Context\s+'
								r'(.+?)(?=Detected:|Policy\s+name:|$)'
							)
							_m_teams_ctx = _re_dlp.findall(_pat_teams_ctx, _body_text, _re_dlp.IGNORECASE | _re_dlp.DOTALL)

							# Always capture the email — body text is the evidence
							_found_email_subject = _subj
							_found_email_date = _msg.get("receivedDateTime", "")
							_found_body_text = _body_text[:4000]

							# Build sits from whichever pattern matched
							_conf_rank = {"high": 3, "medium": 2, "low": 1}
							_dedup: dict = {}
							if _m2:
								for _s, _c, _cf, _loc, _ctx in _m2:
									_sn = _s.strip()
									_cf_lo = _cf.strip().lower().split()[0]
									_existing = _dedup.get(_sn)
									if not _existing or _conf_rank.get(_cf_lo, 0) > _conf_rank.get(_existing["_rank"], 0):
										_dedup[_sn] = {
											"name": _sn, "count": int(_c),
											"confidence": _cf.strip().split()[0].capitalize(),
											"location": _loc.strip() if _loc else "",
											"context": _ctx.strip()[:200] if _ctx else "",
											"_rank": _cf_lo,
										}
							elif _m1:
								for _s, _c, _cf in _m1:
									_sn = _s.strip()
									_cf_lo = _cf.strip().lower().split()[0]
									_existing = _dedup.get(_sn)
									if not _existing or _conf_rank.get(_cf_lo, 0) > _conf_rank.get(_existing["_rank"], 0):
										_dedup[_sn] = {"name": _sn, "count": int(_c),
										               "confidence": _cf.strip().split()[0].capitalize(),
										               "location": "", "context": "", "_rank": _cf_lo}
							# Fill in Teams-format context for any entry that still has empty context
							for _s_tc, _ctx_tc in _m_teams_ctx:
								_sn_tc = _s_tc.strip()
								if _sn_tc in _dedup and not _dedup[_sn_tc].get("context"):
									_dedup[_sn_tc]["context"] = _re_dlp.sub(r'\s+', ' ', _ctx_tc).strip()[:300]
							_found_sits = [
								{"name": v["name"], "count": v["count"], "confidence": v["confidence"],
								 "location": v.get("location",""), "context": v.get("context","")}
								for v in _dedup.values()
							]
							break  # found the matching email — stop searching
						if _found_email_subject:
							break  # found in this search term, no need to try the next

					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"sits": _found_sits,
						"source_email_subject": _found_email_subject,
						"source_email_date": _found_email_date,
						"body_text": _found_body_text,
					})
				except Exception as _e_dlp:
					self._write_json(HTTPStatus.OK, {"ok": False, "sits": [], "error": str(_e_dlp)})
				return

			if path == "/api/assignment-group/members":
				from urllib.parse import parse_qs
				qs = parse_qs(urlparse(self.path).query)
				group_name = (qs.get("group_name") or [""])[0].strip()
				if not group_name:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "group_name query param is required."})
					return
				try:
					_load_flask_api_creds()
					from clients import servicenow_client as _snow
					groups = _snow.find_group_by_name(group_name)
					if not groups:
						self._write_json(HTTPStatus.OK, {"ok": False, "message": f"No group named '{group_name}' found in ServiceNow."})
						return
					exact = next((g for g in groups if g["name"].lower() == group_name.lower()), groups[0])
					members = _snow.get_group_members(exact["sys_id"])
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"group": {"sys_id": exact["sys_id"], "name": exact["name"]},
						"members": members,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/assignment-group/members/default":
				try:
					_load_flask_api_creds()
					from clients import servicenow_client as _snow_def
					_ag_file3 = root / "data" / "system_settings.json"
					_ag_data3 = json.loads(_ag_file3.read_text(encoding="utf-8")) if _ag_file3.is_file() else {}
					group_name3 = _ag_data3.get("default_assignment_group", "").strip()
					if not group_name3:
						self._write_json(HTTPStatus.OK, {"ok": False, "members": [], "message": "No default assignment group configured."})
						return
					groups3 = _snow_def.find_group_by_name(group_name3)
					if not groups3:
						self._write_json(HTTPStatus.OK, {"ok": False, "members": [], "message": f"Group '{group_name3}' not found in ServiceNow."})
						return
					exact3 = next((g for g in groups3 if g["name"].lower() == group_name3.lower()), groups3[0])
					members3 = _snow_def.get_group_members(exact3["sys_id"])
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"group": {"sys_id": exact3["sys_id"], "name": exact3["name"]},
						"members": members3,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/stream":
				self._handle_stream()
				return
			if path == "/api/users":
				user = self._require_auth_api()
				if user is None:
					return
				if not self._require_permission_api(user, "configure"):
					return
				users_file = root / "data" / "users.json"
				try:
					if users_file.is_file():
						raw = json.loads(users_file.read_text(encoding="utf-8")).get("users", [])
					else:
						raw = []
					safe = [{k: v for k, v in u.items() if k != "password_hash"} for u in raw]
					self._write_json(HTTPStatus.OK, {"ok": True, "users": safe})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/users/snow-assignment":
				user = self._require_auth_api()
				if user is None:
					return
				if not self._require_permission_api(user, "configure"):
					return
				users_file = root / "data" / "users.json"
				try:
					raw = json.loads(users_file.read_text(encoding="utf-8")).get("users", []) if users_file.is_file() else []
				except Exception:
					raw = []
				_map = _load_snow_user_map(root)
				out = [{
					"email": u.get("email", ""),
					"display_name": u.get("display_name", ""),
					"role": u.get("role", ""),
					"snow_assignee": _map.get((u.get("email", "") or "").strip().lower(), ""),
				} for u in raw]
				self._write_json(HTTPStatus.OK, {"ok": True, "users": out})
				return

			# Serve standalone pages
			for _page, _file in (("/report", "report.html"), ("/assignment", "assignment.html"), ("/settings", "settings.html")):
				if path == _page:
					_f = root / _file
					if _f.is_file():
						_c = _f.read_bytes()
						self.send_response(HTTPStatus.OK)
						self.send_header("Content-Type", "text/html; charset=utf-8")
						self.send_header("Content-Length", str(len(_c)))
						self.end_headers()
						self.wfile.write(_c)
					else:
						self.send_response(HTTPStatus.NOT_FOUND)
						self.end_headers()
					return

			# Entra user search: GET /api/entra/search-users?q=<name>
			if path == "/api/entra/search-users":
				from urllib.parse import parse_qs
				q = parse_qs(urlparse(self.path).query).get("q", [""])[0].strip()
				if not q or len(q) < 2:
					self._write_json(HTTPStatus.OK, {"users": []})
					return
				try:
					import auth.token_manager as _tm_eu
					import requests as _req_eu
					_eu_cf = root / "config.json"
					_eu_raw = json.loads(_eu_cf.read_text(encoding="utf-8")) if _eu_cf.is_file() else {}
					_eu_active = _eu_raw.get("active_env", "tenant")
					_eu_env = _eu_raw.get("envs", {}).get(_eu_active, _eu_raw)
					_eu_tenant = _eu_env.get("TENANT_ID", "")
					_eu_client = _eu_env.get("CLIENT_ID", "")
					_eu_secret = _eu_env.get("CLIENT_SECRET", "")
					if not all([_eu_tenant, _eu_client, _eu_secret]):
						raise Exception("Azure credentials not configured. Save them in Developer Config first.")
					_tm_eu.init(_eu_tenant, _eu_client, _eu_secret)
					token = _tm_eu.get_graph_token()
					r = _req_eu.get(
						"https://graph.microsoft.com/v1.0/users",
						headers={"Authorization": f"Bearer {token}"},
						params={
							"$filter": f"startswith(userPrincipalName,'{q}') or mail eq '{q}'",
							"$select": "id,displayName,mail,userPrincipalName",
							"$top": 10,
						},
						timeout=15,
					)
					r.raise_for_status()
					users = [
						{
							"id": u.get("id", ""),
							"displayName": u.get("displayName", ""),
							"email": u.get("mail") or u.get("userPrincipalName", ""),
						}
						for u in r.json().get("value", [])
					]
					self._write_json(HTTPStatus.OK, {"users": users})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "users": []})
				return

			# ServiceNow group members: GET /api/snow/group-members
			if path == "/api/snow/group-members":
				try:
					from clients import servicenow_client as _snow_gm
					import config as _cfg_gm
					# Load Snow credentials from root/config.json
					_gm_cf = root / "config.json"
					_gm_raw = json.loads(_gm_cf.read_text(encoding="utf-8")) if _gm_cf.is_file() else {}
					_gm_active = _gm_raw.get("active_env", "tenant")
					_gm_env = _gm_raw.get("envs", {}).get(_gm_active, _gm_raw)
					_cfg_gm.SNOW_INSTANCE_URL = _gm_env.get("SNOW_INSTANCE") or _gm_env.get("SNOW_URL") or _gm_env.get("SNOW_INSTANCE_URL", "")
					_cfg_gm.SNOW_USERNAME     = _gm_env.get("SNOW_USER") or _gm_env.get("SNOW_USERNAME", "")
					_cfg_gm.SNOW_PASSWORD     = _gm_env.get("SNOW_PASSWORD", "")
					if not _cfg_gm.SNOW_INSTANCE_URL:
						self._write_json(HTTPStatus.OK, {"members": [], "group": "", "error": "ServiceNow credentials not configured."})
						return
					_snow_gm.init()
					# Read group name from system_settings.json
					ss_file = root / "data" / "system_settings.json"
					ss_data = json.loads(ss_file.read_text(encoding="utf-8")) if ss_file.is_file() else {}
					group_name = ss_data.get("default_assignment_group", "")
					if not group_name:
						self._write_json(HTTPStatus.OK, {"members": [], "group": "", "error": "No default assignment group configured."})
						return
					groups = _snow_gm.find_group_by_name(group_name)
					if not groups:
						self._write_json(HTTPStatus.OK, {"members": [], "group": group_name})
						return
					members = _snow_gm.get_group_members(groups[0]["sys_id"])
					self._write_json(HTTPStatus.OK, {
						"group": group_name,
						"members": [
							{
								"name": m.get("user.name", ""),
								"username": m.get("user.user_name", ""),
								"email": m.get("user.email", ""),
								"sys_id": m.get("user.sys_id", ""),
							}
							for m in members
						],
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc), "members": []})
				return

			# Analyst management (read)
			if path == "/api/analysts":
				af = root / "data" / "analysts.json"
				try:
					data = json.loads(af.read_text(encoding="utf-8")) if af.is_file() else {}
					self._write_json(HTTPStatus.OK, {"ok": True, "analysts": data.get("analysts", [])})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Assignment log
			if path == "/api/assignment-log":
				lf = root / "data" / "assignment_log.json"
				try:
					log = json.loads(lf.read_text(encoding="utf-8")) if lf.is_file() else []
					self._write_json(HTTPStatus.OK, {"ok": True, "log": list(reversed(log))})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Config
			if path == "/api/config":
				cf = root / "config.json"
				try:
					raw = json.loads(cf.read_text(encoding="utf-8")) if cf.is_file() else {}
					active = raw.get("active_env", "tenant")
					envs = raw.get("envs", {"tenant": {k: v for k, v in raw.items() if k != "envs"}, "production": {}}) if "envs" in raw else {"tenant": raw, "production": {}}
					for e in ("tenant", "production"):
						if e not in envs:
							envs[e] = {}
					def _mask(v):
						s = str(v or "")
						return s[:4] + "…" + s[-4:] if len(s) >= 8 else s
					masked = {env: {k: (_mask(val) if k in ("CLIENT_SECRET","ANTHROPIC_API_KEY","SNOW_PASSWORD") else val) for k, val in env_data.items()} for env, env_data in envs.items()}
					self._write_json(HTTPStatus.OK, {"ok": True, "active_env": active, "envs": masked})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Health (for settings page)
			if path == "/api/health":
				self._write_json(HTTPStatus.OK, {"status": "ok", "time": datetime.now().astimezone().isoformat()})
				return

			if path == "/api/version":
				html_path = root / "incidents.html"
				try:
					first_line = html_path.read_text(encoding="utf-8", errors="replace")[:120]
				except Exception as e:
					first_line = str(e)
				self._write_json(HTTPStatus.OK, {
					"root": str(root),
					"incidents_html": str(html_path),
					"exists": html_path.is_file(),
					"first_120_chars": first_line,
				})
				return

			# ── Data endpoints (direct Graph/Purview API calls) ──────
			if path == "/api/incidents":
				try:
					_load_flask_api_creds()
					from clients import defender_client as _dc
					from datetime import timezone as _tz
					incidents = _dc.list_incidents(top=50, dlp_only=True)
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"incidents": incidents,
						"fetched_at": datetime.now(_tz.utc).isoformat(),
					})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc), "incidents": []})
				return

			if path.startswith("/api/incidents/") and path.count("/") == 3:
				inc_id = path.rsplit("/", 1)[-1]
				try:
					_load_flask_api_creds()
					from clients import defender_client as _dc
					incident = _dc.get_incident(inc_id)
					self._write_json(HTTPStatus.OK, {"ok": True, "incident": incident})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			if path == "/api/dlp-alerts":
				try:
					_load_flask_api_creds()
					from clients import purview_client as _pc
					from clients import defender_client as _dc
					from datetime import timezone as _tz
					raw_alerts = _pc.list_dlp_alerts(top=100)
					try:
						incidents = _dc.list_incidents(top=50)
						inc_by_id = {str(i.get("id", "")): i for i in incidents}
					except Exception:
						inc_by_id = {}
					alerts = []
					for a in raw_alerts:
						inc_id = str(a.get("Incident ID") or a.get("incidentId") or "").strip()
						inc = inc_by_id.get(inc_id) if inc_id else None
						alerts.append({
							"id":              a.get("_graph_id") or a.get("id") or "",
							"title":           a.get("Alert Name") or a.get("title") or "",
							"displayName":     a.get("Alert Name") or a.get("displayName") or "",
							"severity":        (a.get("Severity") or a.get("severity") or "").lower(),
							"status":          a.get("Status") or a.get("status") or "",
							"createdDateTime": a.get("createdDateTime") or a.get("Time Detected") or "",
							"lastUpdateDateTime": a.get("lastUpdateDateTime") or "",
							"incidentId":      inc_id,
							"_inc_found":      bool(inc),
							"_inc_id":         str(inc.get("id", "")) if inc else "",
							"_inc_title":      (inc.get("displayName") or inc.get("title") or "") if inc else "",
							"_inc_severity":   (inc.get("severity") or "") if inc else "",
							"_inc_status":     (inc.get("status") or "") if inc else "",
							"_inc_createdAt":  (inc.get("createdDateTime") or "") if inc else "",
							"_inc_assignedTo": (inc.get("assignedTo") or "") if inc else "",
						})
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"alerts": alerts,
						"fetched_at": datetime.now(_tz.utc).isoformat(),
					})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc), "alerts": []})
				return

			# Email body + attachments (Mail.Read via Graph)
			if path == "/api/email-body":
				print(f"[email-body] REQUEST IN: {self.path}", flush=True)
				try:
					from urllib.parse import parse_qs as _pqs_eb
					import requests as _req_eb
					import re as _re_eb
					import html as _html_eb
					_qs_eb   = _pqs_eb(urlparse(self.path).query)
					_upn_eb  = (_qs_eb.get("user_upn")            or [""])[0].strip()
					_imid_eb = (_qs_eb.get("internet_message_id") or [""])[0].strip()
					if not _upn_eb or not _imid_eb:
						self._write_json(HTTPStatus.OK, {"ok": False, "error": "user_upn and internet_message_id are required"})
						return
					_load_flask_api_creds()
					import auth.token_manager as _tm_eb
					_tok_eb = _tm_eb.get_graph_token()
					_hdrs_eb     = {"Authorization": f"Bearer {_tok_eb}"}
					_adv_hdrs_eb = dict(_hdrs_eb, **{"ConsistencyLevel": "eventual"})
					_gv1_eb = "https://graph.microsoft.com/v1.0"
					# Find message — angle-bracket form first
					_imid_bare_eb = _imid_eb.strip("<>")
					_msg_id_eb  = ""
					_has_att_eb = False
					for _imid_try_eb in [f"<{_imid_bare_eb}>", _imid_bare_eb]:
						_r_find = _req_eb.get(
							f"{_gv1_eb}/users/{_upn_eb}/messages",
							headers=_adv_hdrs_eb,
							params={"$filter": f"internetMessageId eq '{_imid_try_eb}'", "$select": "id,hasAttachments", "$top": 1, "$count": "true"},
							timeout=20,
						)
						if _r_find.ok:
							_found_msgs = _r_find.json().get("value", [])
							if _found_msgs:
								_msg_id_eb  = _found_msgs[0]["id"]
								_has_att_eb = _found_msgs[0].get("hasAttachments", False)
								break
					if not _msg_id_eb:
						print(f"[email-body] Email NOT found for upn={_upn_eb!r} imid={_imid_eb!r}", flush=True)
						self._write_json(HTTPStatus.OK, {"ok": False, "error": "Email not found in mailbox"})
						return
					# Fetch full body
					_r_body = _req_eb.get(
						f"{_gv1_eb}/users/{_upn_eb}/messages/{_msg_id_eb}",
						headers=_hdrs_eb,
						params={"$select": "body"},
						timeout=25,
					)
					_body_raw_eb     = (_r_body.json().get("body") or {}) if _r_body.ok else {}
					_body_type_eb    = (_body_raw_eb.get("contentType") or "text").lower()
					_body_content_eb = _body_raw_eb.get("content") or ""
					# PII scanner
					def _scan_pii_eb(text, location):
						_defs = [
							("U.S. Social Security Number",        [r"\b\d{3}-\d{2}-\d{4}\b"],                                                      "high"),
							("Credit Card Number",                  [r"\b4[0-9]{12}(?:[0-9]{3})?\b",r"\b5[1-5][0-9]{14}\b",r"\b3[47][0-9]{13}\b"], "high"),
							("U.S. Bank Account Number",            [r"\b\d{8,17}\b"],                                                               "high"),
							("U.S. ITIN",                           [r"\b9\d{2}-[7-9]\d-\d{4}\b"],                                                   "medium"),
							("U.S. Driver's License Number",        [r"\b[A-Z]\d{7}\b",                                                              # CA, WA, CO...
							                                          r"\b[A-Z]{1,2}\d{5,8}\b",                                                       # general letter-prefix
							                                          r"\b\d{7,9}\b"],                                                                "high"),
							("U.S. Phone Number",                   [r"\b(?:\+1[\s-]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"],                       "low"),
							("Email Address",                       [r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"],                     "low"),
						]
						_res = []
						for _pn, _pp, _pc in _defs:
							_ms = []
							for _pat in _pp:
								for _m in _re_eb.finditer(_pat, text):
									_s = max(0, _m.start()-40); _e = min(len(text), _m.end()+40)
									_ms.append({"matched": _m.group(), "location": location, "surrounding": text[_s:_e].replace("\n"," ").strip()})
							if _ms:
								_res.append({"name": _pn, "count": len(_ms), "confidence": _pc, "samples": [x["matched"] for x in _ms[:3]], "matches": _ms[:10]})
						return _res
					# Scan email body for PII
					_body_text_eb  = _re_eb.sub(r"<[^>]+>", " ", _body_content_eb) if _body_type_eb == "html" else _body_content_eb
					_body_text_eb  = _html_eb.unescape(_body_text_eb)
					_body_pats_eb  = _scan_pii_eb(_body_text_eb, "Message Body")
					print(f"[email-body debug] text snippet: {_body_text_eb[:200]!r}", flush=True)
					print(f"[email-body debug] patterns found: {[p['name'] for p in _body_pats_eb]}", flush=True)
					print(f"[email-body debug] samples: {[p.get('samples',[]) for p in _body_pats_eb]}", flush=True)
					# Attachments: list then download+scan each
					_atts_eb = []
					_att_scans_eb = []
					if _has_att_eb:
						_r_atts = _req_eb.get(
							f"{_gv1_eb}/users/{_upn_eb}/messages/{_msg_id_eb}/attachments",
							headers=_hdrs_eb,
							params={"$select": "id,name,size"},
							timeout=20,
						)
						if _r_atts.ok:
							import io as _io_eb
							for _att_eb in _r_atts.json().get("value", []):
								_aname = _att_eb.get("name") or ""
								_asize = _att_eb.get("size") or 0
								_atts_eb.append({"name": _aname, "size": _asize})
								_alnc  = _aname.lower()
								try:
									_val_r = _req_eb.get(
										f"{_gv1_eb}/users/{_upn_eb}/messages/{_msg_id_eb}/attachments/{_att_eb['id']}/$value",
										headers=_hdrs_eb, timeout=30,
									)
									if not _val_r.ok:
										_att_scans_eb.append({"filename": _aname, "type": "error", "error": f"Download failed: {_val_r.status_code}"})
										continue
									_raw_att = _val_r.content
									if _alnc.endswith((".xlsx", ".xls")):
										import openpyxl as _oxl
										_wb  = _oxl.load_workbook(_io_eb.BytesIO(_raw_att), read_only=True, data_only=True)
										_att_pats = []
										for _wsn in _wb.sheetnames:
											_ws   = _wb[_wsn]
											_flat = [str(c) for r in _ws.iter_rows(values_only=True) for c in r if c is not None]
											_att_pats += _scan_pii_eb(" ".join(_flat), _aname)
										_att_scans_eb.append({"filename": _aname, "type": "excel", "patterns": _att_pats})
									elif _alnc.endswith(".csv"):
										import csv as _csv_eb
										_text_att  = _raw_att.decode("utf-8", errors="replace")
										_rows_csv  = list(_csv_eb.reader(_io_eb.StringIO(_text_att)))[:200]
										_att_pats  = _scan_pii_eb(_text_att, _aname)
										_att_scans_eb.append({"filename": _aname, "type": "csv", "rows": _rows_csv, "patterns": _att_pats})
									elif _alnc.endswith((".txt", ".log", ".htm", ".html", ".xml", ".json")):
										_text_att = _raw_att.decode("utf-8", errors="replace")
										_att_pats = _scan_pii_eb(_text_att, _aname)
										_att_scans_eb.append({"filename": _aname, "type": "text", "content": _text_att[:10000], "patterns": _att_pats})
									else:
										_att_scans_eb.append({"filename": _aname, "type": "unsupported"})
								except Exception as _e_att:
									_att_scans_eb.append({"filename": _aname, "type": "error", "error": str(_e_att)})
					# Merge all patterns
					_all_pats_eb = list(_body_pats_eb)
					for _sc_eb in _att_scans_eb:
						for _p_eb in (_sc_eb.get("patterns") or []):
							_all_pats_eb.append(dict(_p_eb, matches=[dict(m, location=_sc_eb["filename"]) for m in _p_eb.get("matches", [])]))
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"body": {"contentType": _body_type_eb, "content": _body_content_eb},
						"attachments": _atts_eb,
						"patterns": _all_pats_eb,
						"attachment_scans": _att_scans_eb,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# File content download (SharePoint / OneDrive / Exchange attachment)
			if path == "/api/file-content":
				try:
					from urllib.parse import parse_qs as _pqs_fc
					import base64 as _b64
					import io as _io
					import requests as _req_fc
					_qs_fc = _pqs_fc(urlparse(self.path).query)
					_alert_id_fc  = (_qs_fc.get("alert_id")   or [""])[0].strip()
					_filename_fc  = (_qs_fc.get("filename")    or [""])[0].strip()
					_file_url_fc  = (_qs_fc.get("file_url")    or [""])[0].strip()
					_sender_fc    = (_qs_fc.get("sender_upn")  or [""])[0].strip()
					_msg_id_param = (_qs_fc.get("msg_id")      or [""])[0].strip()
					_raw_fc: bytes = b""

					_load_flask_api_creds()
					import auth.token_manager as _tm_fc
					_tok_fc = _tm_fc.get_graph_token()
					_hdrs_fc = {"Authorization": f"Bearer {_tok_fc}"}

					# If no direct file_url, try to get it from the alert evidence
					_internet_msg_id_fc = _msg_id_param   # may be pre-supplied by frontend
					_network_msg_id_fc  = ""
					if not _file_url_fc and _alert_id_fc:
						try:
							from clients import purview_client as _pc_fc
							_ev = _pc_fc.get_alert_evidence(_alert_id_fc)
							for _item in (_ev.get("evidence") or []):
								_t = (_item.get("@odata.type") or "").lower()
								if "fileevidence" in _t:
									_fd = _item.get("fileDetails") or _item
									_fn = _fd.get("fileName") or _item.get("fileName") or _item.get("name") or ""
									if not _filename_fc or _fn.lower() == _filename_fc.lower():
										_file_url_fc = _fd.get("fileUrl") or _fd.get("filePath") or _item.get("fileUrl") or _item.get("filePath") or ""
										if _file_url_fc:
											break
								elif "analyzedmessage" in _t or "message" in _t:
									if not _internet_msg_id_fc:
										_internet_msg_id_fc = _item.get("internetMessageId") or ""
										_network_msg_id_fc  = _item.get("networkMessageId") or ""
						except Exception:
							pass

					# Exchange attachment path: search sender's mailbox for the email then get attachment
					_mail_debug: list = []
					_mail_err = ""
					if not _file_url_fc and _sender_fc and (_internet_msg_id_fc or _network_msg_id_fc):
						try:
							_graph_v1 = "https://graph.microsoft.com/v1.0"
							_msgs_list = []
							_adv_hdrs = dict(_hdrs_fc, **{"ConsistencyLevel": "eventual"})
							# Search sender mailbox for the email then download the matching attachment
							if _internet_msg_id_fc:
								_imid_bare = _internet_msg_id_fc.strip("<>")
								for _imid_try in [f"<{_imid_bare}>", _imid_bare]:
									_fq = f"internetMessageId eq '{_imid_try}'"
									_r1 = _req_fc.get(
										f"{_graph_v1}/users/{_sender_fc}/messages",
										headers=_adv_hdrs,
										params={"$filter": _fq, "$select": "id,subject", "$top": 5, "$count": "true"},
										timeout=20,
									)
									_mail_debug.append({"step": f"filter_imid({'angle' if _imid_try.startswith('<') else 'bare'})", "status": _r1.status_code, "count": len(_r1.json().get("value", [])) if _r1.ok else None})
									if _r1.ok:
										_msgs_list = _r1.json().get("value", [])
										if _msgs_list:
											break
									else:
										_mail_err = f"Mailbox filter: {_r1.status_code} — {(_r1.json().get('error',{}).get('message') or _r1.reason)}"
										break
							if not _msgs_list and _filename_fc:
								_r2 = _req_fc.get(
									f"{_graph_v1}/users/{_sender_fc}/mailFolders/sentItems/messages",
									headers=_adv_hdrs,
									params={"$search": f'"{_filename_fc}"', "$select": "id,subject", "$top": 5},
									timeout=20,
								)
								_mail_debug.append({"step": "search_sentitems_filename", "status": _r2.status_code, "count": len(_r2.json().get("value", [])) if _r2.ok else None, "error": _r2.json().get("error") if not _r2.ok else None})
								if _r2.ok:
									_msgs_list = _r2.json().get("value", [])
							if _msgs_list:
								_msg_gid = _msgs_list[0]["id"]
								_att_list_r = _req_fc.get(
									f"{_graph_v1}/users/{_sender_fc}/messages/{_msg_gid}/attachments",
									headers=_hdrs_fc,
									params={"$select": "id,name,size"},
									timeout=20,
								)
								_mail_debug.append({"step": "list_attachments", "status": _att_list_r.status_code, "count": len(_att_list_r.json().get("value", [])) if _att_list_r.ok else None})
								if _att_list_r.ok:
									for _att in _att_list_r.json().get("value", []):
										_att_name = _att.get("name") or ""
										if not _filename_fc or _att_name.lower() == _filename_fc.lower():
											_att_id = _att["id"]
											_val_r = _req_fc.get(
												f"{_graph_v1}/users/{_sender_fc}/messages/{_msg_gid}/attachments/{_att_id}/$value",
												headers=_hdrs_fc,
												timeout=30,
											)
											_mail_debug.append({"step": "download_attachment", "status": _val_r.status_code, "bytes": len(_val_r.content) if _val_r.ok else None})
											if _val_r.ok:
												_raw_fc = _val_r.content
											break
						except Exception as _exc_mail:
							_mail_err = str(_exc_mail)
							_mail_debug.append({"step": "exception", "error": _mail_err})

					if not _raw_fc and not _file_url_fc:
						_err_msg = _mail_err or "No accessible file content found."
						self._write_json(HTTPStatus.OK, {"ok": False, "error": _err_msg})
						return

					# Download SharePoint / OneDrive file via Sites API path lookup
					if not _raw_fc and _file_url_fc.startswith("http"):
						from urllib.parse import urlparse as _up2_fc, unquote as _uq2_fc
						_pu = _up2_fc(_file_url_fc)
						_host_fc  = _pu.netloc
						_pparts   = [p for p in _pu.path.split("/") if p]
						_dl_url_fc = ""
						_r_fc = None
						if "sharepoint.com" in _host_fc and len(_pparts) >= 3 and _pparts[0] == "sites":
							_site_name_fc = _pparts[1]
							_lib_name_fc  = _uq2_fc(_pparts[2])
							_within_parts = [_uq2_fc(p) for p in _pparts[3:]]
							if _filename_fc:
								_fn2 = _uq2_fc(_filename_fc)
								if not _within_parts or _within_parts[-1].lower() != _fn2.lower():
									_within_parts.append(_fn2)
							_within_path = "/".join(_within_parts)
							print(f"[file-dl] lib={_lib_name_fc!r} within={_within_path!r}", flush=True)
							_r_site = _req_fc.get(
								f"https://graph.microsoft.com/v1.0/sites/{_host_fc}:/sites/{_site_name_fc}",
								headers=_hdrs_fc, timeout=15,
							)
							print(f"[file-dl] site:{_r_site.status_code}", flush=True)
							if _r_site.ok:
								_site_id_fc = _r_site.json().get("id", "")
								if _site_id_fc and _within_path:
									_r_drives = _req_fc.get(
										f"https://graph.microsoft.com/v1.0/sites/{_site_id_fc}/drives",
										headers=_hdrs_fc, timeout=15,
									)
									_lib_clean = _lib_name_fc.lower().replace(" ", "")
									_matched_drive_id = ""
									if _r_drives.ok:
										_all_names = [d.get("name", "") for d in _r_drives.json().get("value", [])]
										print(f"[file-dl] drives:{_all_names}", flush=True)
										for _drv in _r_drives.json().get("value", []):
											_dc = (_drv.get("name") or "").lower().replace(" ", "")
											if _dc == _lib_clean or (
												_lib_clean in ("shareddocuments","documents") and _dc in ("documents","shareddocuments")
											):
												_matched_drive_id = _drv.get("id", "")
												break
									else:
										print(f"[file-dl] drives-fail:{_r_drives.status_code}", flush=True)
									print(f"[file-dl] matched={_matched_drive_id!r}", flush=True)
									if _matched_drive_id:
										_dl_url_fc = f"https://graph.microsoft.com/v1.0/drives/{_matched_drive_id}/root:/{_within_path}:/content"
									else:
										# Drives list didn't include this library — try list name with camelCase spaces
										import re as _re_fc
										_lib_spaced = _re_fc.sub(r'(?<=[a-z])(?=[A-Z])', ' ', _lib_name_fc)
										for _ln_try in ([_lib_name_fc] if _lib_spaced == _lib_name_fc else [_lib_name_fc, _lib_spaced]):
											_r_list_drv = _req_fc.get(
												f"https://graph.microsoft.com/v1.0/sites/{_site_id_fc}/lists/{_ln_try}/drive",
												headers=_hdrs_fc, timeout=15,
											)
											print(f"[file-dl] list-drive({_ln_try!r}):{_r_list_drv.status_code}", flush=True)
											if _r_list_drv.ok:
												_list_drv_id = _r_list_drv.json().get("id", "")
												if _list_drv_id:
													_dl_url_fc = f"https://graph.microsoft.com/v1.0/drives/{_list_drv_id}/root:/{_within_path}:/content"
													break
										if not _dl_url_fc:
											_dl_url_fc = f"https://graph.microsoft.com/v1.0/sites/{_site_id_fc}/drive/root:/{_lib_name_fc}/{_within_path}:/content"
									print(f"[file-dl] dl_url={_dl_url_fc}", flush=True)
							if _dl_url_fc:
								_r_fc = _req_fc.get(_dl_url_fc, headers=_hdrs_fc, allow_redirects=True, timeout=30)
								print(f"[file-dl] dl:{_r_fc.status_code}", flush=True)
						if _r_fc is None or not _r_fc.ok:
							# Shares encoding with full file URL (folder + filename)
							_full_file_url = _file_url_fc.rstrip("/") + "/" + _filename_fc if _filename_fc else _file_url_fc
							_enc = _b64.urlsafe_b64encode(f"u!{_full_file_url}".encode()).decode().rstrip("=")
							_sh_url = f"https://graph.microsoft.com/v1.0/shares/{_enc}/driveItem/content"
							_r_fc = _req_fc.get(_sh_url, headers=_hdrs_fc, allow_redirects=True, timeout=30)
							print(f"[file-dl] shares:{_r_fc.status_code}", flush=True)
						if not _r_fc.ok:
							self._write_json(HTTPStatus.OK, {"ok": False, "error": f"Download failed: {_r_fc.status_code} {_r_fc.reason}"})
							return
						_raw_fc = _r_fc.content

					_fname_lc = _filename_fc.lower() if _filename_fc else ""

					# Excel
					if _fname_lc.endswith((".xlsx", ".xls")):
						try:
							import openpyxl as _xl
							_wb = _xl.load_workbook(_io.BytesIO(_raw_fc), read_only=True, data_only=True)
							_sheets_out: dict = {}
							for _sn in _wb.sheetnames:
								_ws = _wb[_sn]
								_rows_out = []
								for _row in _ws.iter_rows(values_only=True, max_row=200):
									_rows_out.append([str(c) if c is not None else "" for c in _row])
								_sheets_out[_sn] = _rows_out
							_xl_flat = "\n".join(" ".join(r) for rows in _sheets_out.values() for r in rows)
							_xl_pats = _scan_text_pii(_xl_flat)
							self._write_json(HTTPStatus.OK, {"ok": True, "type": "excel", "sheets": _sheets_out, "patterns": _xl_pats})
						except ImportError:
							self._write_json(HTTPStatus.OK, {"ok": False, "error": "openpyxl not installed — cannot parse Excel files."})
						return

					# CSV
					if _fname_lc.endswith(".csv"):
						import csv as _csv
						_text_fc = _raw_fc.decode("utf-8-sig", errors="replace")
						_reader = _csv.reader(_io.StringIO(_text_fc))
						_rows_csv = [row for row in _reader][:200]
						_csv_pats = _scan_text_pii(_text_fc)
						self._write_json(HTTPStatus.OK, {"ok": True, "type": "csv", "rows": _rows_csv, "patterns": _csv_pats})
						return

					# Plain text / fallback
					_content_fc = _raw_fc.decode("utf-8", errors="replace")
					_txt_pats = _scan_text_pii(_content_fc)
					self._write_json(HTTPStatus.OK, {"ok": True, "type": "text", "content": _content_fc[:20000], "patterns": _txt_pats})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# User context (risk level + manager from Graph)
			if path == "/api/user-context":
				try:
					import requests as _req
					qs = urlparse(self.path).query
					url = f"{_FLASK_BASE}/api/user-context" + (f"?{qs}" if qs else "")
					r = _req.get(url, timeout=20)
					self._write_json(HTTPStatus.OK if r.ok else HTTPStatus(r.status_code), r.json())
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# DLP alert detail — fetch directly from Graph API
			if path.startswith("/api/dlp-alerts/") and "/" not in path[len("/api/dlp-alerts/"):]:
				alert_id = path[len("/api/dlp-alerts/"):]
				try:
					_load_flask_api_creds()
					from clients import purview_client as _pc
					detail = _pc.get_alert_evidence(alert_id)
					self._write_json(HTTPStatus.OK, {"ok": True, "alert": detail})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			# ServiceNow ticket details for a Defender incident
			if path == "/api/snow-details":
				# Fallback map — used when the JSON file is absent (e.g. ephemeral Render filesystem)
				_SNOW_MAP_FALLBACK = {}
				try:
					_load_flask_api_creds()
					import json as _json_sd
					from urllib.parse import parse_qs as _pqs
					inc_id = _pqs(urlparse(self.path).query).get("inc_id", [""])[0].strip()
					_map_file2 = root / "data" / "snow_incident_map.json"
					_map2 = dict(_SNOW_MAP_FALLBACK)
					if _map_file2.is_file():
						try: _map2.update(_json_sd.loads(_map_file2.read_text(encoding="utf-8")))
						except Exception: pass
					entry = _map2.get(str(inc_id))
					# NOTE: previously fell back to querying ServiceNow directly by description
					# text when the local map had no entry, so a ticket already existing in SNOW
					# (e.g. left over from earlier testing) would resurface even after clearing
					# data/snow_incident_map.json. Removed — the local map is now the single
					# source of truth, so "Create Tickets" always starts fresh unless a mapping
					# was actually written here (by /api/snow-create-for-incident).
					if not entry:
						self._write_json(HTTPStatus.OK, {"ok": False})
						return
					# Load children: prefer stored list in map, fall back to parent_incident query
					children = list(entry.get("children") or [])
					if not children and entry.get("snow_sys_id"):
						try:
							import config as _cfg_sd
							import requests as _req_sd
							_auth_sd = (_cfg_sd.SNOW_USERNAME, _cfg_sd.SNOW_PASSWORD)
							_base_sd = _cfg_sd.SNOW_INSTANCE_URL.rstrip("/")
							r_ch = _req_sd.get(
								f"{_base_sd}/api/now/table/incident",
								auth=_auth_sd,
								headers={"Accept": "application/json"},
								params={
									"sysparm_query": f"parent_incident={entry['snow_sys_id']}",
									"sysparm_fields": "number,sys_id",
									"sysparm_limit": 50,
								},
								timeout=15,
							)
							if r_ch.ok:
								for row in r_ch.json().get("result", []):
									children.append({
										"ticket": row.get("number", ""),
										"sys_id": row.get("sys_id", ""),
									})
						except Exception:
							pass
					_cfg_raw4 = {}
					try:
						import json as _j4
						_cfg_raw4 = _j4.loads((root / "config.json").read_text(encoding="utf-8"))
					except Exception: pass
					_env4 = _cfg_raw4.get("envs", {}).get(_cfg_raw4.get("active_env", "tenant"), _cfg_raw4)
					snow_base = (os.environ.get("SNOW_INSTANCE_URL") or _env4.get("SNOW_INSTANCE") or _env4.get("SNOW_URL") or "").rstrip("/")
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"snow_number": entry.get("snow_number", ""),
						"snow_sys_id": entry.get("snow_sys_id", ""),
						"snow_base_url": snow_base,
						"children": children,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# ServiceNow status + resolve-notes proxy (GET with query string)
			# ── ServiceNow ticket status (local — no Flask needed) ────────────────
			if path == "/api/snow-status":
				from urllib.parse import parse_qs as _pqs_ss
				_qs_ss = _pqs_ss(urlparse(self.path).query)
				_inc_id_ss  = (_qs_ss.get("inc_id")   or [""])[0].strip()
				_alert_id_ss = (_qs_ss.get("alert_id") or [""])[0].strip()
				# Load snow map (fallback + file override)
				_SS_FB = {}
				_ss_map = dict(_SS_FB)
				_ss_map_file = root / "data" / "snow_incident_map.json"
				if _ss_map_file.is_file():
					try: _ss_map.update(json.loads(_ss_map_file.read_text(encoding="utf-8")))
					except Exception: pass
				_ss_entry = _ss_map.get(str(_inc_id_ss))
				if not _ss_entry or not _ss_entry.get("snow_number"):
					self._write_json(HTTPStatus.OK, {"configured": False})
					return
				# Resolve snow base URL from config
				_ss_snow_base = ""
				try:
					_load_flask_api_creds()
					import config as _cfg_ss
					_ss_snow_base = (_cfg_ss.SNOW_INSTANCE_URL or "").rstrip("/")
				except Exception: pass
				if not _ss_snow_base:
					try:
						import json as _json_ss
						_ss_cfg = _json_ss.loads((root / "config.json").read_text(encoding="utf-8")) if (root / "config.json").is_file() else {}
						_ss_env = _ss_cfg.get("envs", {}).get(_ss_cfg.get("active_env", "tenant"), _ss_cfg)
						_ss_snow_base = (_ss_env.get("SNOW_INSTANCE") or _ss_env.get("SNOW_URL") or _ss_env.get("SNOW_INSTANCE_URL", "")).rstrip("/")
					except Exception: pass
				# Find the right ticket: matching child by alert_id, then any child, then parent
				_ss_ticket = ""
				_ss_sys_id = ""
				if _alert_id_ss:
					for _ch in (_ss_entry.get("children") or []):
						if _ch.get("alert_id") == _alert_id_ss:
							_ss_ticket  = _ch["ticket"]
							_ss_sys_id  = _ch.get("sys_id", "")
							break
				if not _ss_ticket:
					_ss_ticket = _ss_entry.get("snow_number", "")
					_ss_sys_id = _ss_entry.get("snow_sys_id", "")
				_ss_link = ""
				if _ss_snow_base and _ss_sys_id:
					if not _ss_snow_base.startswith(("http://", "https://")):
						_ss_snow_base = "https://" + _ss_snow_base
					_ss_link = f"{_ss_snow_base}/nav_to.do?uri=incident.do%3Fsys_id%3D{_ss_sys_id}"
				self._write_json(HTTPStatus.OK, {
					"configured": True,
					"ticket": _ss_ticket,
					"snow_number": _ss_ticket,
					"snow_link": _ss_link,
				})
				return

			_SNOW_GET_ROUTES = {"/api/resolve-notes"}
			if path in _SNOW_GET_ROUTES:
				try:
					import requests as _req
					qs = urlparse(self.path).query
					url = f"{_FLASK_BASE}{path}" + (f"?{qs}" if qs else "")
					r = _req.get(url, timeout=20)
					self._write_json(HTTPStatus.OK if r.ok else HTTPStatus(r.status_code), r.json())
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# Report data (requires Graph — returns empty if not configured)
			if path == "/api/report/data":
				try:
					_load_flask_api_creds()
					import config as _cfg
					import auth.token_manager as _tm
					_tm.init(_cfg.TENANT_ID, _cfg.CLIENT_ID, _cfg.CLIENT_SECRET)
					token = _tm.get_graph_token()
					import requests as _req
					alerts_resp = _req.get(
						"https://graph.microsoft.com/v1.0/security/alerts_v2?$filter=serviceSource eq 'dataLossPrevention'&$top=50",
						headers={"Authorization": f"Bearer {token}"}, timeout=20,
					)
					incidents_resp = _req.get(
						"https://graph.microsoft.com/v1.0/security/incidents?$top=50",
						headers={"Authorization": f"Bearer {token}"}, timeout=20,
					)
					alerts = alerts_resp.json().get("value", []) if alerts_resp.ok else []
					incidents = incidents_resp.json().get("value", []) if incidents_resp.ok else []
					inc_by_id = {str(i.get("id", "")): i for i in incidents}
					# ServiceNow ticket numbers added via the "Add to Database" button
					snow_by_alert_id: dict[str, str] = {}
					_rp_con = _snow_report_db_connect(root)
					try:
						for _rd_row in _rp_con.execute("SELECT alert_id, snow_ticket FROM snow_report_tickets WHERE ticket_type = 'child'"):
							snow_by_alert_id[_rd_row[0]] = _rd_row[1] or ""
						# Resolution Notes is still manual-entry — preserved across this refresh.
						# Description now comes live from the ServiceNow ticket (below), so it's
						# no longer a manually-preserved field.
						preserved_notes_by_alert_id: dict[str, str] = {}
						for _pr_row in _rp_con.execute("SELECT alert_id, resolution_notes FROM dlp_report"):
							preserved_notes_by_alert_id[_pr_row[0]] = _pr_row[1] or ""
					except Exception:
						preserved_notes_by_alert_id = {}
					# Live ServiceNow ticket state + description per alert (not Defender's
					# incident status/description — these columns mean the SNOW ticket's own
					# field values).
					snow_fields_by_ticket: dict[str, dict[str, str]] = {}
					try:
						from clients import servicenow_client as _snow_rp
						snow_fields_by_ticket = _snow_rp.get_fields_by_numbers(list(snow_by_alert_id.values()))
					except Exception:
						pass
					for a in alerts:
						inc_id = str(a.get("incidentId") or "")
						inc = inc_by_id.get(inc_id, {})
						a["_inc_title"] = inc.get("displayName") or inc.get("title") or ""
						a["_inc_status"] = inc.get("status", "")
						a["_inc_severity"] = inc.get("severity", "")
						a["_inc_assignedTo"] = inc.get("assignedTo", "")
						a["_inc_classification"] = inc.get("classification", "")
						a["_inc_determination"] = inc.get("determination", "")
						a["_inc_createdAt"] = inc.get("createdDateTime", "")
						a["_inc_resolvedAt"] = inc.get("resolvedDateTime", "")
					COLS = ["Incident Ticket Number (ServiceNow)","Detection Source","Incident ID (Microsoft Defender)","Category","Urgency","Incident Severity (Microsoft Defender)","Incident Ticket Status (ServiceNow)","Title (Short Description)","Description","Date of Alert (Microsoft Defender)","Date of Alert Resolved (Microsoft Defender)","Incident Ticket Assigned To (ServiceNow)","DLP Policy","Classification","Determination","User","Email - Work","Egress Channel","Resolution Notes"]
					AUTO_COLS = [c for c in COLS if c not in ("Resolution Notes",)]
					rows = []
					now_iso_rp = datetime.now().astimezone().isoformat()
					for a in alerts:
						alert_id_rp = str(a.get("id") or "")
						add = a.get("additionalData") or {}
						ev = a.get("evidence") or []
						user_name = user_email = ""
						for e in ev:
							if "userevidence" in (e.get("@odata.type") or "").lower():
								ua = e.get("userAccount") or {}
								user_name = ua.get("displayName") or ua.get("accountName") or ""
								user_email = ua.get("userPrincipalName") or ""
								break
						tl = (a.get("title") or "").lower()
						egress = "Exchange Online" if ("exchange" in tl or "email" in tl or "mail" in tl) else ("SharePoint Online" if "sharepoint" in tl else ("OneDrive" if "onedrive" in tl else ("Endpoint Device" if "device" in tl else "")))
						preserved_notes = preserved_notes_by_alert_id.get(alert_id_rp, "")
						alert_ticket_number = snow_by_alert_id.get(alert_id_rp, "")
						snow_fields = snow_fields_by_ticket.get(alert_ticket_number, {}) if alert_ticket_number else {}
						row = {
							"Incident Ticket Number (ServiceNow)": alert_ticket_number,
							"Detection Source": "Microsoft Data Loss Prevention",
							"Incident ID (Microsoft Defender)": a.get("incidentId") or "",
							"Category": a.get("category") or "",
							"Urgency": (a.get("_inc_severity") or a.get("severity") or "").capitalize(),
							"Incident Severity (Microsoft Defender)": (a.get("_inc_severity") or a.get("severity") or "").capitalize(),
							"Incident Ticket Status (ServiceNow)": snow_fields.get("state", ""),
							"Title (Short Description)": a.get("_inc_title") or a.get("title") or "",
							"Description": snow_fields.get("description", ""),
							"Date of Alert (Microsoft Defender)": a.get("createdDateTime") or "",
							"Date of Alert Resolved (Microsoft Defender)": a.get("resolvedDateTime") or "",
							"Incident Ticket Assigned To (ServiceNow)": a.get("_inc_assignedTo") or "",
							"DLP Policy": add.get("AlertPolicyTitle") or "",
							"Classification": a.get("_inc_classification") or "",
							"Determination": a.get("_inc_determination") or "",
							"User": user_name,
							"Email - Work": user_email,
							"Egress Channel": egress,
							"Resolution Notes": preserved_notes,
						}
						rows.append(row)
						if alert_id_rp:
							try:
								_rp_con.execute(
									"""
									INSERT INTO dlp_report
										(alert_id, incident_id, category, severity, ticket_status, title, description,
										 created_at, resolved_at, assigned_to, dlp_policy, classification, determination,
										 user_name, user_email, egress_channel, resolution_notes, updated_at)
									VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
									ON CONFLICT(alert_id) DO UPDATE SET
										incident_id    = excluded.incident_id,
										category       = excluded.category,
										severity       = excluded.severity,
										ticket_status  = excluded.ticket_status,
										title          = excluded.title,
										description    = excluded.description,
										created_at     = excluded.created_at,
										resolved_at    = excluded.resolved_at,
										assigned_to    = excluded.assigned_to,
										dlp_policy     = excluded.dlp_policy,
										classification = excluded.classification,
										determination  = excluded.determination,
										user_name      = excluded.user_name,
										user_email     = excluded.user_email,
										egress_channel = excluded.egress_channel,
										updated_at     = excluded.updated_at
									""",
									(alert_id_rp, row["Incident ID (Microsoft Defender)"], row["Category"], row["Incident Severity (Microsoft Defender)"],
									 row["Incident Ticket Status (ServiceNow)"], row["Title (Short Description)"], row["Description"],
									 row["Date of Alert (Microsoft Defender)"], row["Date of Alert Resolved (Microsoft Defender)"], row["Incident Ticket Assigned To (ServiceNow)"],
									 row["DLP Policy"], row["Classification"], row["Determination"], user_name, user_email, egress, preserved_notes, now_iso_rp),
								)
							except Exception:
								pass
					_rp_con.commit()
					_rp_con.close()
					self._write_json(HTTPStatus.OK, {"ok": True, "columns": COLS, "rows": rows, "auto_cols": AUTO_COLS})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			# Daily / monthly incident-received counts for the Executive dashboard,
			# from the same dlp_report table /api/report/data keeps up to date.
			if path == "/api/report/trends":
				try:
					con = _snow_report_db_connect(root)
					try:
						daily_rows = con.execute(
							"""
							SELECT date(created_at) AS day, COUNT(*) AS cnt
							FROM dlp_report
							WHERE created_at IS NOT NULL AND created_at != ''
							  AND date(created_at) >= date('now', '-29 days')
							GROUP BY day
							ORDER BY day
							"""
						).fetchall()
						monthly_rows = con.execute(
							"""
							SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS cnt
							FROM dlp_report
							WHERE created_at IS NOT NULL AND created_at != ''
							  AND date(created_at) >= date('now', 'start of month', '-11 months')
							GROUP BY month
							ORDER BY month
							"""
						).fetchall()
						# Previous 30-day window, for the KPI trend arrow
						prev_total_row = con.execute(
							"""
							SELECT COUNT(*) FROM dlp_report
							WHERE created_at IS NOT NULL AND created_at != ''
							  AND date(created_at) >= date('now', '-59 days')
							  AND date(created_at) <  date('now', '-29 days')
							"""
						).fetchone()
						severity_rows = con.execute(
							"""
							SELECT COALESCE(NULLIF(severity, ''), 'Unknown') AS sev, COUNT(*) AS cnt
							FROM dlp_report
							WHERE created_at IS NOT NULL AND created_at != ''
							  AND date(created_at) >= date('now', '-29 days')
							GROUP BY sev
							"""
						).fetchall()
						status_rows = con.execute(
							"""
							SELECT COALESCE(NULLIF(ticket_status, ''), 'Unknown') AS st, COUNT(*) AS cnt
							FROM dlp_report
							WHERE created_at IS NOT NULL AND created_at != ''
							  AND date(created_at) >= date('now', '-29 days')
							GROUP BY st
							"""
						).fetchall()
					finally:
						con.close()
					# Fill gaps so the charts show a continuous 30-day / 12-month timeline,
					# not just the sparse days/months that happen to have incidents.
					daily_by_day = {r[0]: r[1] for r in daily_rows}
					today = datetime.now().date()
					daily = []
					for i in range(29, -1, -1):
						d = today - timedelta(days=i)
						key = d.isoformat()
						daily.append({"date": key, "count": daily_by_day.get(key, 0)})
					monthly_by_month = {r[0]: r[1] for r in monthly_rows}
					monthly = []
					year, month = today.year, today.month
					for i in range(11, -1, -1):
						m = month - i
						y = year
						while m <= 0:
							m += 12
							y -= 1
						key = f"{y:04d}-{m:02d}"
						monthly.append({"month": key, "count": monthly_by_month.get(key, 0)})
					total_30d = sum(d["count"] for d in daily)
					prev_30d = prev_total_row[0] if prev_total_row else 0
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"daily": daily,
						"monthly": monthly,
						"total_30d": total_30d,
						"prev_30d": prev_30d,
						"severity": [{"severity": r[0], "count": r[1]} for r in severity_rows],
						"status": [{"status": r[0], "count": r[1]} for r in status_rows],
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			# SIT analysis status
			if path.startswith("/api/dlp-alerts/") and "/analyze/" in path:
				parts = path.split("/")
				try:
					alert_idx = parts.index("dlp-alerts") + 1
					analyze_idx = parts.index("analyze") + 1
					a_id = parts[alert_idx]
					j_id = parts[analyze_idx]
				except (ValueError, IndexError):
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid path."})
					return
				with _analysis_lock:
					job = dict(_analysis_jobs.get(j_id) or {})
				if not job or job.get("alert_id") != a_id:
					self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Job not found."})
					return
				resp_data: dict[str, Any] = {"ok": True, "job_id": j_id, "status": job.get("status"),
											 "stage": job.get("stage"), "stage_index": job.get("stage_index", 0)}
				if job.get("status") == "complete":
					resp_data["result"] = job.get("result", {})
				elif job.get("status") == "error":
					resp_data["error"] = job.get("error", "Unknown error")
				self._write_json(HTTPStatus.OK, resp_data)
				return

			_HTML_ROUTES = {
				"/": "incidents.html",
				"/incidents": "incidents.html",
				"/incidents.html": "incidents.html",
				"/dashboard": "dashboard.html",
				"/dashboard.html": "dashboard.html",
				"/overview": "overview.html",
				"/overview.html": "overview.html",
				"/report": "report.html",
				"/report.html": "report.html",
				"/assignment": "assignment.html",
				"/assignment.html": "assignment.html",
				"/settings": "settings.html",
				"/settings.html": "settings.html",
				"/system-config": "system_config.html",
				"/system-config.html": "system_config.html",
				"/data-classification": "data_classification.html",
				"/data_classification.html": "data_classification.html",
				"/data_classification": "data_classification.html",
			}
			if path in _HTML_ROUTES:
				html_file = root / _HTML_ROUTES[path]
				if html_file.is_file():
					content = html_file.read_bytes()
					self.send_response(HTTPStatus.OK)
					self.send_header("Content-Type", "text/html; charset=utf-8")
					self.send_header("Content-Length", str(len(content)))
					self.send_header("Cache-Control", "no-store")
					self.end_headers()
					self.wfile.write(content)
					return
			super().do_GET()

		def do_POST(self) -> None:
			path = urlparse(self.path).path
			try:
				payload = parse_request_body(self)
			except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
				self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Request body must be valid JSON."})
				return

			# Auth: login
			if path == "/api/auth/login":
				email    = str(payload.get("email") or "").strip()
				password = str(payload.get("password") or "")
				token = auth_service.login(email, password, source_ip=self._source_ip())
				if token is None:
					self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "message": "Invalid email or password."})
					return
				user = auth_service.get_user(token)
				if user is None:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": "Session error after login."})
					return
				# Set HttpOnly session cookie (8 hours = 28800 seconds)
				self.send_response(HTTPStatus.OK)
				self.send_header("Content-Type", "application/json; charset=utf-8")
				self.send_header(
					"Set-Cookie",
					f"rf_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"
				)
				body = json.dumps({"ok": True, "user": {
					"id": user.id, "email": user.email,
					"display_name": user.display_name, "role": user.role
				}}).encode("utf-8")
				self.send_header("Content-Length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)
				return

			# Auth: logout
			if path == "/api/auth/logout":
				token = self._get_session_token()
				auth_service.logout(token, source_ip=self._source_ip())
				self.send_response(HTTPStatus.OK)
				self.send_header("Content-Type", "application/json; charset=utf-8")
				# Expire the cookie
				self.send_header(
					"Set-Cookie",
					"rf_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
				)
				body = json.dumps({"ok": True}).encode("utf-8")
				self.send_header("Content-Length", str(len(body)))
				self.end_headers()
				self.wfile.write(body)
				return

			if path in {"/run", "/api/system-config/run"}:
				if self._require_auth_api() is None:
					return
				if path == "/run":
					command = str(payload.get("command") or "").strip().lower()
					if command not in {"rf.run", "rf.tools", "rf.start"}:
						self._write_json(
							HTTPStatus.BAD_REQUEST,
							{
								"ok": False,
								"status": "error",
								"message": "Only rf.run is supported by the local automation backend.",
							},
						)
						return
				started, status_payload = manager.start_process(source="manual dashboard action")
				status_code = HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT
				response_payload = {**status_payload, "ok": started, "status": "started" if started else "already_running"}
				self._write_json(status_code, response_payload)
				return

			if path == "/api/system-config/terminate":
				stopped, status_payload = manager.terminate_process(source="manual dashboard action")
				status_code = HTTPStatus.ACCEPTED if stopped else HTTPStatus.CONFLICT
				response_payload = {**status_payload, "ok": stopped, "status": "terminating" if stopped else "not_running"}
				self._write_json(status_code, response_payload)
				return

			if path == "/api/artifacts/delete":
				if self._require_auth_api() is None:
					return
				try:
					response_payload = manager.delete_artifact(payload.get("relative_path"))
				except ValueError as exc:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
					return
				except FileNotFoundError as exc:
					self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": str(exc)})
					return
				except OSError as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": f"Unable to delete artifact: {exc}"})
					return
				self._write_json(HTTPStatus.OK, response_payload)
				return

			if path == "/api/system-config/schedule":
				try:
					status_payload = manager.save_schedule(payload)
				except ValueError as exc:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
					return
				self._write_json(HTTPStatus.OK, {**status_payload, "ok": True, "status": "saved"})
				return

			# Assignment group config — save the default group name (admin only).
			if path == "/api/system-config/assignment-group":
				_ag_cu = self._require_auth_api()
				if _ag_cu is None:
					return
				if not self._require_permission_api(_ag_cu, "configure"):
					return
				_ag_file = root / "data" / "system_settings.json"
				try:
					_ag_data = json.loads(_ag_file.read_text(encoding="utf-8")) if _ag_file.is_file() else {}
				except Exception:
					_ag_data = {}
				group_name = str(payload.get("group_name") or "").strip()
				if not group_name:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "group_name is required."})
					return
				_ag_data["default_assignment_group"] = group_name
				_ag_file.parent.mkdir(parents=True, exist_ok=True)
				_ag_file.write_text(json.dumps(_ag_data, indent=2), encoding="utf-8")
				self._write_json(HTTPStatus.OK, {"ok": True, "group_name": group_name})
				return

			if path == "/api/incidents/assign-to-me":
				current_user = self._require_auth_api()
				if current_user is None:
					return
				if not self._require_permission_api(current_user, "assign"):
					return
				try:
					import config as _cfg
					import auth.token_manager as _tm
					from clients import servicenow_client as _snow
					from pipeline import assignment_validator as _av
					from clients import defender_client as _dc

					snow_number     = str(payload.get("snow_number") or "").strip()
					defender_inc_id = str(payload.get("defender_incident_id") or "").strip()

					if not snow_number:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "snow_number is required."})
						return
					if not defender_inc_id:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "defender_incident_id is required."})
						return

					fallback_group = str(payload.get("fallback_group_name") or "").strip()
					# Also check server-side saved default group if frontend didn't send one
					if not fallback_group:
						_ag_file = root / "data" / "system_settings.json"
						try:
							_ag_data = json.loads(_ag_file.read_text(encoding="utf-8")) if _ag_file.is_file() else {}
							fallback_group = _ag_data.get("default_assignment_group", "")
						except Exception:
							pass

					_load_flask_api_creds()

					# Developer role, and the "Microsoft CDX" demo-tenant identity, use the
					# shared RedFlagged Service ServiceNow account; Defender uses the real MS account.
					snow_email, defender_email = _snow_identity_for_user(root, current_user)

					# ── Resolve group & user sys_id ───────────────────────────
					_snow_inc = _snow.get_incident_by_number(snow_number)
					if not _snow_inc:
						self._write_json(HTTPStatus.OK, {"ok": False, "reason": "INCIDENT_NOT_FOUND", "message": f"ServiceNow ticket {snow_number} not found."})
						return

					_grp_raw = _snow_inc.get("assignment_group") or {}
					_grp_sys_id = (_grp_raw.get("value") or "").strip() if isinstance(_grp_raw, dict) else str(_grp_raw).strip()
					_grp_name   = (_grp_raw.get("display_value") or "").strip() if isinstance(_grp_raw, dict) else _grp_sys_id
					if not _grp_sys_id and fallback_group:
						_fg = _snow.find_group_by_name(fallback_group)
						if _fg:
							_grp_sys_id = _fg[0].get("sys_id", "")
							_grp_name   = _fg[0].get("name", fallback_group)
					if not _grp_sys_id:
						self._write_json(HTTPStatus.OK, {"ok": False, "reason": "NO_ASSIGNMENT_GROUP", "message": "Incident has no Assignment Group in ServiceNow."})
						return

					_members = _snow.get_group_members(_grp_sys_id)
					_member_map = {(m.get("user.email") or "").strip().lower(): (m.get("user.sys_id") or "").strip()
					               for m in _members if m.get("user.email") and m.get("user.sys_id")}
					if snow_email.lower() not in _member_map:
						self._write_json(HTTPStatus.OK, {"ok": False, "reason": "USER_NOT_MEMBER_OF_ASSIGNMENT_GROUP",
						                                 "email": snow_email, "assignment_group": _grp_name,
						                                 "message": f"Assignment denied — {snow_email} is not a member of {_grp_name}."})
						return
					_snow_user_sys_id = _member_map[snow_email.lower()]

					# ── Check 1: Is SNOW ticket already assigned to this user? ─
					_cur_assigned = _snow_inc.get("assigned_to") or {}
					_cur_assigned_sys_id = (_cur_assigned.get("value") or "").strip() if isinstance(_cur_assigned, dict) else str(_cur_assigned).strip()
					_snow_already_mine = (_cur_assigned_sys_id == _snow_user_sys_id)

					# ── Check 2: Is Defender incident already assigned to this user? ─
					try:
						_def_inc = _dc.get_incident(defender_inc_id)
						_def_assigned = (_def_inc.get("assignedTo") or "").strip().lower()
						_defender_already_mine = (_def_assigned == defender_email.lower())
					except Exception:
						_defender_already_mine = False

					# Both already assigned → notify user
					if _snow_already_mine and _defender_already_mine:
						self._write_json(HTTPStatus.OK, {
							"ok": False,
							"reason": "ALREADY_ASSIGNED_BOTH",
							"message": "Both ServiceNow and Defender XDR are already assigned to you.",
						})
						return

					# ── Assign where not yet assigned ─────────────────────────
					_assigned_where = []

					if not _snow_already_mine:
						_inc_sys_raw = _snow_inc.get("sys_id") or {}
						_inc_sys_id  = (_inc_sys_raw.get("value") or str(_inc_sys_raw)).strip() if isinstance(_inc_sys_raw, dict) else str(_inc_sys_raw).strip()
						try:
							_snow.assign_incident(_inc_sys_id, _snow_user_sys_id, group_sys_id=_grp_sys_id)
							_assigned_where.append("ServiceNow")
						except Exception as exc:
							self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": f"ServiceNow assignment failed: {exc}"})
							return

					if not _defender_already_mine:
						try:
							_dc.assign_incident(defender_inc_id, defender_email)
							_assigned_where.append("Defender XDR")
						except Exception as exc:
							_partial = f" (ServiceNow already assigned)" if _snow_already_mine else f" (ServiceNow assigned successfully)"
							self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": f"Defender assignment failed: {exc}{_partial}"})
							return

					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"email": snow_email,
						"message": f"Incident assigned in {' and '.join(_assigned_where)}.",
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/incidents/reassign-to-me":
				current_user = self._require_auth_api()
				if current_user is None:
					return
				if not self._require_permission_api(current_user, "reassign"):
					return
				try:
					import config as _cfg
					import auth.token_manager as _tm
					from clients import servicenow_client as _snow
					from pipeline import assignment_validator as _av
					from clients import defender_client as _dc

					snow_number     = str(payload.get("snow_number") or "").strip()
					defender_inc_id = str(payload.get("defender_incident_id") or "").strip()

					if not snow_number:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "snow_number is required."})
						return
					if not defender_inc_id:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "defender_incident_id is required."})
						return

					fallback_group = str(payload.get("fallback_group_name") or "").strip()
					if not fallback_group:
						_ag_file = root / "data" / "system_settings.json"
						try:
							_ag_data = json.loads(_ag_file.read_text(encoding="utf-8")) if _ag_file.is_file() else {}
							fallback_group = _ag_data.get("default_assignment_group", "")
						except Exception:
							pass

					_load_flask_api_creds()

					# Developer role, and the "Microsoft CDX" demo-tenant identity, use the
					# shared RedFlagged Service ServiceNow account; Defender uses the real MS account.
					snow_email_r, defender_email_r = _snow_identity_for_user(root, current_user)

					# allow_reassign=True skips the "already assigned" check
					result = _av.validate(snow_number, snow_email_r, allow_reassign=True,
					                      fallback_group_name=fallback_group,
					                      require_defender_check=False)
					if not result["eligible"]:
						self._write_json(HTTPStatus.OK, {"ok": False, **result})
						return

					snow_result = _av.perform_assignment(result)
					if not snow_result["ok"]:
						self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
							"ok": False,
							"message": f"ServiceNow reassignment failed: {snow_result.get('error')}",
							**result,
						})
						return

					try:
						_dc.assign_incident(defender_inc_id, defender_email_r)
					except Exception as exc:
						self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
							"ok": False,
							"message": f"ServiceNow reassigned, but Defender assignment failed: {exc}",
							**result,
						})
						return

					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"message": f"Incident reassigned to {result['email']} in ServiceNow and {defender_email_r} in Defender XDR.",
						**result,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/incidents/assign-to-user":
				current_user = self._require_auth_api()
				if current_user is None:
					return
				try:
					from clients import servicenow_client as _snow_au
					from clients import defender_client as _dc_au
					from pipeline import assignment_validator as _av_au

					snow_number     = str(payload.get("snow_number") or "").strip()
					defender_inc_id = str(payload.get("defender_incident_id") or "").strip()
					target_email    = str(payload.get("target_email") or "").strip().lower()

					if not snow_number:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "snow_number is required."})
						return
					if not defender_inc_id:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "defender_incident_id is required."})
						return
					if not target_email:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "target_email is required."})
						return

					fallback_group = ""
					_ag_file2 = root / "data" / "system_settings.json"
					try:
						_ag_data2 = json.loads(_ag_file2.read_text(encoding="utf-8")) if _ag_file2.is_file() else {}
						fallback_group = _ag_data2.get("default_assignment_group", "")
					except Exception:
						pass

					_load_flask_api_creds()

					# Validate and resolve assignment group + target user.
					# If the user is confirmed in SNOW group but not in Graph (e.g. SNOW-only
					# user without an M365 licence), treat it as a warning — ServiceNow
					# assignment is still performed; Defender update is skipped gracefully.
					result = _av_au.validate(snow_number, target_email, allow_reassign=True, fallback_group_name=fallback_group)
					_defender_skipped_msg = ""
					if not result["eligible"]:
						if result.get("reason") == "USER_NOT_FOUND_IN_DEFENDER" and result.get("snow_user_sys_id"):
							# Member confirmed in SNOW group; Defender update will be skipped below.
							result = dict(result, eligible=True)
							_defender_skipped_msg = f"{target_email} is not in Microsoft Graph — SNOW assignment only."
						else:
							self._write_json(HTTPStatus.OK, {"ok": False, **result})
							return

					snow_result = _av_au.perform_assignment(result)
					if not snow_result["ok"]:
						self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
							"ok": False,
							"message": f"ServiceNow assignment failed: {snow_result.get('error')}",
							**result,
						})
						return

					if _defender_skipped_msg:
						self._write_json(HTTPStatus.OK, {
							"ok": True,
							"message": f"Incident assigned to {target_email} in ServiceNow. {_defender_skipped_msg}",
							**result,
						})
						return

					try:
						_dc_au.assign_incident(defender_inc_id, target_email)
					except Exception as exc:
						self._write_json(HTTPStatus.OK, {
							"ok": True,
							"message": f"ServiceNow assigned to {target_email}. Defender update failed: {exc}",
							**result,
						})
						return

					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"message": f"Incident assigned to {target_email} in ServiceNow and Defender XDR.",
						**result,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path.startswith("/api/incidents/") and path.endswith("/resolve"):
				current_user = self._require_auth_api()
				if current_user is None:
					return
				if not self._require_permission_api(current_user, "resolve"):
					return
				incident_id_from_path = path[len("/api/incidents/"):-len("/resolve")]
				if not incident_id_from_path:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Incident ID is required in URL path."})
					return
				try:
					# Gather this incident's ServiceNow ticket sys_ids (parent + children)
					# so resolving the Defender incident also resolves the linked tickets.
					_snow_tickets_csv = ""
					try:
						_rm_file = root / "data" / "snow_incident_map.json"
						_rm_map = json.loads(_rm_file.read_text(encoding="utf-8")) if _rm_file.is_file() else {}
						_rm_entry = _rm_map.get(str(incident_id_from_path)) or {}
						_rm_sys_ids = [sid for sid in [_rm_entry.get("snow_sys_id", "")] if sid]
						_rm_sys_ids += [c.get("sys_id", "") for c in (_rm_entry.get("children") or []) if c.get("sys_id")]
						_snow_tickets_csv = ",".join(_rm_sys_ids)
					except Exception:
						pass
					_snow_caller_email, _ = _snow_identity_for_user(root, current_user)
					started, response_payload = manager.start_incident_resolve_process(
						incident_id_from_path,
						source=str(payload.get("source") or "resolve modal").strip() or "resolve modal",
						status=payload.get("status"),
						classification=payload.get("classification"),
						determination=payload.get("determination"),
						comment=payload.get("comment"),
						assigned_to=payload.get("assignedTo"),
						snow_tickets=_snow_tickets_csv,
						resolution_code=payload.get("resolutionCode"),
						caller_email=_snow_caller_email,
					)
				except ValueError as exc:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": str(exc)})
					return
				status_code = HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT
				self._write_json(status_code, response_payload)
				return

			# Analyst create
			if path == "/api/analysts":
				import uuid as _uuid2
				data_file = root / "data" / "analysts.json"
				try:
					af_data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.is_file() else {"analysts": [], "last_assigned_idx": -1}
					analyst = {
						"id": str(_uuid2.uuid4()),
						"display_name": str(payload.get("display_name") or "").strip(),
						"defender_account": str(payload.get("defender_account") or "").strip(),
						"snow_account": str(payload.get("snow_account") or "").strip(),
						"shift": int(payload.get("shift", 1)),
						"active": bool(payload.get("active", True)),
						"enabled": bool(payload.get("enabled", True)),
					}
					if not analyst["display_name"]:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "display_name is required"})
						return
					af_data.setdefault("analysts", []).append(analyst)
					data_file.write_text(json.dumps(af_data, indent=2), encoding="utf-8")
					self._write_json(HTTPStatus.CREATED, {"ok": True, "analyst": analyst})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Config save
			if path == "/api/config":
				cf = root / "config.json"
				try:
					raw = json.loads(cf.read_text(encoding="utf-8")) if cf.is_file() else {}
					env_name = payload.get("env", "tenant")
					allowed = {"TENANT_ID","CLIENT_ID","CLIENT_SECRET","ANTHROPIC_API_KEY","CACHE_TTL","PORT","SNOW_INSTANCE","SNOW_USER","SNOW_PASSWORD","SNOW_URL","TEAMS_WEBHOOK_URL"}
					if "envs" not in raw:
						raw = {"active_env": "tenant", "envs": {"tenant": {k: raw[k] for k in allowed if k in raw}, "production": {}}}
					env_data = dict(raw["envs"].get(env_name, {}))
					for k, v in payload.items():
						if k in allowed and v is not None:
							if isinstance(v, str) and "…" in v:
								continue
							env_data[k] = v
					raw["envs"][env_name] = env_data
					cf.write_text(json.dumps(raw, indent=2), encoding="utf-8")
					self._write_json(HTTPStatus.OK, {"ok": True, "message": f"Environment '{env_name}' saved."})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Config switch env
			if path == "/api/config/switch-env":
				cf = root / "config.json"
				try:
					env_name = payload.get("env")
					if not env_name:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "No env specified."})
						return
					raw = json.loads(cf.read_text(encoding="utf-8")) if cf.is_file() else {}
					if "envs" not in raw or env_name not in raw.get("envs", {}):
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": f"Environment '{env_name}' not found."})
						return
					raw["active_env"] = env_name
					cf.write_text(json.dumps(raw, indent=2), encoding="utf-8")
					self._write_json(HTTPStatus.OK, {"ok": True, "message": f"Switched to '{env_name}' environment."})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Test Graph credentials
			if path == "/api/config/test-graph":
				try:
					import requests as _req
					tenant = payload.get("TENANT_ID", "")
					client = payload.get("CLIENT_ID", "")
					secret = payload.get("CLIENT_SECRET", "")
					if not all([tenant, client, secret]):
						import config as _cfg2
						try: _cfg2.load()
						except Exception: pass
						tenant = tenant or getattr(_cfg2, "TENANT_ID", "")
						client = client or getattr(_cfg2, "CLIENT_ID", "")
						secret = secret or getattr(_cfg2, "CLIENT_SECRET", "")
					r = _req.post(
						f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
						data={"client_id": client, "client_secret": secret, "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
						timeout=15,
					)
					if r.ok and r.json().get("access_token"):
						self._write_json(HTTPStatus.OK, {"ok": True, "message": "Microsoft Graph token obtained successfully."})
					else:
						self._write_json(HTTPStatus.OK, {"ok": False, "message": r.json().get("error_description", r.text[:200])})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Test ServiceNow
			if path == "/api/snow-test":
				try:
					import requests as _req
					instance = str(payload.get("instance") or "").strip()
					user = str(payload.get("user") or "").strip()
					password = str(payload.get("password") or "").strip()
					if not all([instance, user, password]):
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "instance, user and password are required"})
						return
					base = (instance if instance.startswith("http") else f"https://{instance}").rstrip("/")
					r = _req.get(f"{base}/api/now/table/incident?sysparm_limit=1&sysparm_fields=number", auth=(user, password), headers={"Accept": "application/json"}, timeout=15)
					if r.status_code == 401:
						self._write_json(HTTPStatus.OK, {"ok": False, "message": "Authentication failed — check username and password."})
					elif not r.ok:
						self._write_json(HTTPStatus.OK, {"ok": False, "message": f"ServiceNow returned {r.status_code}"})
					else:
						self._write_json(HTTPStatus.OK, {"ok": True})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Test Teams webhook
			if path == "/api/config/test-teams":
				try:
					import requests as _req
					webhook_url = str(payload.get("TEAMS_WEBHOOK_URL") or "").strip()
					if not webhook_url:
						self._write_json(HTTPStatus.OK, {"ok": False, "message": "No webhook URL provided."})
						return
					test_card = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": [{"type": "TextBlock", "text": "RedFlagged webhook test — OK", "wrap": True}]}}]}
					r = _req.post(webhook_url, json=test_card, headers={"Content-Type": "application/json"}, timeout=15)
					if r.status_code in (200, 202):
						self._write_json(HTTPStatus.OK, {"ok": True, "message": "Test notification sent. Check your Teams channel."})
					else:
						self._write_json(HTTPStatus.OK, {"ok": False, "message": f"Webhook returned {r.status_code}: {r.text[:200]}"})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Report export
			if path == "/api/report/export-data":
				try:
					import io as _io
					import openpyxl as _xl
					from openpyxl.styles import Font as _Font, PatternFill as _Fill, Alignment as _Align
					from openpyxl.utils import get_column_letter as _gcl
					rows_data = payload.get("rows", [])
					columns   = payload.get("columns", [])
					wb = _xl.Workbook()
					ws = wb.active
					ws.title = "DLP Incident Report"
					hdr_fill = _Fill(fill_type="solid", fgColor="1E293B")
					hdr_font = _Font(bold=True, color="F1F5F9", size=9)
					ws.cell(row=1, column=1, value="#").fill = hdr_fill
					ws.cell(row=1, column=1).font = hdr_font
					for ci, col in enumerate(columns, 2):
						cell = ws.cell(row=1, column=ci, value=col)
						cell.fill = hdr_fill; cell.font = hdr_font
						cell.alignment = _Align(wrap_text=True, vertical="center")
						ws.column_dimensions[_gcl(ci)].width = 20
					ws.column_dimensions["A"].width = 6
					ws.row_dimensions[1].height = 50
					ws.freeze_panes = "B2"
					for ri, row in enumerate(rows_data, 2):
						ws.cell(row=ri, column=1, value=ri - 1).font = _Font(size=9, bold=True)
						for ci, col in enumerate(columns, 2):
							cell = ws.cell(row=ri, column=ci, value=row.get(col, ""))
							cell.alignment = _Align(wrap_text=True, vertical="top")
							cell.font = _Font(size=9)
					buf = _io.BytesIO()
					wb.save(buf); buf.seek(0)
					fname = f"redflagged_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
					content = buf.read()
					self.send_response(HTTPStatus.OK)
					self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
					self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
					self.send_header("Content-Length", str(len(content)))
					self.end_headers()
					self.wfile.write(content)
				except ImportError:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "openpyxl not installed"})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			if path == "/api/users/create":
				user = self._require_auth_api()
				if user is None:
					return
				if not self._require_permission_api(user, "configure"):
					return
				email        = str(payload.get("email") or "").strip().lower()
				display_name = str(payload.get("display_name") or "").strip()
				role         = str(payload.get("role") or "analyst").strip()
				password     = str(payload.get("password") or "")
				if not email:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Email is required."})
					return
				if not display_name:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Display name is required."})
					return
				if role not in {"admin", "analyst", "developer"}:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Role must be admin, analyst, or developer."})
					return
				if len(password) < 6:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Password must be at least 6 characters."})
					return
				users_file = root / "data" / "users.json"
				try:
					existing = json.loads(users_file.read_text(encoding="utf-8")).get("users", []) if users_file.is_file() else []
					if any(u.get("email", "").strip().lower() == email for u in existing):
						self._write_json(HTTPStatus.CONFLICT, {"ok": False, "message": f"User {email!r} already exists."})
						return
					try:
						from argon2 import PasswordHasher as _PH
						password_hash = _PH().hash(password)
					except ImportError:
						self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": "argon2-cffi not installed on server."})
						return
					import uuid as _uuid
					new_user = {
						"id": f"user-{_uuid.uuid4().hex[:8].upper()}",
						"email": email,
						"display_name": display_name,
						"password_hash": password_hash,
						"role": role,
						"active": True,
					}
					existing.append(new_user)
					users_file.parent.mkdir(parents=True, exist_ok=True)
					users_file.write_text(json.dumps({"users": existing}, indent=2) + "\n", encoding="utf-8")
					safe = {k: v for k, v in new_user.items() if k != "password_hash"}
					self._write_json(HTTPStatus.CREATED, {"ok": True, "user": safe})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/users/update-role":
				user = self._require_auth_api()
				if user is None:
					return
				users_file = root / "data" / "users.json"
				try:
					existing = json.loads(users_file.read_text(encoding="utf-8")).get("users", []) if users_file.is_file() else []
				except Exception:
					existing = []
				_has_admin = any(u.get("role") == "admin" and u.get("active", True) for u in existing)
				# Allow without configure permission only when: no admin exists (bootstrap) AND user is promoting themselves to admin
				_is_bootstrap = (not _has_admin) and (str(payload.get("email") or "").strip().lower() == (user.email or "").strip().lower()) and str(payload.get("role") or "") == "admin"
				if not _is_bootstrap and not self._require_permission_api(user, "configure"):
					return
				email = str(payload.get("email") or "").strip().lower()
				role  = str(payload.get("role") or "").strip()
				if not email:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Email is required."})
					return
				if role not in {"admin", "analyst", "developer"}:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Role must be admin, analyst, or developer."})
					return
				try:
					matched = next((u for u in existing if u.get("email", "").strip().lower() == email), None)
					if not matched:
						self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": f"User '{email}' not found."})
						return
					matched["role"] = role
					users_file.write_text(json.dumps({"users": existing}, indent=2) + "\n", encoding="utf-8")
					self._write_json(HTTPStatus.OK, {"ok": True})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/users/snow-assignment":
				user = self._require_auth_api()
				if user is None:
					return
				if not self._require_permission_api(user, "configure"):
					return
				email = str(payload.get("email") or "").strip().lower()
				snow_assignee = str(payload.get("snow_assignee") or "").strip()
				if not email:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Email is required."})
					return
				try:
					mapping = _load_snow_user_map(root)
					if snow_assignee:
						mapping[email] = snow_assignee
					else:
						mapping.pop(email, None)
					_save_snow_user_map(root, mapping)
					self._write_json(HTTPStatus.OK, {"ok": True})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			if path == "/api/users/toggle-active":
				user = self._require_auth_api()
				if user is None:
					return
				if not self._require_permission_api(user, "configure"):
					return
				email = str(payload.get("email") or "").strip().lower()
				active = payload.get("active")
				if not email or active is None:
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "email and active required."})
					return
				users_file = root / "data" / "users.json"
				try:
					existing = json.loads(users_file.read_text(encoding="utf-8")).get("users", []) if users_file.is_file() else []
					matched = next((u for u in existing if u.get("email", "").strip().lower() == email), None)
					if not matched:
						self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": f"User '{email}' not found."})
						return
					matched["active"] = bool(active)
					users_file.write_text(json.dumps({"users": existing}, indent=2) + "\n", encoding="utf-8")
					self._write_json(HTTPStatus.OK, {"ok": True, "active": bool(active)})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			# Remove sharing links from a SharePoint file (policy action)
			if path == "/api/incidents/restrict-sharing":
				try:
					_load_flask_api_creds()
					import auth.token_manager as _tm_rs
					_tok_rs = _tm_rs.get_graph_token()
					_hdrs_rs = {"Authorization": f"Bearer {_tok_rs}"}
					import requests as _req_rs
					from urllib.parse import urlparse as _up_rs, unquote as _uq_rs
					_body_rs = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
					_alert_id_rs = _body_rs.get("alert_id", "")
					_file_url_rs = _body_rs.get("file_url", "")
					_filename_rs = _body_rs.get("filename", "")

					# If no file_url supplied, fetch from alert evidence
					if not _file_url_rs and _alert_id_rs:
						from clients import purview_client as _pc_rs
						_ev_rs = _pc_rs.get_alert_evidence(_alert_id_rs)
						for _item_rs in (_ev_rs.get("evidence") or []):
							_t_rs = (_item_rs.get("@odata.type") or "").lower()
							if "fileevidence" in _t_rs:
								_fd_rs = _item_rs.get("fileDetails") or _item_rs
								_file_url_rs = _fd_rs.get("filePath") or _fd_rs.get("fileUrl") or ""
								if not _filename_rs:
									_filename_rs = _fd_rs.get("fileName") or ""
								if _file_url_rs:
									break

					if not _file_url_rs:
						self._write_json(HTTPStatus.OK, {"ok": False, "error": "No file URL found for this alert."})
						return

					# Resolve drive item ID from the file URL
					_pu_rs = _up_rs(_file_url_rs)
					_host_rs = _pu_rs.netloc
					_pp_rs = [p for p in _pu_rs.path.split("/") if p]
					_item_id_rs = ""
					_drive_id_rs = ""

					if "sharepoint.com" in _host_rs and len(_pp_rs) >= 3 and _pp_rs[0] == "sites":
						_site_name_rs = _pp_rs[1]
						_lib_name_rs = _uq_rs(_pp_rs[2])
						_within_rs = [_uq_rs(p) for p in _pp_rs[3:]]
						if _filename_rs:
							_fn_rs = _uq_rs(_filename_rs)
							if not _within_rs or _within_rs[-1].lower() != _fn_rs.lower():
								_within_rs.append(_fn_rs)
						_within_path_rs = "/".join(_within_rs)

						_r_site_rs = _req_rs.get(
							f"https://graph.microsoft.com/v1.0/sites/{_host_rs}:/sites/{_site_name_rs}",
							headers=_hdrs_rs, timeout=15,
						)
						if _r_site_rs.ok:
							_site_id_rs = _r_site_rs.json().get("id", "")
							# Find the drive — try drives list then lists by name
							_r_drives_rs = _req_rs.get(
								f"https://graph.microsoft.com/v1.0/sites/{_site_id_rs}/drives",
								headers=_hdrs_rs, timeout=15,
							)
							_lib_clean_rs = _lib_name_rs.lower().replace(" ", "")
							if _r_drives_rs.ok:
								for _drv_rs in _r_drives_rs.json().get("value", []):
									_dc_rs = (_drv_rs.get("name") or "").lower().replace(" ", "")
									if _dc_rs == _lib_clean_rs or (
										_lib_clean_rs in ("shareddocuments", "documents") and _dc_rs in ("documents", "shareddocuments")
									):
										_drive_id_rs = _drv_rs.get("id", "")
										break
							if not _drive_id_rs:
								import re as _re_rs
								_lib_spaced_rs = _re_rs.sub(r'(?<=[a-z])(?=[A-Z])', ' ', _lib_name_rs)
								for _ln_try_rs in ([_lib_name_rs] if _lib_spaced_rs == _lib_name_rs else [_lib_name_rs, _lib_spaced_rs]):
									_r_ld_rs = _req_rs.get(
										f"https://graph.microsoft.com/v1.0/sites/{_site_id_rs}/lists/{_ln_try_rs}/drive",
										headers=_hdrs_rs, timeout=15,
									)
									if _r_ld_rs.ok:
										_drive_id_rs = _r_ld_rs.json().get("id", "")
										break

							if _drive_id_rs and _within_path_rs:
								_r_item_rs = _req_rs.get(
									f"https://graph.microsoft.com/v1.0/drives/{_drive_id_rs}/root:/{_within_path_rs}",
									headers=_hdrs_rs, timeout=15,
								)
								if _r_item_rs.ok:
									_item_id_rs = _r_item_rs.json().get("id", "")

					if not _drive_id_rs or not _item_id_rs:
						self._write_json(HTTPStatus.OK, {"ok": False, "error": "Could not locate the file in SharePoint to restrict sharing."})
						return

					# List all permissions on the item
					_r_perms_rs = _req_rs.get(
						f"https://graph.microsoft.com/v1.0/drives/{_drive_id_rs}/items/{_item_id_rs}/permissions",
						headers=_hdrs_rs, timeout=15,
					)
					if not _r_perms_rs.ok:
						self._write_json(HTTPStatus.OK, {"ok": False, "error": f"Could not list file permissions: {_r_perms_rs.status_code}"})
						return

					_perms_rs = _r_perms_rs.json().get("value", [])
					_removed_rs = []
					_failed_rs = []
					for _perm_rs in _perms_rs:
						# Only remove sharing link permissions (not direct user grants)
						if _perm_rs.get("link"):
							_perm_id_rs = _perm_rs.get("id", "")
							_r_del_rs = _req_rs.delete(
								f"https://graph.microsoft.com/v1.0/drives/{_drive_id_rs}/items/{_item_id_rs}/permissions/{_perm_id_rs}",
								headers=_hdrs_rs, timeout=15,
							)
							if _r_del_rs.status_code in (200, 204):
								_removed_rs.append(_perm_id_rs)
							else:
								_failed_rs.append(_perm_id_rs)

					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"removed_count": len(_removed_rs),
						"failed_count": len(_failed_rs),
						"total_permissions": len(_perms_rs),
						"message": f"Removed {len(_removed_rs)} sharing link(s). {len(_failed_rs)} failed.",
					})
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# Create ServiceNow tickets for a Defender incident
			if path == "/api/snow-create-for-incident":
				current_user = self._require_auth_api()
				if current_user is None:
					return
				if not self._require_permission_api(current_user, "resolve"):
					return
				try:
					_load_flask_api_creds()
					import config as _cfg_snow_guard
					if not _cfg_snow_guard.SNOW_INSTANCE_URL:
						self._write_json(HTTPStatus.OK, {"ok": False, "error": "ServiceNow URL not configured. Set SNOW_INSTANCE_URL (or SNOW_INSTANCE) in Render environment variables, or configure it in Developer Config."})
						return
					import json as _json_sc
					from clients import defender_client as _dc_sc
					from clients import servicenow_client as _snow_sc
					inc_id = str(payload.get("inc_id") or "").strip()
					if not inc_id:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "inc_id required"})
						return
					# Fetch Defender incident + alerts (expand so child alert list is populated)
					inc = _dc_sc.get_incident(inc_id, expand_alerts=True)
					title = inc.get("displayName") or inc.get("title") or f"Defender Incident {inc_id}"
					severity = (inc.get("severity") or "low").lower()
					urgency = {"high": 1, "medium": 2, "low": 3}.get(severity, 2)
					alerts = inc.get("alerts") or []
					# _load_flask_api_creds() already initialised servicenow_client with env-var fallback
					ss_file2 = root / "data" / "system_settings.json"
					ss2 = _json_sc.loads(ss_file2.read_text(encoding="utf-8")) if ss_file2.is_file() else {}
					grp_name = ss2.get("default_assignment_group", "")
					grp_sys_id = ""
					if grp_name:
						groups = _snow_sc.find_group_by_name(grp_name)
						if groups:
							grp_sys_id = groups[0]["sys_id"]
					# Caller — the RedFlagged session user who clicked "Create Tickets"
					# (developer-role / "Microsoft CDX" sessions use the shared RedFlagged
					# Service ServiceNow identity, same mapping used by Resolve/Assign).
					_caller_email, _ = _snow_identity_for_user(root, current_user)
					_caller_sys_id = ""
					if _caller_email:
						_caller_rec = _snow_sc.find_user_by_email(_caller_email)
						_caller_sys_id = _caller_rec.get("sys_id", "")
					# Create parent ticket — represented using the first alert's policy/user
					# (correlated DLP alerts in one incident are typically the same policy)
					_parent_policy, _parent_user = _snow_alert_policy_and_user(alerts[0]) if alerts else ("", "")
					_parent_detected = alerts[0].get("createdDateTime", "") if alerts else inc.get("createdDateTime", "")
					desc = _snow_incident_description(_parent_policy, _parent_detected, severity, _parent_user)
					parent = _snow_sc.create_incident(
						short_description=f"Data Security DLP incident remediation - #{inc_id}",
						description=desc,
						urgency=urgency,
						impact=urgency,
						assignment_group_sys_id=grp_sys_id,
						caller_sys_id=_caller_sys_id,
					)
					parent_num = parent.get("number", "")
					parent_sys_id = parent.get("sys_id", "")
					# Create child tickets per alert
					children_list = []
					for alert in alerts:
						a_policy, a_user = _snow_alert_policy_and_user(alert)
						a_desc = _snow_incident_description(a_policy, alert.get("createdDateTime", ""), alert.get("severity", severity), a_user)
						child = _snow_sc.create_incident(
							short_description=f"Data Security DLP incident remediation - #{inc_id}",
							description=a_desc,
							urgency=urgency,
							impact=urgency,
							assignment_group_sys_id=grp_sys_id,
							caller_sys_id=_caller_sys_id,
						)
						if child.get("sys_id") and parent_sys_id:
							_snow_sc.link_incidents(parent_sys_id, child["sys_id"])
						if child.get("number"):
							children_list.append({"ticket": child["number"], "sys_id": child.get("sys_id", ""), "alert_id": alert.get("id", "")})
					# Persist mapping so snow-details can look it up
					_map_file = root / "data" / "snow_incident_map.json"
					_map = {}
					if _map_file.is_file():
						try: _map = _json_sc.loads(_map_file.read_text(encoding="utf-8"))
						except Exception: pass
					_map[str(inc_id)] = {
						"snow_number": parent_num,
						"snow_sys_id": parent_sys_id,
						"children_created": len(children_list),
						"children": children_list,
					}
					_map_file.write_text(_json_sc.dumps(_map, indent=2), encoding="utf-8")
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"parent_ticket": parent_num,
						"parent_sys_id": parent_sys_id,
						"children_created": len(children_list),
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			# "Add to Database" — persist the parent incident's ServiceNow ticket
			# structure (one row per alert) so the Report tab can read it back
			# without hitting Graph/ServiceNow live.
			if path == "/api/snow-add-to-database":
				try:
					import json as _json_atd
					inc_id = str(payload.get("inc_id") or "").strip()
					if not inc_id:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "inc_id required"})
						return
					_map_file_atd = root / "data" / "snow_incident_map.json"
					_map_atd = {}
					if _map_file_atd.is_file():
						try: _map_atd = _json_atd.loads(_map_file_atd.read_text(encoding="utf-8"))
						except Exception: pass
					entry_atd = _map_atd.get(str(inc_id))
					if not entry_atd or not entry_atd.get("snow_number"):
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "No ServiceNow tickets exist for this incident yet. Use 'Create Tickets' first."})
						return
					parent_ticket = entry_atd.get("snow_number", "")
					parent_sys_id = entry_atd.get("snow_sys_id", "")
					children_atd = entry_atd.get("children") or []
					con = _snow_report_db_connect(root)
					try:
						rows_written = 0
						now_iso = datetime.now().astimezone().isoformat()
						# One row for the parent ticket itself (no alert_id of its own —
						# a synthetic per-incident key keeps it unique in this alert_id-keyed table).
						parent_row_key = f"__parent__{inc_id}"
						con.execute(
							"""
							INSERT INTO snow_report_tickets
								(alert_id, incident_id, snow_ticket, snow_sys_id, snow_parent_ticket, snow_parent_sys_id, ticket_type, added_at)
							VALUES (?, ?, ?, ?, ?, ?, 'parent', ?)
							ON CONFLICT(alert_id) DO UPDATE SET
								incident_id        = excluded.incident_id,
								snow_ticket         = excluded.snow_ticket,
								snow_sys_id         = excluded.snow_sys_id,
								snow_parent_ticket  = excluded.snow_parent_ticket,
								snow_parent_sys_id  = excluded.snow_parent_sys_id,
								ticket_type         = 'parent',
								added_at            = excluded.added_at
							""",
							(parent_row_key, str(inc_id), parent_ticket, parent_sys_id, parent_ticket, parent_sys_id, now_iso),
						)
						rows_written += 1
						for child in children_atd:
							alert_id = child.get("alert_id") or ""
							if not alert_id:
								continue
							con.execute(
								"""
								INSERT INTO snow_report_tickets
									(alert_id, incident_id, snow_ticket, snow_sys_id, snow_parent_ticket, snow_parent_sys_id, ticket_type, added_at)
								VALUES (?, ?, ?, ?, ?, ?, 'child', ?)
								ON CONFLICT(alert_id) DO UPDATE SET
									incident_id        = excluded.incident_id,
									snow_ticket         = excluded.snow_ticket,
									snow_sys_id         = excluded.snow_sys_id,
									snow_parent_ticket  = excluded.snow_parent_ticket,
									snow_parent_sys_id  = excluded.snow_parent_sys_id,
									ticket_type         = 'child',
									added_at            = excluded.added_at
								""",
								(alert_id, str(inc_id), child.get("ticket", ""), child.get("sys_id", ""), parent_ticket, parent_sys_id, now_iso),
							)
							rows_written += 1
						con.commit()
					finally:
						con.close()
					self._write_json(HTTPStatus.OK, {
						"ok": True,
						"parent_ticket": parent_ticket,
						"rows_written": rows_written,
					})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			# Go Hunt — run KQL Advanced Hunting query via Graph API
			if path == "/api/go-hunt":
				try:
					from clients import defender_client as _dc_gh
					kql = str(payload.get("query") or "").strip()
					if not kql:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "query is required."})
						return
					_load_flask_api_creds()
					result_gh = _dc_gh.run_hunting_query(kql)
					self._write_json(HTTPStatus.OK, result_gh)
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# POST routes proxied to Flask server (ai-chat only)
			_FLASK_POST_ROUTES = {"/api/ai-chat"}
			if path in _FLASK_POST_ROUTES:
				try:
					import requests as _req
					r = _req.post(f"{_FLASK_BASE}{path}", json=payload, timeout=60)
					self._write_json(HTTPStatus.OK if r.ok else HTTPStatus(r.status_code), r.json())
				except Exception as exc:
					self._write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
				return

			# SIT analysis — start job
			if path.startswith("/api/dlp-alerts/") and path.endswith("/analyze"):
				parts = path.split("/")
				try:
					a_id = parts[parts.index("dlp-alerts") + 1]
				except (ValueError, IndexError):
					self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid path."})
					return
				alert = payload.get("alert") or {}
				with _analysis_lock:
					existing_job = next(
						(j for j in _analysis_jobs.values()
						 if j.get("alert_id") == a_id and j.get("status") == "running"),
						None
					)
					existing_id = existing_job["id"] if existing_job else None
				if existing_id:
					self._write_json(HTTPStatus.OK, {"ok": True, "job_id": existing_id,
													 "status": "running", "existing": True})
					return
				import uuid as _uuid
				job_id = str(_uuid.uuid4())[:8]
				with _analysis_lock:
					_analysis_jobs[job_id] = {"id": job_id, "alert_id": a_id, "status": "running",
											   "stage": "Starting analysis", "stage_index": 0}
				threading.Thread(target=_run_analysis_job, args=(job_id, alert, a_id), daemon=True).start()
				self._write_json(HTTPStatus.OK, {"ok": True, "job_id": job_id, "status": "running"})
				return

			# Story — build narrative from alert data
			if path.startswith("/api/dlp-alerts/") and path.endswith("/story"):
				alert = payload.get("alert") or {}
				try:
					story = _build_exfiltration_story(alert)
					self._write_json(HTTPStatus.OK, {"ok": True, "story": story})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			# Story hunt — run one of the 4 KQL query types
			if path.startswith("/api/dlp-alerts/") and path.endswith("/story-hunt"):
				import requests as _req  # noqa: F811 — needed due to local shadowing in this method
				try:
					user_upn   = str(payload.get("user_upn") or "").strip()
					event_time = str(payload.get("event_time_iso") or "").strip()
					workload   = str(payload.get("workload") or "").strip().upper()
					window_min = int(payload.get("window_minutes") or 30)
					hunt_type  = str(payload.get("hunt_type") or "file_activities").strip()
					kql = _build_story_hunt_kql(user_upn, event_time, workload, window_min, hunt_type)
					if not kql:
						self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid hunt_type or missing event_time_iso."})
						return
					_load_flask_api_creds()
					from clients import defender_client as _dc_sh
					gh = _dc_sh.run_hunting_query(kql)
					if gh.get("ok"):
						self._write_json(HTTPStatus.OK, {
							"ok": True, "results": gh.get("results") or [],
							"schema": gh.get("schema") or [], "kql": kql, "hunt_type": hunt_type,
						})
					else:
						self._write_json(HTTPStatus.OK, {"ok": False, "error": gh.get("error", "Query failed"), "kql": kql})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
				return

			self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Route not found."})

		def do_PUT(self) -> None:
			path = urlparse(self.path).path
			try:
				payload = parse_request_body(self)
			except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
				self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Request body must be valid JSON."})
				return

			# Analyst update: PUT /api/analysts/<id>
			if path.startswith("/api/analysts/"):
				analyst_id = path[len("/api/analysts/"):]
				data_file = root / "data" / "analysts.json"
				try:
					af_data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.is_file() else {"analysts": []}
					updated = None
					for a in af_data.get("analysts", []):
						if a["id"] == analyst_id:
							for field in ("display_name", "defender_account", "snow_account"):
								if field in payload:
									a[field] = str(payload[field] or "").strip()
							if "shift" in payload:
								a["shift"] = int(payload["shift"])
							if "active" in payload:
								a["active"] = bool(payload["active"])
							if "enabled" in payload:
								a["enabled"] = bool(payload["enabled"])
							updated = a
							break
					if updated is None:
						self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Analyst not found"})
						return
					data_file.write_text(json.dumps(af_data, indent=2), encoding="utf-8")
					self._write_json(HTTPStatus.OK, {"ok": True, "analyst": updated})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Route not found."})

		def do_DELETE(self) -> None:
			path = urlparse(self.path).path

			# Analyst delete: DELETE /api/analysts/<id>
			if path.startswith("/api/analysts/"):
				analyst_id = path[len("/api/analysts/"):]
				data_file = root / "data" / "analysts.json"
				try:
					af_data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.is_file() else {"analysts": []}
					before = len(af_data.get("analysts", []))
					af_data["analysts"] = [a for a in af_data.get("analysts", []) if a["id"] != analyst_id]
					if len(af_data["analysts"]) == before:
						self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Analyst not found"})
						return
					data_file.write_text(json.dumps(af_data, indent=2), encoding="utf-8")
					self._write_json(HTTPStatus.OK, {"ok": True})
				except Exception as exc:
					self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
				return

			self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "message": "Route not found."})

	return DashboardHandler


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Serve the RedFlagged dashboard and automation control API.")
	parser.add_argument("--host", default=DEFAULT_HOST, help="Host interface to bind (default: 127.0.0.1).")
	parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port to bind (default: 8765).")
	return parser.parse_args()


def main() -> int:
	global _LOCAL_BASE
	args = parse_args()
	_LOCAL_BASE = f"http://127.0.0.1:{args.port}"
	root = workspace_root()
	manager = AutomationManager(root)

	# Auth service setup
	import os as _os, json as _json, uuid as _uuid
	auth_provider_type = _os.environ.get("AUTH_PROVIDER", "json").strip().lower()
	if auth_provider_type == "json":
		from auth.json_auth_provider import JsonAuthenticationProvider
		from auth.auth_service import AuthService
		users_path = root / "data" / "users.json"
		# Bootstrap default admin if no users file exists (e.g. first Render deploy)
		if not users_path.exists():
			users_path.parent.mkdir(parents=True, exist_ok=True)
			admin_email    = _os.environ.get("ADMIN_EMAIL",    "admin")
			admin_password = _os.environ.get("ADMIN_PASSWORD", "123admin")
			try:
				from argon2 import PasswordHasher as _PH
				_ph = _PH(memory_cost=65536, time_cost=3, parallelism=4)
				pw_hash = _ph.hash(admin_password)
			except Exception:
				pw_hash = admin_password  # fallback if argon2 unavailable
			users_path.write_text(_json.dumps({
				"users": [{
					"id": f"user-{_uuid.uuid4().hex[:8].upper()}",
					"email": admin_email,
					"display_name": "Admin",
					"password_hash": pw_hash,
					"role": "admin",
					"active": True,
				}]
			}, indent=2), encoding="utf-8")
			print(f"[Dashboard] Created default admin user '{admin_email}'.")
		# Seed ADMIN_EMAILS env var into users.json with admin role on every startup
		_admin_emails_env = _os.environ.get("ADMIN_EMAILS", "").strip()
		if _admin_emails_env and users_path.exists():
			_admin_emails_list = [e.strip().lower() for e in _admin_emails_env.split(",") if e.strip()]
			try:
				_udata = _json.loads(users_path.read_text(encoding="utf-8"))
				_users = _udata.get("users", [])
				_changed = False
				for _ae in _admin_emails_list:
					_eu = next((u for u in _users if u.get("email", "").strip().lower() == _ae), None)
					if _eu:
						if _eu.get("role") != "admin":
							_eu["role"] = "admin"
							_changed = True
					else:
						_users.append({"id": f"ms-{_uuid.uuid4().hex[:8]}", "email": _ae, "display_name": _ae.split("@")[0], "role": "admin", "active": True})
						_changed = True
				if _changed:
					_udata["users"] = _users
					users_path.write_text(_json.dumps(_udata, indent=2), encoding="utf-8")
					print(f"[Dashboard] Seeded ADMIN_EMAILS as admin: {_admin_emails_list}")
			except Exception as _e:
				print(f"[Dashboard] Failed to seed ADMIN_EMAILS: {_e}")
		_auth_service = AuthService(JsonAuthenticationProvider(users_path))
	else:
		raise ValueError(f"Unknown AUTH_PROVIDER: {auth_provider_type!r}")

	handler_cls = make_handler(manager, root, _auth_service)
	server = ThreadingHTTPServer((args.host, args.port), handler_cls)
	_start_keepalive(_FLASK_BASE)
	print(f"[Dashboard] Serving dashboard and API at http://{args.host}:{args.port}/")
	print(f"[Dashboard] v2-dlp-debug: DLN patterns + email-body debug active")
	try:
		server.serve_forever()
	except KeyboardInterrupt:
		print("\n[Dashboard] Shutting down server.")
	finally:
		server.server_close()
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
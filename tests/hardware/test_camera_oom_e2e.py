"""Exercise the running scanner over HTTP, with real CMA allocation failures.

Run with this directory's pytest.ini; application fixtures must not be loaded.
See README.md for the opt-in flag, privileges, and artifact locations.
"""

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import pytest


pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("OPENSCAN_HARDWARE_E2E") != "1",
        reason="Set OPENSCAN_HARDWARE_E2E=1 on a Pi with a real camera to run this test",
    ),
]

CMA_HEAP = Path("/dev/dma_heap/linux,cma")
PROBE_TIMEOUT = 2.0


def memory_state():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemAvailable", "CmaTotal", "CmaFree"}:
            values[key] = int(value.split()[0])
    return values


@contextmanager
def reserve_cma(reserve_kib, record):
    """Hold DMA buffers until the photo returns; release every buffer even on failure."""
    import fcntl

    allocation_ioctl = (3 << 30) | (24 << 16) | (ord("H") << 8)
    buffers = []
    allocated_kib = 0
    try:
        heap = os.open(CMA_HEAP, os.O_RDONLY | os.O_CLOEXEC)
        try:
            for _ in range(900):
                remaining = memory_state()["CmaFree"] - reserve_kib
                if remaining <= 0:
                    break
                size_kib = min(1024, remaining)
                allocation = bytearray(struct.pack("=QIIQ", size_kib * 1024, 0, os.O_CLOEXEC, 0))
                fcntl.ioctl(heap, allocation_ioctl, allocation, True)
                buffers.append(struct.unpack("=QIIQ", allocation)[1])
                allocated_kib += size_kib
        finally:
            os.close(heap)
        record("cma_reserved", allocated_kib=allocated_kib, **memory_state())
        assert buffers, "No CMA pressure was applied; this would not be an OOM test"
        assert memory_state()["CmaFree"] <= reserve_kib + 1024, "CMA pressure did not reach its target"
        yield
    finally:
        for buffer_fd in buffers:
            os.close(buffer_fd)
        record("cma_released", released_kib=allocated_kib, **memory_state())


class Scanner:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.base_url = os.environ.get("OPENSCAN_E2E_BASE_URL", "http://127.0.0.1/api/latest").rstrip("/")
        self.camera = os.environ.get("OPENSCAN_E2E_CAMERA", "arducam_64mp")
        self.unit = os.environ.get("OPENSCAN_E2E_SERVICE", "openscan3.service")
        self.camera_path = f"/cameras/{quote(self.camera, safe='')}"
        self.sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]
        self.started = datetime.now().astimezone().isoformat(timespec="seconds")

    def record(self, stage, **details):
        event = {"time": datetime.now().astimezone().isoformat(), "stage": stage, **details}
        line = json.dumps(event, ensure_ascii=False)
        with (self.artifacts / "events.jsonl").open("a") as output:
            output.write(line + "\n")
        print(line, flush=True)

    def get(self, path, timeout):
        return self.request(path, timeout)

    def request(self, path, timeout, method="GET"):
        try:
            response = urlopen(Request(self.base_url + path, method=method), timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            if "json" in response.headers.get("Content-Type", ""):
                body = json.loads(raw)
            elif response.headers.get("Content-Type", "").startswith("image/"):
                body = {"bytes_received": len(raw)}
            else:
                body = raw.decode(errors="replace")
            return response.status, body

    def probes(self, stage):
        results = {}
        for path in ("/device/info", self.camera_path, "/tasks/"):
            started = time.monotonic()
            try:
                status, body = self.get(path, timeout=PROBE_TIMEOUT)
                result = {"status": status}
                if path == self.camera_path:
                    result["busy"] = body.get("busy") if isinstance(body, dict) else None
            except (OSError, ValueError) as error:
                result = {"status": None, "error": f"{type(error).__name__}: {error}"}
            results[path] = {**result, "seconds": time.monotonic() - started}
        self.record(stage, probes=results, **memory_state())
        return results

    def pid(self):
        return int(subprocess.check_output(
            ["systemctl", "show", self.unit, "--property=MainPID", "--value"], text=True
        ).strip())

    def restart(self):
        try:
            subprocess.run(self.sudo + ["systemctl", "restart", self.unit], check=True, timeout=15)
        except subprocess.TimeoutExpired:
            state = subprocess.run(
                ["systemctl", "is-active", self.unit], capture_output=True, text=True
            ).stdout.strip()
            if state == "deactivating":
                pid = self.pid()
                assert pid > 1, "Cannot identify the stuck service process"
                self.record("force_service_stop", pid=pid)
                subprocess.run(self.sudo + ["kill", "-KILL", str(pid)], check=True, timeout=5)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if self.get("/device/info", timeout=PROBE_TIMEOUT)[0] == 200:
                    self.record("service_ready", pid=self.pid())
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        pytest.fail("Scanner API did not become ready after service restart")

    def diagnostics(self, subdirectory=None):
        artifacts = self.artifacts / subdirectory if subdirectory else self.artifacts
        artifacts.mkdir(parents=True, exist_ok=True)
        commands = {
            "service.txt": ["systemctl", "show", self.unit, "-p", "ActiveState", "-p", "MainPID", "-p", "NRestarts"],
            "journal.txt": ["journalctl", "-u", self.unit, "--since", self.started, "--no-pager"],
            "threads.txt": ["ps", "-L", "-p", str(self.pid()), "-o", "pid,tid,stat,wchan:30,comm"],
        }
        profiler = os.environ.get("OPENSCAN_E2E_PYSPY") or shutil.which("py-spy")
        if profiler:
            commands["python-stacks.txt"] = self.sudo + [profiler, "dump", "--native", "--full-filenames", "--pid", str(self.pid())]
        for name, command in commands.items():
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=12)
                (artifacts / name).write_text(result.stdout + result.stderr)
            except (OSError, subprocess.TimeoutExpired) as error:
                (artifacts / name).write_text(str(error))


@pytest.fixture
def scanner(tmp_path):
    if sys.platform != "linux" or not CMA_HEAP.exists():
        pytest.skip("Requires a Raspberry Pi with the linux,cma DMA heap")
    target = Scanner(tmp_path)
    assert urlsplit(target.base_url).hostname in {"127.0.0.1", "localhost", "::1"}, "CMA and API must be on the same Pi"
    print(f"Hardware test artifacts: {tmp_path}", flush=True)
    try:
        target.restart()
        yield target
    finally:
        try:
            target.diagnostics()
        finally:
            target.restart()
            status, body = target.get(target.camera_path + "/preview?mode=snapshot", timeout=10)
            target.record("restored_snapshot", status=status, body=body)
            assert status == 200, "Camera preview did not recover after test cleanup"
            restored = target.probes("restored_api")
            assert all(result["status"] == 200 for result in restored.values()), restored


def provoke_camera_oom(scanner):
    """Confirm allocation failure and release CMA before checking the API."""
    baseline = scanner.probes("before_oom")
    assert all(result["status"] == 200 for result in baseline.values()), baseline
    status, camera = scanner.get(scanner.camera_path, timeout=PROBE_TIMEOUT)
    assert status == 200 and camera["type"] == "picamera2", camera
    assert camera["busy"] is False, "Start with an idle camera"
    service_pid = scanner.pid()
    reserve_kib = int(os.environ.get("OPENSCAN_E2E_CMA_RESERVE_KIB", "8192"))
    assert reserve_kib >= 4096, "Leave at least 4096 KiB of CMA unreserved"

    with reserve_cma(reserve_kib, scanner.record):
        status, error = scanner.get(scanner.camera_path + "/photo?image_format=jpeg", timeout=20)
        scanner.record("photo_under_pressure", status=status, body=error)
        assert status >= 400 and "cannot allocate memory" in str(error).lower(), (
            f"Expected a real camera allocation failure, received HTTP {status}: {error}"
        )

    after_oom = scanner.probes("after_oom_before_snapshot")
    assert all(result["status"] == 200 for result in after_oom.values()), (
        f"API was already unhealthy before the snapshot request: {after_oom}"
    )
    assert scanner.pid() == service_pid, "Service restarted during the OOM phase"
    return service_pid


def provoke_camera_oom_and_snapshot(scanner):
    """Confirm a real OOM, then request a snapshot and measure API responsiveness."""
    service_pid = provoke_camera_oom(scanner)
    try:
        status, body = scanner.get(scanner.camera_path + "/preview?mode=snapshot", timeout=10)
        scanner.record("snapshot_after_oom", status=status, body=body)
    except (OSError, ValueError) as error:
        # A timed-out camera request is allowed here; the API must still respond.
        scanner.record("snapshot_after_oom", error=f"{type(error).__name__}: {error}")

    after_snapshot = scanner.probes("after_snapshot")
    time.sleep(2)
    later = scanner.probes("after_snapshot_2s")
    assert scanner.pid() == service_pid, "Service died or restarted after the snapshot"
    return service_pid, after_snapshot, later


def test_api_stays_responsive_after_camera_oom_and_snapshot(scanner):
    """An unresponsive camera must not block unrelated HTTP routes."""
    _, after_snapshot, later = provoke_camera_oom_and_snapshot(scanner)
    assert all(result["status"] == 200 for result in [*after_snapshot.values(), *later.values()]), (
        "API stopped responding after a preview snapshot following confirmed CMA OOM. "
        f"Immediately: {after_snapshot}; later: {later}. Artifacts: {scanner.artifacts}"
    )


@pytest.mark.skipif(
    not os.environ.get("OPENSCAN_E2E_INTERNAL_RESTART_SOCKET"),
    reason="Requires the test-only camera_restart_probe.py launcher in the service process",
)
def test_camera_restart_recovers_api_after_confirmed_hang(scanner):
    """Call the real restart method from a thread while the API main thread is stuck."""
    socket_path = os.environ["OPENSCAN_E2E_INTERNAL_RESTART_SOCKET"]

    def command(action):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(20)
        try:
            connection.connect(socket_path)
            connection.sendall((json.dumps({"action": action, "camera": scanner.camera}) + "\n").encode())
        except BaseException:
            connection.close()
            raise
        return connection

    def receive(stream):
        event = json.loads(stream.readline())
        scanner.record("internal_restart_probe", event=event)
        return event

    with command("ping") as connection, connection.makefile("rb") as stream:
        identity = receive(stream)
    assert identity["stage"] == "ready" and identity["pid"] == scanner.pid(), identity
    assert identity["thread_id"] != identity["main_thread_id"], identity

    service_pid, first, second = provoke_camera_oom_and_snapshot(scanner)
    assert all(result["status"] is None for result in [*first.values(), *second.values()]), (
        "The API hang was not reproduced; do not interpret this as a restart result. "
        f"First probes: {first}; second probes: {second}"
    )
    scanner.record("api_hang_confirmed", pid=service_pid)
    scanner.diagnostics("before_camera_restart")

    # The socket listener runs in the actual service process, independently of
    # its event loop. It calls the existing method on the same controller.
    scanner.record("internal_camera_restart_requested", pid=service_pid)
    started = time.monotonic()
    entered = None
    returned = None
    try:
        with command("restart") as connection, connection.makefile("rb") as stream:
            entered = receive(stream)
            returned = receive(stream)
    except (OSError, ValueError) as error:
        scanner.record("internal_camera_restart_wait", error=f"{type(error).__name__}: {error}",
                       seconds=time.monotonic() - started)

    after_restart = scanner.probes("after_camera_restart")
    time.sleep(2)
    later = scanner.probes("after_camera_restart_2s")
    assert scanner.pid() == service_pid, "A service restart must not count as camera recovery"
    assert entered and entered["stage"] == "restart_entered", (entered, returned)
    assert entered["pid"] == service_pid and entered["controller_id"] == identity["controller_id"], entered
    assert entered["thread_id"] != entered["main_thread_id"], entered
    assert returned and returned["stage"] == "restart_returned" and all(
        result["status"] == 200 for result in [*after_restart.values(), *later.values()]
    ), (
        "The internal camera restart did not recover the confirmed API hang. "
        f"Restart result: {returned}; probes: {after_restart}; later: {later}. "
        f"Artifacts: {scanner.artifacts}"
    )
    status, body = scanner.get(scanner.camera_path + "/preview?mode=snapshot", timeout=10)
    scanner.record("snapshot_after_camera_restart", status=status, body=body)
    assert status == 200, "API recovered but the camera cannot produce a new preview"
    final = scanner.probes("after_recovered_snapshot")
    assert all(result["status"] == 200 for result in final.values()), final


@pytest.mark.skipif(
    not os.environ.get("OPENSCAN_E2E_THREAD_EVENTS"),
    reason="Requires the test launcher with OPENSCAN_E2E_SNAPSHOT_TO_THREAD=1",
)
def test_threaded_snapshot_unwinds_on_camera_restart_and_api_stays_responsive(scanner):
    """Real to_thread wait -> healthy API -> HTTP restart -> pool call exits -> new captures."""
    service_pid = provoke_camera_oom(scanner)
    event_path = Path(os.environ["OPENSCAN_E2E_THREAD_EVENTS"])

    def events():
        return [event for line in event_path.read_text().splitlines()
                if (event := json.loads(line))["pid"] == service_pid]

    assert any(event["stage"] == "snapshot_offload_installed" for event in events()), (
        "The test launcher did not install the experimental snapshot offload"
    )
    # Keep the original HTTP request connected while probing and restarting.
    # Its client timeout does not cancel the server task or release camera jobs.
    client = ThreadPoolExecutor(max_workers=1)
    pending = client.submit(scanner.get, scanner.camera_path + "/preview?mode=snapshot", 45)
    try:
        deadline = time.monotonic() + 5
        entries = []
        while time.monotonic() < deadline:
            entries = [event for event in events() if event["stage"] == "preview_entered"]
            if entries:
                break
            time.sleep(0.1)
        assert len(entries) == 1, f"Expected one real preview invocation: {events()}"
        entry = entries[0]
        assert entry["thread_id"] != entry["main_thread_id"], entry

        for stage in ("api_while_thread_waits", "api_while_thread_waits_2s"):
            time.sleep(2)
            probes = scanner.probes(stage)
            assert all(result["status"] == 200 for result in probes.values()), probes
            assert not pending.done(), "Preview did not remain pending long enough to test recovery"
            assert not any(event["stage"] == "preview_exited" and event.get("call_id") == entry["call_id"]
                           for event in events()), "Preview worker already exited before restart"
        scanner.record("thread_wait_confirmed", worker=entry)
        scanner.diagnostics("before_camera_restart")

        started = time.monotonic()
        status, body = scanner.request(scanner.camera_path + "/restart", timeout=15, method="POST")
        scanner.record("http_camera_restart", status=status, body=body, seconds=time.monotonic() - started)
        assert status == 200, body
        assert scanner.pid() == service_pid, "Service restart must not count as camera recovery"

        try:
            status, body = pending.result(timeout=10)
            scanner.record("original_snapshot_finished", status=status, body=body)
        except OSError as error:
            scanner.record("original_snapshot_finished", error=f"{type(error).__name__}: {error}")
        assert pending.done(), "The original HTTP request still waits after camera restart"
        finished = [event for event in events() if event.get("call_id") == entry["call_id"]]
        scanner.record("original_preview_worker", events=finished)
        assert any(event["stage"] == "preview_exited" for event in finished), finished
        assert any(event["stage"] == "preview_raised" and event["error_type"] == "CancelledError"
                   for event in finished), finished

        for attempt in range(2):
            status, body = scanner.get(scanner.camera_path + "/preview?mode=snapshot", timeout=10)
            scanner.record("new_threaded_snapshot", attempt=attempt, status=status, body=body)
            assert status == 200, body
        status, body = scanner.get(scanner.camera_path + "/photo?image_format=jpeg", timeout=30)
        scanner.record("new_photo", status=status, body=body)
        assert status == 200, body
        status, body = scanner.get(scanner.camera_path + "/preview?mode=snapshot", timeout=10)
        scanner.record("snapshot_after_new_photo", status=status, body=body)
        assert status == 200, body

        probes = scanner.probes("after_threaded_camera_recovery")
        assert all(result["status"] == 200 for result in probes.values()), probes
        assert scanner.pid() == service_pid, "Service restarted during recovery validation"
    finally:
        client.shutdown(wait=False, cancel_futures=True)
        (scanner.artifacts / "preview-thread-events.jsonl").write_text(event_path.read_text())

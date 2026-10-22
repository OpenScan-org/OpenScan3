"""Test-only launcher: call the live camera controller from a separate thread.

Usage: <firmware-python> camera_restart_probe.py SOCKET ENTRYPOINT [ARGS...]
The local socket is restricted to the service user. Production files stay intact;
an explicit opt-in can offload the snapshot handler in memory for an experiment.
"""

import atexit
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import threading
import time
import traceback
from uuid import uuid4


def thread_event(stage, **details):
    path = os.environ["OPENSCAN_E2E_THREAD_EVENTS"]
    event = {"stage": stage, "pid": os.getpid(), "thread_id": threading.get_native_id(),
             "main_thread_id": threading.main_thread().native_id, "time": time.time(), **details}
    with open(path, "a") as output:
        output.write(json.dumps(event) + "\n")


def traced_preview(controller):
    """Observe a real pool invocation, including its exit after job cancellation."""
    call_id = uuid4().hex
    thread_event("preview_entered", call_id=call_id, controller_id=id(controller))
    try:
        frame = controller.preview()
    except BaseException as error:
        thread_event("preview_raised", call_id=call_id, error_type=type(error).__name__)
        raise
    else:
        thread_event("preview_returned", call_id=call_id, bytes_received=len(frame))
        return frame
    finally:
        thread_event("preview_exited", call_id=call_id)


class SnapshotLoader(SourceFileLoader):
    """Experiment with one await/to_thread change in memory, leaving files intact."""
    def get_code(self, fullname):
        source = self.get_data(self.path).decode("utf-8")
        original = "frame = controller.preview()"
        assert source.count(original) == 1, "Snapshot implementation changed; review this experiment"
        source = source.replace(original, "frame = await asyncio.to_thread(_hardware_test_preview, controller)")
        return self.source_to_code(source, self.path)

    def exec_module(self, module):
        module.__dict__["_hardware_test_preview"] = traced_preview
        super().exec_module(module)
        thread_event("snapshot_offload_installed", module=module.__name__)


class SnapshotFinder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "openscan_firmware.routers.v0_9.cameras":
            spec = PathFinder.find_spec(fullname, path)
            spec.loader = SnapshotLoader(fullname, spec.origin)
            return spec
        return None


def serve(listener):
    while True:
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(60)
            with connection.makefile("rwb") as stream:
                def send(**event):
                    stream.write((json.dumps(event) + "\n").encode())
                    stream.flush()

                try:
                    command = json.loads(stream.readline(4096))
                    # Use only the registry already loaded by the real server;
                    # importing another application or constructing a camera
                    # here would not test the stuck controller instance.
                    module = sys.modules["openscan_firmware.controllers.hardware.cameras.camera"]
                    controller = module.get_all_camera_controllers()[command["camera"]]
                    identity = {
                        "pid": os.getpid(),
                        "thread_id": threading.get_native_id(),
                        "main_thread_id": threading.main_thread().native_id,
                        "controller_id": id(controller),
                    }
                    if command["action"] == "ping":
                        send(stage="ready", **identity)
                    elif command["action"] == "restart":
                        method = controller.restart_camera
                        send(stage="restart_entered", source=method.__func__.__code__.co_filename,
                             **identity)
                        method()
                        send(stage="restart_returned", **identity)
                    else:
                        raise ValueError("Only ping and restart are supported")
                except Exception:
                    try:
                        send(stage="error", traceback=traceback.format_exc(), pid=os.getpid())
                    except OSError:
                        traceback.print_exc()


def main():
    if os.environ.get("OPENSCAN_E2E_SNAPSHOT_TO_THREAD") == "1":
        sys.meta_path.insert(0, SnapshotFinder())
    socket_path = Path(sys.argv[1])
    entrypoint = sys.argv[2]
    if socket_path.is_socket():
        socket_path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    socket_path.chmod(0o600)
    listener.listen(1)
    atexit.register(lambda: socket_path.unlink(missing_ok=True))
    threading.Thread(target=serve, args=(listener,), name="hardware-test-restart", daemon=True).start()
    sys.argv = [entrypoint, *sys.argv[3:]]
    runpy.run_path(entrypoint, run_name="__main__")


if __name__ == "__main__":
    main()

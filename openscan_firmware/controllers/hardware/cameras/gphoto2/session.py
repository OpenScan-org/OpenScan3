"""Low-level session wrapper around python-gphoto2."""

from __future__ import annotations

import logging
import time
from typing import Any

import gphoto2 as gp

from .profile import CameraIdentity

logger = logging.getLogger(__name__)


class GPhoto2Session:
    """Manage a gphoto2 camera session for one physical device."""

    def __init__(self, camera_path: str, model_hint: str | None = None):
        self._camera_path = camera_path
        self._model_hint = model_hint
        self._camera: Any | None = None
        self._identity = CameraIdentity(model=model_hint, port=camera_path)
        self._io_retry_attempts = 3
        self._io_retry_delay_s = 0.08

    @property
    def identity(self) -> CameraIdentity:
        return self._identity

    def ensure_connected(self) -> Any:
        if self._camera is not None:
            return self._camera

        port_info_list = gp.PortInfoList()
        port_info_list.load()
        abilities_list = gp.CameraAbilitiesList()
        abilities_list.load()

        detected_model = self._model_hint
        try:
            camera_list = abilities_list.detect(port_info_list)
            for idx in range(camera_list.count()):
                model_name = camera_list.get_name(idx)
                detected_path = camera_list.get_value(idx)
                if detected_path == self._camera_path:
                    detected_model = model_name
                    break
        except Exception:
            logger.debug("GPhoto2 autodetect lookup failed.", exc_info=True)

        camera = gp.Camera()
        if self._camera_path:
            port_idx = port_info_list.lookup_path(self._camera_path)
            if port_idx >= 0:
                camera.set_port_info(port_info_list[port_idx])

        if detected_model:
            try:
                abilities_idx = abilities_list.lookup_model(detected_model)
                if abilities_idx >= 0:
                    camera.set_abilities(abilities_list[abilities_idx])
            except Exception:
                logger.debug("Failed setting camera abilities for '%s'.", detected_model, exc_info=True)

        camera.init()
        self._camera = camera
        self._identity = CameraIdentity(model=detected_model or self._model_hint, port=self._camera_path)
        return camera

    def close(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.exit()
        except Exception:
            logger.debug("Failed to close gphoto2 camera session cleanly.", exc_info=True)
        finally:
            self._camera = None

    def capture_preview(self) -> bytes:
        camera = self.ensure_connected()
        camera_file = gp.gp_camera_capture_preview(camera)[1]
        return bytes(camera_file.get_data_and_size())

    def capture_image(self, gp_file_type: int = gp.GP_FILE_TYPE_NORMAL) -> tuple[bytes, dict[str, Any]]:
        camera = self.ensure_connected()
        try:
            file_path = camera.capture(gp.GP_CAPTURE_IMAGE)
        except Exception as exc:
            message = str(exc)
            # Nikon (and some other DSLRs) can fail with "Unspecified error"
            # on camera.capture(), but succeed via trigger + event polling.
            if "Unspecified error" in message or "[-1]" in message:
                logger.debug(
                    "camera.capture failed with '%s'; trying trigger-capture fallback.",
                    message,
                )
                file_path = self._trigger_capture_and_wait_for_file(camera)
            else:
                raise
        camera_file = camera.file_get(file_path.folder, file_path.name, gp_file_type)
        payload = bytes(camera_file.get_data_and_size())
        metadata = {
            "capture_folder": file_path.folder,
            "capture_name": file_path.name,
            "gp_file_type": gp_file_type,
        }
        return payload, metadata

    def _trigger_capture_and_wait_for_file(self, camera: Any, timeout_s: float = 12.0):
        start = time.monotonic()

        if hasattr(camera, "trigger_capture"):
            camera.trigger_capture()
        else:
            gp.gp_camera_trigger_capture(camera)

        while time.monotonic() - start < timeout_s:
            event_type, event_data = camera.wait_for_event(1000)
            if event_type == gp.GP_EVENT_FILE_ADDED and event_data is not None:
                return event_data
            if event_type == gp.GP_EVENT_TIMEOUT:
                continue
            if event_type == gp.GP_EVENT_UNKNOWN:
                continue

        raise RuntimeError("Trigger-capture fallback timed out waiting for GP_EVENT_FILE_ADDED.")

    def set_config_value(self, key: str, value: Any) -> bool:
        camera = self.ensure_connected()
        config = self._get_config_with_retry(camera, key_context=key)
        if config is None:
            return False
        child = self._find_widget(config, key)
        if child is None:
            return False
        # Normalize enum-like choices to avoid trivial casing mismatches.
        choices = self._extract_choices(child)
        if choices:
            selected = self._match_choice(choices, value)
        else:
            selected = value

        current = self._safe_call(child, "get_value")
        if current is not None and str(current) == str(selected):
            return True

        try:
            child.set_value(selected)
            camera.set_config(config)
        except Exception as exc:
            logger.debug("Setting config '%s' to '%s' failed: %s", key, selected, exc)
            return False

        verified = self._safe_call(child, "get_value")
        if verified is not None and str(verified) != str(selected):
            logger.debug(
                "Config '%s' write did not persist expected value (wanted=%s got=%s).",
                key,
                selected,
                verified,
            )
            return False
        return True

    def set_first_config_value(self, keys: list[str], value: Any) -> bool:
        for key in keys:
            try:
                if self.set_config_value(key, value):
                    return True
            except Exception:
                logger.debug("Setting config '%s' failed.", key, exc_info=True)
        return False

    def get_config_details(self, key: str) -> dict[str, Any] | None:
        camera = self.ensure_connected()
        config = self._get_config_with_retry(camera, key_context=key)
        if config is None:
            return None
        child = self._find_widget(config, key)
        if child is None:
            return None

        details: dict[str, Any] = {
            "key": key,
            "name": self._safe_call(child, "get_name"),
            "label": self._safe_call(child, "get_label"),
            "type": self._safe_call(child, "get_type"),
            "readonly": self._safe_call(child, "get_readonly"),
            "value": self._safe_call(child, "get_value"),
            "choices": self._extract_choices(child),
        }
        return details

    def get_first_config_details(self, keys: list[str]) -> dict[str, Any] | None:
        for key in keys:
            try:
                details = self.get_config_details(key)
            except Exception:
                logger.debug("Reading config '%s' failed.", key)
                continue
            if details is not None:
                return details
        return None

    def _get_config_with_retry(self, camera: Any, key_context: str) -> Any | None:
        for attempt in range(self._io_retry_attempts):
            try:
                return camera.get_config()
            except Exception as exc:
                message = str(exc)
                is_io_in_progress = "I/O in progress" in message or "[-110]" in message
                if is_io_in_progress and attempt < self._io_retry_attempts - 1:
                    time.sleep(self._io_retry_delay_s)
                    continue
                logger.debug("Reading config '%s' failed: %s", key_context, exc)
                return None
        return None

    @staticmethod
    def _find_widget(config_root: Any, key: str) -> Any | None:
        if key.startswith("/"):
            return GPhoto2Session._find_widget_by_path(config_root, key)
        by_name = GPhoto2Session._find_widget_by_name(config_root, key)
        if by_name is not None:
            return by_name
        if hasattr(config_root, "get_child_by_name"):
            try:
                return config_root.get_child_by_name(key)
            except Exception:
                return None
        return None

    @staticmethod
    def _find_widget_by_path(config_root: Any, key_path: str) -> Any | None:
        parts = [part for part in key_path.split("/") if part]
        if not parts:
            return config_root

        current = config_root
        root_name = GPhoto2Session._safe_call(current, "get_name")
        if parts and root_name and parts[0] == str(root_name):
            parts = parts[1:]

        for part in parts:
            next_widget = None
            if hasattr(current, "get_child_by_name"):
                try:
                    next_widget = current.get_child_by_name(part)
                except Exception:
                    next_widget = None
            if next_widget is None:
                return None
            current = next_widget
        return current

    @staticmethod
    def _find_widget_by_name(config_root: Any, key_name: str) -> Any | None:
        if hasattr(config_root, "get_name"):
            try:
                if str(config_root.get_name()) == key_name:
                    return config_root
            except Exception:
                pass

        try:
            child_count = config_root.count_children()
        except Exception:
            child_count = 0

        for child_idx in range(child_count):
            try:
                child = config_root.get_child(child_idx)
            except Exception:
                continue
            found = GPhoto2Session._find_widget_by_name(child, key_name)
            if found is not None:
                return found
        return None

    @staticmethod
    def _extract_choices(widget: Any) -> list[Any]:
        try:
            count = widget.count_choices()
        except Exception:
            return []
        choices: list[Any] = []
        for idx in range(count):
            try:
                choices.append(widget.get_choice(idx))
            except Exception:
                continue
        return choices

    @staticmethod
    def _match_choice(choices: list[Any], value: Any) -> Any:
        value_str = str(value)
        for choice in choices:
            if str(choice) == value_str:
                return choice
        lowered = value_str.lower()
        for choice in choices:
            if str(choice).lower() == lowered:
                return choice
        return value

    @staticmethod
    def _safe_call(widget: Any, method_name: str) -> Any:
        method = getattr(widget, method_name, None)
        if method is None:
            return None
        try:
            return method()
        except Exception:
            return None

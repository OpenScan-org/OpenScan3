"""
Picamera2 Controller

This module provides a CameraController class for controlling the picamera2 camera.

"""

import io
import logging
import time
from importlib.metadata import metadata
from io import BytesIO

import cv2
import numpy as np
import piexif
from typing import IO, Any
from libcamera import ColorSpace, controls, Transform
ColorSpace.Jpeg = ColorSpace.Sycc
from picamera2 import Picamera2

from openscan_firmware.controllers.hardware.cameras.camera import CameraController
from openscan_firmware.models.camera import Camera, CameraMetadata, PhotoData
from openscan_firmware.config.camera import CameraSettings

logger = logging.getLogger(__name__)

class CameraStrategy:
    """Base strategy class for camera-specific configurations and processing"""

    def create_preview_config(self, picam: Picamera2, preview_resolution=None, additional_settings=None):
        raise NotImplementedError

    def create_photo_config(self, picam: Picamera2, photo_resolution=None, additional_settings=None):
        raise NotImplementedError

    def create_raw_config(self, picam: Picamera2, raw_resolution=None, additional_settings=None):
        raise NotImplementedError

    def create_yuv_config(self, picam: Picamera2, additional_settings=None):
        raise NotImplementedError

    def create_rgb_config(self, picam: Picamera2, additional_settings=None):
        raise NotImplementedError

    def process_preview_frame(self, frame):
        return frame

    def process_photo_frame(self, frame):
        return frame

class IMX519Strategy(CameraStrategy):
    """Strategy class for camera-specific configurations and processing for the Arducam IMX519 camera with 16MP."""
    def create_preview_config(self, picam: Picamera2, preview_resolution=None, additional_settings=None):
        if preview_resolution is None:
            return picam.create_preview_configuration(
                main={"size": (2328, 1748)},
                lores={"size": (640, 480), "format": "YUV420"},
                raw=None,
                controls=additional_settings
            )
        return picam.create_preview_configuration(
            main={"size": preview_resolution},
            controls=additional_settings
        )

    def create_photo_config(self, picam: Picamera2, photo_resolution=None, additional_settings=None):
        if photo_resolution is None:
            return picam.create_still_configuration(buffer_count=1,
                                                    main={"size": (4656, 3496),  "format": "RGB888"},
                                                    controls=additional_settings
                                                    )
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": photo_resolution, "format": "RGB888"},
                                                controls=additional_settings
                                                )

    def create_raw_config(self, picam: Picamera2, raw_resolution=None, additional_settings=None):
        if raw_resolution is None:
            return picam.create_still_configuration(buffer_count=1,
                                                    main={"size": (4656, 3496)},
                                                    raw={"size": (4656, 3496)},
                                                    controls=additional_settings
                                                    )
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": (2328, 1748)},
                                                raw={"size": raw_resolution},
                                                controls=additional_settings
                                                )

    def create_yuv_config(self, picam: Picamera2, additional_settings=None):
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": (4656, 3496), "format": "YUV420"},
                                                controls=additional_settings
                                                )

    def create_rgb_config(self, picam: Picamera2, additional_settings=None):
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": (4656, 3496)},
                                                controls=additional_settings
                                                )

    def process_preview_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels


class HawkeyeStrategy(CameraStrategy):
    """Strategy class for camera-specific configurations and processing for the Arducam Hawkeye camera with 64MP."""
    def create_preview_config(self, picam: Picamera2, preview_resolution=None, additional_settings=None):
        return picam.create_preview_configuration(
            main={"size": (2312, 1736)},
            raw=None,
            controls=additional_settings
            )

    def create_photo_config(self, picam: Picamera2, photo_resolution=None, additional_settings=None):
        if photo_resolution is None:
            return picam.create_still_configuration(
                buffer_count=1,
                main={"size": (9152, 6944)},
                raw=None, # needed to be set to None explicitly to avoid memory issues
                controls=additional_settings
                )
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": photo_resolution},
                                                raw=None,
                                                controls=additional_settings)

    def create_raw_config(self, picam: Picamera2, raw_resolution=None, additional_settings=None):
        if raw_resolution is None:
            return picam.create_still_configuration(
                buffer_count=1,
                main={"size": (2312, 1736)}, # main cannot be None and has to be reduced to avoid memory issues
                raw={"size": (9152, 6944)},
                controls=additional_settings
                )
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": (2312, 1736)},
                                                raw={"size": raw_resolution},
                                                controls=additional_settings
                                                )

    def create_yuv_config(self, picam: Picamera2, additional_settings=None):
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": (9152, 6944), "format": "YUV420"},
                                                raw=None,
                                                controls=additional_settings
                                                )

    def create_rgb_config(self, picam: Picamera2, additional_settings=None):
        return picam.create_still_configuration(buffer_count=1,
                                                main={"size": (9152, 6944)},
                                                raw=None,
                                                controls=additional_settings
                                                )

    def process_preview_frame(self, frame):
        return frame[:, :, [2, 1, 0]]  # correct the color channels

    def process_photo_frame(self, frame):
        return frame


class Picamera2Controller(CameraController):
    _strategies = {
        "imx519": IMX519Strategy(),
        "arducam_64mp": HawkeyeStrategy()
    }
    _tuning_files = {
        "imx519": "imx519.json",
        "arducam_64mp": "arducam_64mp.json",
    }
    _macro_focus_ranges = {
        # Measured on the OpenScan Mini with the IMX519: the turntable is at
        # approximately 11.9 dpt, while useful objects extend towards 15 dpt.
        ("mini", "imx519"): {
            "min": 11.0,
            "max": 15.0,
            "default": 12.0,
            "fallback": 11.9,
            "timeout": 8.0,
        },
    }

    def __init__(self, camera: Camera):
        super().__init__(camera)
        self._picam = self._create_picamera()
        self._strategy = self._strategies.get(self.camera.name, IMX519Strategy())
        self._is_closing = False

        self._configure_resolutions()
        self._picam.configure(self.preview_config)
        self._picam.start()

        self._apply_settings_to_hardware(self.camera.settings)
        self._configure_focus(camera_mode="preview")

    def _create_picamera(self) -> Picamera2:
        """Create Picamera2 with the camera's tuned autofocus range."""
        focus_range = self._macro_focus_ranges.get((self.camera.scanner_model, self.camera.name))
        if focus_range is None:
            logger.info(
                "No OpenScan autofocus profile configured for scanner=%s camera=%s; "
                "using the vendor tuning unchanged.",
                self.camera.scanner_model,
                self.camera.name,
            )
            return Picamera2()

        tuning_file = self._tuning_files.get(self.camera.name)

        if tuning_file is None:
            logger.info("No Picamera2 tuning file configured for %s; using libcamera defaults.", self.camera.name)
            return Picamera2()

        try:
            tuning = Picamera2.load_tuning_file(tuning_file)
            af = Picamera2.find_tuning_algo(tuning, "rpi.af")
            ranges = af["ranges"]
            if "macro" not in ranges:
                raise KeyError("rpi.af.ranges.macro")
            ranges["macro"].update(focus_range)
        except (KeyError, RuntimeError, StopIteration, TypeError) as exc:
            logger.warning(
                "Could not load Picamera2 tuning file '%s'; using libcamera defaults: %s",
                tuning_file,
                exc,
            )
            return Picamera2()

        logger.info(
            "Loaded Picamera2 tuning '%s' with macro focus range %.1f-%.1f dpt.",
            tuning_file,
            focus_range["min"],
            focus_range["max"],
        )
        return Picamera2(tuning=tuning)

    def _autofocus_or_fallback(self) -> bool:
        """Run profiled autofocus with a bounded fallback.

        Cameras without an OpenScan focus profile keep Picamera2's normal
        autofocus behaviour.
        """
        focus_profile = self._macro_focus_ranges.get((self.camera.scanner_model, self.camera.name))
        if focus_profile is None:
            return self._picam.autofocus_cycle()

        try:
            success = self._picam.autofocus_cycle(wait=focus_profile["timeout"])
        except TimeoutError:
            # The Picamera2 job would otherwise remain queued after its wait
            # timed out. Clear it before switching the lens to manual mode.
            self._picam.cancel_all_and_flush()
            logger.warning(
                "Autofocus timed out after %.1f seconds; using fallback LensPosition %.1f.",
                focus_profile["timeout"],
                focus_profile["fallback"],
            )
            success = False

        if success:
            return True

        logger.warning(
            "Autofocus failed; using fallback LensPosition %.1f.",
            focus_profile["fallback"],
        )
        self._picam.set_controls({
            "AfMode": controls.AfModeEnum.Manual,
            "LensPosition": focus_profile["fallback"],
        })
        return False

    def _apply_settings_to_hardware(self, settings: CameraSettings):
        """This method is call on every change of settings."""
        self._set_busy(True)
        
        # Apply the same manual controls to the live preview that are used by
        # still captures. The still configuration supplies its own default
        # frame-duration range.
        manual_controls = self._manual_camera_controls(settings)
        if manual_controls:
            self._picam.set_controls(manual_controls)

        # Rebuild the capture configurations so still captures use the new settings.
        self._configure_resolutions()

        self._configure_focus(camera_mode="preview")  

        self._set_busy(False)
        logger.debug(f"Applied settings to hardware: {settings.model_dump_json()}")


    @staticmethod
    def _manual_camera_controls(settings):
        """Build the manual camera controls from the current settings."""
        controls = {}

        setting_mapping = {
            "saturation": "Saturation",
            "contrast": "Contrast",
            "gain": "AnalogueGain",
        }
        for setting_name, control_name in setting_mapping.items():
            value = getattr(settings, setting_name)
            if value is not None:
                controls[control_name] = value

        if settings.shutter is not None:
            controls["ExposureTime"] = int(settings.shutter * 1000)

        if settings.awbg_red is not None and settings.awbg_blue is not None:
            controls["ColourGains"] = (settings.awbg_red, settings.awbg_blue)

        return controls

    def _still_capture_controls(self):
        """Return controls that must be applied consistently to still captures."""
        still_settings = self._photogrammetry_settings.copy()
        manual_controls = self._manual_camera_controls(self.settings)
        still_settings.update(manual_controls)
        return still_settings

    def _configure_resolutions(self, crop_rect=None):
        """Create all camera configurations.

        Preview uses the fixed photogrammetry controls. Still captures additionally
        use the current manual exposure, gain, colour, saturation and contrast
        settings. JPEG and DNG may supply ``crop_rect``; RGB and YUV deliberately
        remain uncropped for image analysis.
        """
        logger.debug("Configuring resolutions...")
        photogrammetry_settings = {"AeEnable": False,  # Disable auto exposure
                                   "NoiseReductionMode": 0,  # Disable noise reduction
                                   "AwbEnable": False  # Disable automatic white balance
                                   }

        self._photogrammetry_settings = photogrammetry_settings.copy()
        still_settings = self._still_capture_controls()

        still_resolution = self.settings.photo_resolution
        if crop_rect is not None:
            _, _, crop_width, crop_height = crop_rect
            still_resolution = (crop_width, crop_height)
            still_settings["ScalerCrop"] = crop_rect

        self.preview_config = self._strategy.create_preview_config(self._picam, self.settings.preview_resolution, photogrammetry_settings)
        self.photo_config = self._strategy.create_photo_config(self._picam, still_resolution, still_settings)
        self.raw_config = self._strategy.create_raw_config(self._picam, still_resolution, still_settings)
        self.yuv_config = self._strategy.create_yuv_config(self._picam, self._still_capture_controls())
        self.rgb_config = self._strategy.create_rgb_config(self._picam, self._still_capture_controls())

        logger.debug(f"Configured resolutions with preview controls {photogrammetry_settings} and still controls {still_settings}.")


    def _configure_cropping(self):
        """Deprecated! Configure cropping of the image based on settings."""
        full_x, full_y = self._picam.camera_properties['PixelArraySize']
        # because of rotation, height is x and width is y
        crop_x = self.settings.crop_height / 100
        crop_y = self.settings.crop_width / 100

        x_start = int(full_x * crop_x / 2)
        x_end = int(full_x - x_start)
        y_start = int(full_y * crop_y / 2)
        y_end = int(full_y - y_start)

        return y_start, y_end, x_start, x_end

    def _configure_cropping_for_scalercrop(self):
        """Configure JPEG/DNG cropping and rebuild the shared camera configurations.

        RGB and YUV configurations intentionally remain uncropped for analysis.
        """
        full_x, full_y = self._picam.camera_properties["PixelArraySize"]

        crop_x = self.settings.crop_width / 100
        crop_y = self.settings.crop_height / 100

        rotated_flags = {5, 6, 7, 8}
        if self.camera.settings.orientation_flag in rotated_flags:
            # adujst for camera rotation
            crop_x = self.settings.crop_height / 100
            crop_y = self.settings.crop_width / 100

        width = int(full_x * (1 - crop_x))  // 2 * 2
        height = int(full_y * (1 - crop_y))  // 2 * 2


        x_start = (full_x - width) // 2
        y_start = (full_y - height) // 2

        crop_rect = (x_start, y_start, width, height)
        self._configure_resolutions(crop_rect=crop_rect)

        return crop_rect


    def _configure_focus(self, camera_mode: str = None):
        """Configure focus based on settings.

        Args:
            camera_mode (str, optional): Whether to configure focus for "preview" or "photo" mode. Defaults to None.
        """
        if self.settings.AF:
            full_x, full_y = self._picam.camera_properties['PixelArraySize']

            if self.settings.AF_window is not None:
                x, y, w, h = self.settings.AF_window
                af_window = [_transform_settings_to_camera_coordinates(setting_coordinates=(x, y),
                                                                       camera_resolution=(full_x, full_y),
                                                                       setting_size=(w, h))]
            else:
                # Default the focus window to the central 1% of the image
                win_width = int(full_x * 0.1)
                win_height = int(full_y * 0.1)
                x_start = (full_x - win_width) // 2
                y_start = (full_y - win_height) // 2
                af_window = [(x_start, y_start, win_width, win_height)]

            self._picam.set_controls({
                    "AfMetering": controls.AfMeteringEnum.Windows,
                    "AfWindows": af_window
                })

            if camera_mode == "preview":
                # Configure continuous auto focus mode
                self._picam.set_controls({
                        "AfMode": controls.AfModeEnum.Continuous,
                        "AfRange": controls.AfRangeEnum.Macro,
                        #"AfSpeed": controls.AfSpeedEnum.Fast
                    })
            elif camera_mode == "photo":
                # Configure auto focus mode
                self._picam.set_controls({
                        "AfMode": controls.AfModeEnum.Auto,
                        "AfRange": controls.AfRangeEnum.Macro,
                        #"AfSpeed": controls.AfSpeedEnum.Fast
                    })
            logger.info(f"Auto focus enabled with AFWindow: {af_window}")

        else:
            # configure manual focus mode
            if self.settings.manual_focus is None:
                self.settings.manual_focus = 1.0
            self._picam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": self.settings.manual_focus})

            # Wait for focus with tolerance
            target_focus = float(self.settings.manual_focus)
            tolerance = 0.001 # 0.1% tolerance

            start = time.time()
            while abs(self._picam.capture_metadata()["LensPosition"] - target_focus) > tolerance:
                if time.time() - start > 5:
                    logger.warning(f"Warning: Focus timeout! Target: {target_focus}, Current: {self._picam.capture_metadata()['LensPosition']}")
                    break
                time.sleep(0.05) # wait for focus to be applied

            logger.info(f"Manual focus enabled, current Focus set to: {self._picam.capture_metadata()['LensPosition']}")

    def calibrate_awb_and_lock(self, warmup_frames=12,
                                     stable_frames=4,
                                     eps=0.01,
                                     timeout_s=2.0) -> tuple[float, float]:
        """Use the picamera2 automatic white balance and lock it afterward for consistent color correction.

        Args:
            warmup_frames (int, optional): Number of frames to warm up the camera. Defaults to 12.
            stable_frames (int, optional): Number of frames to wait for the AWB to stabilize. Defaults to 4.
            eps (float, optional): Epsilon value for the AWB stability check. Defaults to 0.01.
            timeout_s (float, optional): Timeout in seconds for the AWB calibration. Defaults to 2.0.

        Returns:
            tuple: A tuple containing the red and blue gains.
        """
        self._set_busy(True)
        logger.info("Will configure automatic white balance for color correction...")
        logger.debug(
            f"Warmup frames: {warmup_frames}, Stable frames: {stable_frames}, Epsilon: {eps}, Timeout: {timeout_s}")
        logger.debug(f"Current camera metadata: {self._picam.capture_metadata()}")

        self._picam.set_controls({"AwbEnable": True})

        metadata = self._picam.capture_metadata()
        current_controls = {c: metadata[c] for c in ["ExposureTime", "ColourGains"]}
        logger.info(f"Current Exposure {current_controls['ExposureTime']}, ColourGains {current_controls['ColourGains']}")

        self._picam.drop_frames(warmup_frames, wait=True)

        last = None
        steady = 0
        best_gains = None
        t0 = time.monotonic()

        while time.monotonic() - t0 < timeout_s:
            md = self._picam.capture_metadata()
            gains = md.get("ColourGains")
            if not gains:
                continue

            if last is not None:
                dr = abs(gains[0] - last[0])
                db = abs(gains[1] - last[1])
                if dr < eps and db < eps:
                    steady += 1
                else:
                    steady = 0
            last = gains
            best_gains = gains

            if steady >= stable_frames:
                break

        if best_gains is None:
            logger.error("Could not determine gains from metadata.")
            raise RuntimeError("Could not determine gains from metadata.")

        # Disable AWB and persist gains via the settings wrapper so downstream code stays in sync
        locked_gains = (float(best_gains[0]), float(best_gains[1]))
        self._picam.set_controls({
            "AwbEnable": False,
        })
        self.settings.update(awbg_red=locked_gains[0], awbg_blue=locked_gains[1])
        logger.info(f"AWB locked with gains: {best_gains}")

        self._set_busy(False)

        return locked_gains

    def restart_camera(self):
        """Restart the camera and reconfigure resolution."""
        self._picam.stop()
        self._set_busy(False)
        self._configure_resolutions()
        self._picam.configure(self.preview_config)
        self._picam.start()
        logger.info(f"Picamera2 restarted.")

    def _capture_array(self, config):
        self._set_busy(True)
        self._configure_focus(camera_mode="photo")
        if self.settings.AF:
            self._autofocus_or_fallback()

        last_exc = None
        try:
            for attempt in range(1, 4):
                try:
                    req = self._picam.switch_mode_and_capture_request(config, wait=True)
                    try:
                        array = req.make_array("main")
                        cam_metadata = req.get_metadata()
                    finally:
                        req.release()
                    logger.debug(f"Captured array with metadata: {cam_metadata}")
                    return array, cam_metadata
                except Exception as e:
                    last_exc = e
                    logger.warning(f"_capture_array attempt {attempt}/3 failed: {e}")
                    if attempt < 3:
                        # Exponential backoff to give the system time to free/recover memory
                        backoff = [1, 2, 4][attempt - 1]
                        time.sleep(backoff)
                        continue
                    break
        except:
            # All attempts failed; re-raise the last exception for upstream handling
            raise last_exc
        finally:
            # Always switch back to preview focus and clear busy flag
            self._configure_focus(camera_mode="preview")
            self._set_busy(False)


    def capture_rgb_array(self) -> PhotoData:
        """Capture a rgb array.

        Returns:
            tuple[Any, Any]: A tuple containing the rgb array and metadata.

        """
        array, camera_metadata = self._capture_array(self.rgb_config)
        return self._create_artifact(array, "rgb_array", camera_metadata)


    def capture_yuv_array(self) -> PhotoData:
        """Capture a yuv array.

        Returns:
            tuple[Any, Any]: A tuple containing the yuv array and metadata.
        """
        array, camera_metadata = self._capture_array(self.yuv_config)
        return self._create_artifact(array, "yuv_array", camera_metadata)


    def crop_arrays(self, array):
        """Todo: keep as utility function or delete?"""
        processed_array = self._strategy.process_photo_frame(array)

        if self.settings.crop_height > 0 or self.settings.crop_width > 0:
            y_start, y_end, x_start, x_end = self._configure_cropping()
            processed_array = processed_array[y_start:y_end, x_start:x_end]
            logger.debug(f"Cropped photo array.")

        return processed_array



    def capture_jpeg(self, optional_exif_data: dict = None) -> PhotoData:
        """Capture a jpeg.

        Capture a single photo using the current photo configuration.
        We capture a photo by switching to photo config and immediately returning to faster preview mode.
        This results in much faster autofocus adjustments after motor movements.
        The photo is processed according to the strategy and then converted to a JPEG image.

        Args:
            optional_exif_data (dict, optional): Optional exif data to be added to the image. Defaults to None.

        Returns:
            tuple[BytesIO, dict]: A tuple containing the JPEG data and metadata."""
        self._set_busy(True)
        self._configure_focus(camera_mode="photo")
        self._configure_cropping_for_scalercrop()
        if self.settings.AF:
            self._autofocus_or_fallback()

        self._picam.options["quality"] = self.settings.jpeg_quality

        exif_data = {
            "0th": {
                piexif.ImageIFD.Orientation: self.settings.orientation_flag,
                # piexif.ImageIFD.Make: "Raspberry Pi",
                piexif.ImageIFD.Model: self.camera.name,
                piexif.ImageIFD.Software: "OpenScan3 (Picamera2)",
            }
        }

        if optional_exif_data:
            exif_data.update(optional_exif_data)

        jpeg_data = io.BytesIO()
        cam_metadata = self._picam.switch_mode_and_capture_file(self.photo_config,
                                                            jpeg_data,
                                                            #delay=5,
                                                            format='jpeg',
                                                            exif_data=exif_data)

        self._configure_focus(camera_mode="preview")

        logger.debug(f"Captured jpeg with metadata: {cam_metadata}")

        self._set_busy(False)

        return self._create_artifact(jpeg_data, "jpeg", cam_metadata)


    def capture_dng(self) -> PhotoData:
        """Capture a dng.

        Returns:
            tuple[BytesIO, Any]: A tuple containing the dng data and metadata."""
        self._set_busy(True)
        self._configure_focus(camera_mode="photo")
        self._picam.set_controls({"ScalerCrop": self._configure_cropping_for_scalercrop()})
        if self.settings.AF:
            self._autofocus_or_fallback()

        dng_data = io.BytesIO()
        camera_metadata = self._picam.switch_mode_and_capture_file(self.raw_config,
                                                            dng_data,
                                                            name='raw')

        self._configure_focus(camera_mode="preview")

        logger.debug(f"Captured dng with metadata: {camera_metadata}")

        self._set_busy(False)

        return self._create_artifact(dng_data, "dng", camera_metadata)

    def _prepare_metadata(self, raw_metadata) -> CameraMetadata:
        """Prepare metadata for photo artifact.

        Args:
            raw_metadata (Any): The raw metadata.

        Returns:
            CameraMetadata: The prepared metadata."""
        camera_metadata = CameraMetadata(camera_name=self.camera.name,
                                         camera_settings=self.settings.model,
                                         raw_metadata=raw_metadata)
        return camera_metadata

    def _create_artifact(self, data, data_format, camera_metadata) -> PhotoData:
        """Create a photo artifact.

        Args:
            data (Any): The data.
            data_format (Literal['jpeg', 'dng', 'rgb_array', 'yuv_array']): The image format.
            camera_metadata (CameraMetadata): The camera metadata.

        Returns:
            PhotoData: The photo artifact."""
        return PhotoData(data=data, format=data_format, camera_metadata=self._prepare_metadata(camera_metadata))


    def preview(self, mode="main") -> IO[bytes]:
        """Capture a preview.

        Capture a preview using the current preview configuration.
        The preview is processed according to the strategy and then converted to a JPEG image.

        Args:
            mode (str, optional): The mode to capture in either "main" or "lores". Defaults to "main".

        Returns:
            IO[bytes]: A file-like object containing the JPEG image.
        """
        if self._picam is None or self._is_closing:
            raise RuntimeError("Picamera2 controller is not available")

        self._set_busy(True)
        frame = self._picam.capture_array(mode)

        if mode == "lores":
            frame = cv2.cvtColor(frame, cv2.COLOR_YUV420p2RGB)
            frame = apply_gamma_correction(frame, gamma=2.2)
        elif mode == "main":
            frame = cv2.resize(frame, (640, 480))
            frame = self._strategy.process_preview_frame(frame)

        rotate_map = {
            1: lambda f: f,
            3: lambda f: cv2.rotate(f, cv2.ROTATE_180),
            6: lambda f: cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE),
            8: lambda f: cv2.rotate(f, cv2.ROTATE_90_COUNTERCLOCKWISE),
        }
        #frame = rotate_map.get(self.settings.orientation_flag, lambda f: f)(frame)

        _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        self._set_busy(False)
        return jpeg.tobytes()


    def cleanup(self):
        """Clean up the camera resource."""
        if self._picam is None:
            return

        self._is_closing = True
        try:
            self._picam.stop()
        except Exception as exc:
            logger.debug("Picamera2 stop raised during cleanup: %s", exc)

        try:
            self._picam.close()
        finally:
            self._picam = None
            logger.debug("Picamera2 controller closed successfully.")


"""
Utility functions.
"""

def apply_gamma_correction(image, gamma=2.2):
    lookup_table = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)]).astype("uint8")
    return cv2.LUT(image, lookup_table)


def _transform_settings_to_camera_coordinates(setting_coordinates: tuple[int, int],
                                              camera_resolution: tuple[int, int],
                                              setting_size: tuple[int, int] = (0, 0),
                                              rotation_steps: int = 3) -> tuple[int, int, int, int]:
    """
    Transforms setting coordinates and adjusts them to camera coordinates, applying rotation
    steps as needed. Rotation is in 90 degree steps clockwise.

    Args:
        setting_coordinates (tuple[int, int]): X and Y coordinates of the setting
        camera_resolution (tuple[int, int]): X and Y resolution of the camera
        setting_size (tuple[int, int], optional): Width and height of the setting. Defaults to (0, 0).
        rotation_steps (int, optional): Number of 90 degree clockwise rotation steps. Defaults to 3.

    Returns:
        tuple[int, int, int, int]: Top-left X, Top-left Y, Width, Height of the setting in camera coordinates
    """
    setting_x, setting_y = setting_coordinates
    resolution_x, resolution_y = camera_resolution
    settings_width, setting_height = setting_size

    norm_rotation = rotation_steps % 4

    if norm_rotation == 0:  # 0 degrees rotation
        # User view is aligned with camera view
        cam_x, cam_y, cam_w, cam_h = setting_x, setting_y, settings_width, setting_height
    elif norm_rotation == 1:  # 90 degrees clockwise rotation
        # User's X-axis -> Camera's Y-axis (top to bottom)
        # User's Y-axis -> Camera's X-axis (right to left)
        cam_x = resolution_x - setting_y - setting_height
        cam_y = setting_x
        cam_w = setting_height
        cam_h = settings_width
    elif norm_rotation == 2:  # 180 degrees clockwise rotation
        # User view is upside down and mirrored relative to camera view
        cam_x = resolution_x - setting_x - settings_width
        cam_y = resolution_y - setting_y - setting_height
        cam_w = settings_width
        cam_h = setting_height
    elif norm_rotation == 3:  # 270 degrees clockwise rotation (or 90 degrees CCW)
        # User's X-axis -> Camera's Y-axis (bottom to top)
        # User's Y-axis -> Camera's X-axis (left to right)
        cam_x = setting_y
        cam_y = resolution_y - setting_x - settings_width
        cam_w = setting_height
        cam_h = settings_width
    else:
        # This case should not be reached due to modulo 4, but as a fallback:
        logger.error("Invalid norm rotation value")
        raise ValueError(f"Unexpected normalized rotation: {norm_rotation}")

    # Check if the window is outside the camera sensor boundaries
    if cam_x + cam_w > resolution_x or cam_y + cam_h > resolution_y:
        logger.error(
            f"Calculated AF window (x:{cam_x}, y:{cam_y}, w:{cam_w}, h:{cam_h}) "
            f"is outside the native camera sensor boundaries ({resolution_x}x{resolution_y}). "
            f"Original user settings: AF_window=({setting_x}, {setting_y}, {settings_width}, {setting_height}), ")
        raise ValueError(
            f"Calculated AF window (x:{cam_x}, y:{cam_y}, w:{cam_w}, h:{cam_h}) "
            f"is outside the native camera sensor boundaries ({resolution_x}x{resolution_y}). "
            f"Original user settings: AF_window=({setting_x}, {setting_y}, {settings_width}, {setting_height}), "
        )

    return cam_x, cam_y, cam_w, cam_h

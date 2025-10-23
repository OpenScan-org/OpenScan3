import os
import re
import gc
from pathlib import Path
from collections import defaultdict
from typing import Optional

import numpy as np
import cv2

try:
    from turbojpeg import TurboJPEG

    turbo_jpeg = TurboJPEG()
    TURBO_AVAILABLE = True
except ImportError:
    TURBO_AVAILABLE = False

DEFAULT_JPEG_QUALITY = 90


def load_image(path: str) -> np.ndarray:
    if TURBO_AVAILABLE:
        with open(path, 'rb') as f:
            return turbo_jpeg.decode(f.read())
    return cv2.imread(path, cv2.IMREAD_COLOR)


def save_image(path: str, img: np.ndarray, quality: int = DEFAULT_JPEG_QUALITY):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    if TURBO_AVAILABLE:
        with open(path, 'wb') as f:
            f.write(turbo_jpeg.encode(img, quality=quality))
    else:
        cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, quality])


def resize_to_gray(img: np.ndarray, scale: float) -> np.ndarray:
    h, w = img.shape[:2]
    small = cv2.resize(img, (int(w * scale), int(h * scale)))
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0


def compute_focus_map(img: np.ndarray, downscale: float) -> np.ndarray:
    h, w = img.shape[:2]
    gray = resize_to_gray(img, downscale)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    energy = laplacian * laplacian
    return cv2.resize(energy, (w, h))


def compute_alignment_transform(img: np.ndarray, ref_gray: np.ndarray, scale: float = 0.25) -> np.ndarray:
    gray = resize_to_gray(img, scale)
    gray = cv2.boxFilter(gray, -1, (5, 5))
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 1e-6)

    try:
        _, warp = cv2.findTransformECC(
            ref_gray, gray, warp, cv2.MOTION_AFFINE, criteria,
            inputMask=None, gaussFiltSize=3
        )
        warp[:, 2] /= scale
    except cv2.error:
        pass

    return warp


def apply_transform(img: np.ndarray, transform: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.warpAffine(
        img, transform, (w, h),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_CONSTANT
    )


def find_image_batches(directory: str, stack_size: Optional[int] = None) -> dict[int, list[str]]:
    """Find and group images into focus stack batches.

    Finds images matching pattern: scan{scan_idx}_{position}_fs{stack_idx}.jpg
    Groups by position number (the middle number).

    Args:
        directory: Directory containing scan images
        stack_size: Expected stack size. If None, auto-detects from first batch

    Returns:
        Dictionary mapping position number to list of image paths (sorted by stack index)
    """
    pattern = re.compile(r'scan\d+_(\d+)_fs(\d+)\.jpg')
    batches = defaultdict(list)

    for filepath in Path(directory).glob('*.jpg'):
        if match := pattern.match(filepath.name):
            position = int(match.group(1))
            fs_index = int(match.group(2))
            batches[position].append((fs_index, str(filepath)))

    # Auto-detect stack size from first complete batch
    if stack_size is None and batches:
        first_batch = next(iter(batches.values()))
        stack_size = len(first_batch)

    result = {}
    for position, files in batches.items():
        files.sort()
        paths = [path for _, path in files]
        if len(paths) == stack_size:
            result[position] = paths

    return result


class FocusStacker:
    """Focus stacking with optional calibration for alignment transforms."""

    def __init__(self, downscale: float = 0.25, jpeg_quality: Optional[int] = None):
        """Initialize focus stacker.

        Args:
            downscale: Downscale factor for focus map computation (lower = faster but less accurate)
            jpeg_quality: JPEG quality for output (0-100). If None, uses DEFAULT_JPEG_QUALITY
        """
        self.transforms: Optional[list[np.ndarray]] = None
        self.downscale = downscale
        self.jpeg_quality = jpeg_quality or DEFAULT_JPEG_QUALITY

    def calibrate_from_directory(self, directory: str, num_batches: int = 1) -> list[np.ndarray]:
        """Calibrate from batches in a directory.

        Args:
            directory: Directory containing focus stack images
            num_batches: Number of batches to use for calibration (1 = single, >1 = averaged)

        Returns:
            Computed or averaged transforms
        """
        batches = find_image_batches(directory)
        if not batches:
            raise ValueError(f"No focus stack batches found in {directory}")

        batch_list = list(batches.values())

        if num_batches == 1:
            # Single batch calibration
            transforms = self.calibrate(batch_list[0])
        else:
            # Multi-batch calibration with evenly spaced samples
            num_batches = min(num_batches, len(batch_list))
            indices = np.linspace(0, len(batch_list) - 1, num_batches, dtype=int)
            selected_batches = [batch_list[i] for i in indices]
            transforms = self.calibrate_multi(selected_batches)

        self.transforms = transforms
        return transforms

    def calibrate(self, image_paths: list[str]) -> list[np.ndarray]:
        """Compute alignment transforms from a reference batch.

        Args:
            image_paths: List of image paths to calibrate from

        Returns:
            List of transforms (one per image)
        """
        n = len(image_paths)
        ref_idx = n // 2

        ref_img = load_image(image_paths[ref_idx])
        ref_gray_small = resize_to_gray(ref_img, self.downscale)
        ref_gray = cv2.boxFilter(ref_gray_small, -1, (5, 5))

        transforms = [None] * n
        transforms[ref_idx] = np.eye(2, 3, dtype=np.float32)

        for idx in range(n):
            if idx == ref_idx:
                continue
            img = load_image(image_paths[idx])
            transforms[idx] = compute_alignment_transform(img, ref_gray, self.downscale)
            del img

        return transforms

    def calibrate_multi(self, batch_list: list[list[str]]) -> list[np.ndarray]:
        """Calibrate from multiple batches and average the transforms.

        Args:
            batch_list: List of image path lists (one per calibration batch)

        Returns:
            Averaged transforms
        """
        all_transforms = [self.calibrate(batch) for batch in batch_list]
        n_images = len(all_transforms[0])

        averaged = []
        for idx in range(n_images):
            transforms = [t[idx] for t in all_transforms if t[idx] is not None]
            avg = np.mean(transforms, axis=0).astype(np.float32) if transforms else np.eye(2, 3, dtype=np.float32)
            averaged.append(avg)

        self.transforms = averaged
        return averaged

    def stack(self, image_paths: list[str], output_path: str, transforms: Optional[list[np.ndarray]] = None):
        """Stack images using focus stacking.

        Args:
            image_paths: List of image paths to stack
            output_path: Output path for stacked image
            transforms: Optional transforms to use. If None, uses self.transforms
        """
        n = len(image_paths)

        if n == 1:
            img = load_image(image_paths[0])
            save_image(output_path, img, self.jpeg_quality)
            return

        transforms = transforms or self.transforms
        if transforms is None:
            raise ValueError("No transforms available. Run calibrate() first or provide transforms.")

        ref_idx = n // 2
        ref_img = load_image(image_paths[ref_idx])
        h, w = ref_img.shape[:2]

        ref_gray_small = resize_to_gray(ref_img, self.downscale)
        laplacian = cv2.Laplacian(ref_gray_small, cv2.CV_32F, ksize=3)
        energy = laplacian * laplacian
        best_focus = cv2.resize(energy, (w, h))

        result = ref_img.copy()

        for idx in range(n):
            if idx == ref_idx:
                continue

            img = load_image(image_paths[idx])
            aligned = apply_transform(img, transforms[idx])
            focus = compute_focus_map(aligned, self.downscale)

            better = focus > best_focus
            result = np.where(better[..., np.newaxis], aligned, result)
            best_focus = np.where(better, focus, best_focus)

            del img, aligned, focus
            if idx % 3 == 0:
                gc.collect()

        save_image(output_path, result, self.jpeg_quality)

    def stack_directory(self, directory: str, output_dir: Optional[str] = None) -> list[str]:
        """Stack all batches in a directory.

        Args:
            directory: Directory containing focus stack images
            output_dir: Output directory for stacked images. If None, uses directory + '_stacked'

        Returns:
            List of output file paths
        """
        if self.transforms is None:
            raise ValueError("No transforms available. Run calibrate_from_directory() first.")

        batches = find_image_batches(directory)
        if not batches:
            raise ValueError(f"No focus stack batches found in {directory}")

        if output_dir is None:
            output_dir = directory + '_stacked'
        os.makedirs(output_dir, exist_ok=True)

        output_paths = []
        for position, image_paths in sorted(batches.items()):
            output_path = os.path.join(output_dir, f"stacked_{position:03d}.jpg")
            self.stack(image_paths, output_path)
            output_paths.append(output_path)

        return output_paths
        """Stack images using focus stacking.

        Args:
            image_paths: List of image paths to stack
            output_path: Output path for stacked image
            transforms: Optional transforms to use. If None, uses self.transforms
        """
        n = len(image_paths)

        if n == 1:
            img = load_image(image_paths[0])
            save_image(output_path, img, self.jpeg_quality)
            return

        transforms = transforms or self.transforms
        if transforms is None:
            raise ValueError("No transforms available. Run calibrate() first or provide transforms.")

        ref_idx = n // 2
        ref_img = load_image(image_paths[ref_idx])
        h, w = ref_img.shape[:2]

        ref_gray_small = resize_to_gray(ref_img, self.downscale)
        laplacian = cv2.Laplacian(ref_gray_small, cv2.CV_32F, ksize=3)
        energy = laplacian * laplacian
        best_focus = cv2.resize(energy, (w, h))

        result = ref_img.copy()

        for idx in range(n):
            if idx == ref_idx:
                continue

            img = load_image(image_paths[idx])
            aligned = apply_transform(img, transforms[idx])
            focus = compute_focus_map(aligned, self.downscale)

            better = focus > best_focus
            result = np.where(better[..., np.newaxis], aligned, result)
            best_focus = np.where(better, focus, best_focus)

            del img, aligned, focus
            if idx % 3 == 0:
                gc.collect()

        save_image(output_path, result, self.jpeg_quality)
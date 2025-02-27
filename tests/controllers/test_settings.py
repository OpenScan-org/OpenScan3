import unittest
from dataclasses import dataclass
from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import tempfile
import json
import os

from app.controllers.settings import SettingsManager


# Test models
class TestSettings(BaseModel):
    value1: Optional[int] = None
    value2: Optional[str] = None


@dataclass
class TestModel:
    settings: TestSettings
    settings_file: Optional[str] = None


class TestSettingsManager(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for test settings files
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        # Clean up temporary files
        import shutil
        shutil.rmtree(self.test_dir)

    def test_init_without_settings_file(self):
        """Test that SettingsManager correctly initializes with None settings"""
        model = TestModel(settings=TestSettings())
        manager = SettingsManager(model)
        self.assertIsNotNone(model.settings)
        self.assertIsInstance(model.settings, TestSettings)

    def test_init_with_existing_settings(self):
        """Test that SettingsManager keeps existing settings"""
        settings = TestSettings(value1=42, value2="test")
        model = TestModel(settings=settings)
        manager = SettingsManager(model)
        self.assertEqual(manager.get_setting("value1"), 42)
        self.assertEqual(manager.get_setting("value2"), "test")

    def test_load_settings_from_file(self):
        """Test loading settings from a file"""
        # Create a test settings file
        settings_file = "test_settings.json"
        settings_path = Path(self.test_dir) / settings_file
        test_settings = {"value1": 42, "value2": "test"}
        with open(settings_path, 'w') as f:
            json.dump(test_settings, f)

        model = TestModel(
            settings=TestSettings(),
            settings_file=settings_file
        )
        manager = SettingsManager(
            model,
            settings_file=settings_file,
            settings_dir=self.test_dir
        )

        manager.load_from_file()

        self.assertEqual(manager.get_setting("value1"), 42)
        self.assertEqual(manager.get_setting("value2"), "test")

    def test_settings_callback(self):
        """Test that callback is called when settings change"""
        callback_called = False

        def on_settings_changed(settings):
            nonlocal callback_called
            callback_called = True

        model = TestModel(settings=TestSettings())
        manager = SettingsManager(model, on_settings_changed=on_settings_changed)
        manager.set_setting("value1", 42)

        self.assertTrue(callback_called)

    def test_autosave(self):
        """Test that settings are automatically saved when autosave is enabled"""
        settings_file = "test_settings.json"
        settings_path = Path(self.test_dir) / settings_file

        model = TestModel(settings=TestSettings(), settings_file=settings_file)
        manager = SettingsManager(
            model,
            settings_file=settings_file,
            settings_dir=self.test_dir,
            autosave=True
        )

        manager.set_setting("value1", 42)

        # Check if file exists and contains correct settings
        self.assertTrue(settings_path.exists())
        with open(settings_path, 'r') as f:
            saved_settings = json.load(f)
        self.assertEqual(saved_settings["value1"], 42)

    def test_get_all_settings(self):
        """Test getting all settings at once"""
        settings = TestSettings(value1=42, value2="test")
        model = TestModel(settings=settings)
        manager = SettingsManager(model)

        all_settings = manager.get_all_settings()fix: controllers are now properly seperated by controller type
        self.assertEqual(all_settings.value1, 42)
        self.assertEqual(all_settings.value2, "test")

    def test_replace_settings(self):
        """Test replacing all settings at once"""
        old_settings = TestSettings(value1=42, value2="old")
        new_settings = TestSettings(value1=99, value2="new")
        model = TestModel(settings=old_settings)
        manager = SettingsManager(model)

        # Test without callback
        manager.replace_settings(new_settings, execute_callback=False)
        self.assertEqual(manager.get_setting("value1"), 99)
        self.assertEqual(manager.get_setting("value2"), "new")

        # Test with callback
        callback_called = False

        def on_settings_changed(settings):
            nonlocal callback_called
            callback_called = True

        manager = SettingsManager(model, on_settings_changed=on_settings_changed)
        manager.replace_settings(old_settings)
        self.assertTrue(callback_called)

    def test_save_to_file(self):
        """Test explicitly saving settings to file"""
        settings_file = "test_settings.json"
        settings_path = Path(self.test_dir) / settings_file

        settings = TestSettings(value1=42, value2="test")
        model = TestModel(settings=settings)
        manager = SettingsManager(
            model,
            settings_file=settings_file,
            settings_dir=self.test_dir
        )

        manager.save_to_file()

        # Check if file exists and contains correct settings
        self.assertTrue(settings_path.exists())
        with open(settings_path, 'r') as f:
            saved_settings = json.load(f)
        self.assertEqual(saved_settings["value1"], 42)
        self.assertEqual(saved_settings["value2"], "test")

    def test_invalid_setting_name(self):
        """Test accessing non-existent setting"""
        model = TestModel(settings=TestSettings())
        manager = SettingsManager(model)

        with self.assertRaises(ValueError):
            manager.get_setting("non_existent")

        with self.assertRaises(ValueError):
            manager.set_setting("non_existent", 42)

    def test_invalid_settings_file(self):
        """Test loading from non-existent or invalid settings file"""
        model = TestModel(settings=TestSettings())
        manager = SettingsManager(
            model,
            settings_file="non_existent.json",
            settings_dir=self.test_dir
        )

        # Should not raise an error, just skip loading
        manager.load_from_file()

        # Create invalid JSON file
        settings_file = "invalid.json"
        settings_path = Path(self.test_dir) / settings_file
        with open(settings_path, 'w') as f:
            f.write("invalid json")

        model = TestModel(settings=TestSettings())
        manager = SettingsManager(
            model,
            settings_file=settings_file,
            settings_dir=self.test_dir
        )

        # Should not raise an error, just skip loading
        manager.load_from_file()

    def test_settings_dir_creation(self):
        """Test that settings directory is created if it doesn't exist"""
        test_dir = Path(self.test_dir) / "non_existent_dir"
        model = TestModel(settings=TestSettings())
        manager = SettingsManager(
            model,
            settings_file="test.json",
            settings_dir=str(test_dir)
        )

        manager.save_to_file()
        self.assertTrue(test_dir.exists())

if __name__ == '__main__':
    unittest.main()
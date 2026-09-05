import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tools.v1.screenshot_tool as screenshot_tool


class TestScreenshotTool(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("tools.v1.screenshot_tool.pyautogui")
    def test_fullscreen_screenshot(self, mock_pyautogui):
        mock_image = MagicMock()
        mock_pyautogui.screenshot.return_value = mock_image
        screenshot_tool.pyautogui = mock_pyautogui

        target = self.dir_path / "full.png"
        res = screenshot_tool.take_screenshot(output_path=str(target))

        mock_pyautogui.screenshot.assert_called_once_with()
        mock_image.save.assert_called_once()
        self.assertIn("Thành công", res)

    @patch("tools.v1.screenshot_tool.pyautogui")
    def test_region_screenshot(self, mock_pyautogui):
        mock_image = MagicMock()
        mock_pyautogui.screenshot.return_value = mock_image
        screenshot_tool.pyautogui = mock_pyautogui

        target = self.dir_path / "crop.png"
        region = (100, 100, 400, 300)
        res = screenshot_tool.take_screenshot(
            output_path=str(target), region=region
        )

        mock_pyautogui.screenshot.assert_called_once_with(region=region)
        self.assertIn("vùng (100, 100, 400, 300)", res)

    def test_invalid_region_format(self):
        res = screenshot_tool.take_screenshot(region=(100, -50))  # Sai định dạng
        self.assertIn("Lỗi: 'region' phải là tuple 4 số", res)


if __name__ == "__main__":
    unittest.main()
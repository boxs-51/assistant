from pathlib import Path
from typing import Optional, Tuple, Union

try:
    import pyautogui
except ImportError:
    pyautogui = None


def take_screenshot(
    output_path: str = "screenshot.png",
    region: Optional[Tuple[int, int, int, int]] = None,
) -> str:
    """Chụp ảnh toàn màn hình hoặc một vùng cụ thể và lưu thành file.

    Args:
        output_path (str): Đường dẫn file ảnh đầu ra (mặc định:
          'screenshot.png').
        region (Tuple[int, int, int, int], optional): Vùng cần chụp theo định
          dạng (x, y, width, height). Để None nếu muốn chụp toàn màn hình.

    Returns:
        str: Thông báo kết quả thực thi kèm đường dẫn file hoặc thông báo lỗi.
    """
    if pyautogui is None:
        return "Lỗi: Thư viện 'pyautogui' chưa được cài đặt. Vui lòng chạy 'pip install pyautogui pillow'."

    try:
        save_path = Path(output_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if region:
            if len(region) != 4 or any(
                not isinstance(v, (int, float)) or v < 0 for v in region
            ):
                return "Lỗi: 'region' phải là tuple 4 số dương dạng (x, y, width, height)."
            image = pyautogui.screenshot(region=region)
            info_str = f"vùng {region}"
        else:
            image = pyautogui.screenshot()
            info_str = "toàn màn hình"

        image.save(save_path)
        return f"Thành công: Đã chụp {info_str} và lưu vào '{save_path.resolve()}'."

    except Exception as e:
        return f"Lỗi khi chụp ảnh màn hình: {str(e)}"
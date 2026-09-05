import requests
from bs4 import BeautifulSoup


def scrape_webpage(url: str, timeout: int = 10) -> str:
    """Tải và trích xuất nội dung văn bản sạch từ một URL trang web.

    Args:
        url (str): Đường dẫn URL của trang web cần đọc (http:// hoặc https://).
        timeout (int): Thời gian chờ kết nối tối đa tính bằng giây (mặc định:
          10).

    Returns:
        str: Nội dung văn bản đã làm sạch hoặc thông báo lỗi.
    """
    if not url or not url.startswith(("http://", "https://")):
        return "Lỗi: URL không hợp lệ (phải bắt đầu bằng http:// hoặc https://)."

    # Giả lập User-Agent trình duyệt để tránh bị chặn 403 Forbidden
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()

        # Tự động khắc phục lỗi font chữ/encoding
        if response.encoding is None or response.encoding == "ISO-8859-1":
            response.encoding = response.apparent_encoding

        soup = BeautifulSoup(response.text, "html.parser")

        # Loại bỏ các thẻ rác không chứa nội dung chính
        for element in soup([
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "noscript",
            "svg",
            "form",
            "aside",
        ]):
            element.decompose()

        # Lấy văn bản và dọn dẹp dòng trống/khoảng trắng thừa
        text = soup.get_text(separator="\n")
        lines = (line.strip() for line in text.splitlines())
        clean_text = "\n".join(chunk for chunk in lines if chunk)

        if not clean_text:
            return f"Cảnh báo: Trang web '{url}' không có nội dung văn bản để trích xuất."

        return clean_text

    except requests.exceptions.Timeout:
        return f"Lỗi: Kết nối tới '{url}' bị quá thời gian chờ (Timeout)."
    except requests.exceptions.HTTPError as e:
        return f"Lỗi HTTP khi truy cập '{url}': {e.response.status_code} {e.response.reason}"
    except requests.exceptions.RequestException as e:
        return f"Lỗi khi tải trang '{url}': {str(e)}"
    except Exception as e:
        return f"Lỗi không xác định khi đọc trang '{url}': {str(e)}"
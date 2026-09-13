export function initResizers() {
  const resizerLeft = document.getElementById('resizer-left');
  const resizerRight = document.getElementById('resizer-right');
  const sidebar = document.getElementById('sidebar');
  const editorPanel = document.getElementById('editor-panel');
  const appLayout = document.getElementById('app-layout');

  if (!resizerLeft || !resizerRight || !sidebar || !editorPanel || !appLayout) return;

  // Sidebar Trái: Min 12%, Max 60%
  makeResizable(resizerLeft, sidebar, appLayout, 'left', 12, 60);

  // Editor Panel Phải: Min 15%, Max 60%
  makeResizable(resizerRight, editorPanel, appLayout, 'right', 15, 60);
}

function makeResizable(resizer, targetElement, container, direction, minPercent, maxPercent) {
  let startX = 0;
  let startWidthPx = 0;
  let containerWidthPx = 0;
  let animationFrameId = null;

  const onMouseMove = (e) => {
    // Dùng requestAnimationFrame để khóa nhịp cập nhật theo FPS màn hình (60/120Hz)
    if (animationFrameId) return;

    animationFrameId = requestAnimationFrame(() => {
      const deltaX = e.clientX - startX;
      // Editor kéo sang trái làm tăng chiều rộng nên đảo chiều delta
      const actualDelta = direction === 'left' ? deltaX : -deltaX;

      const newWidthPx = startWidthPx + actualDelta;
      let newPercent = (newWidthPx / containerWidthPx) * 100;

      // Giới hạn theo tỷ lệ %
      if (newPercent < minPercent) newPercent = minPercent;
      if (newPercent > maxPercent) newPercent = maxPercent;

      targetElement.style.width = `${newPercent}%`;
      animationFrameId = null;
    });
  };

  const onMouseUp = () => {
    resizer.classList.remove('dragging');
    document.body.classList.remove('is-resizing'); // Cho phép chọn văn bản lại

    if (animationFrameId) {
      cancelAnimationFrame(animationFrameId);
      animationFrameId = null;
    }

    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
  };

  resizer.addEventListener('mousedown', (e) => {
    startX = e.clientX;
    
    // Chỉ đọc kích thước 1 lần duy nhất khi BẮT ĐẦU kéo (tránh Layout Thrashing)
    startWidthPx = targetElement.getBoundingClientRect().width;
    containerWidthPx = container.getBoundingClientRect().width;

    resizer.classList.add('dragging');
    document.body.classList.add('is-resizing'); // Vô hiệu hóa bôi đen text khi đang kéo

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  });
}
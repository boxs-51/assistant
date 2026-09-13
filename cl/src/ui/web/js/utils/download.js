export function triggerFileDownload(filename, content = '', mimeType = '') {
  const value = String(content || '');
  let url = value;
  let isCreatedUrl = false;

  try {
    if (value.startsWith('data:')) {
      url = value;
    } else if (/^base64:/i.test(value)) {
      const base64 = value.slice(value.indexOf(':') + 1);
      const binary = atob(base64);
      const bytes = Uint8Array.from(binary, char => char.charCodeAt(0));
      const blob = new Blob([bytes], { type: mimeType || 'application/octet-stream' });
      url = URL.createObjectURL(blob);
      isCreatedUrl = true;
    } else if (/^https?:/i.test(value) || /^blob:/i.test(value)) {
      url = value;
    } else {
      const blob = new Blob([value], { type: mimeType || 'text/plain;charset=utf-8' });
      url = URL.createObjectURL(blob);
      isCreatedUrl = true;
    }

    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } finally {
    if (isCreatedUrl) {
      URL.revokeObjectURL(url);
    }
  }
}
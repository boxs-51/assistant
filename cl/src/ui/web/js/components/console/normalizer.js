// Helper chuẩn hóa object file đính kèm thành GatewayAttachment đúng Schema Python
function buildGatewayAttachment(fileObj) {
  if (!fileObj) return null;
  const path = fileObj.path || fileObj.uri || null;
  const filename = fileObj.filename || (path ? path.split(/[\\\/]/).pop() : 'Attachment');

  return {
    id: fileObj.id || null,
    filename: filename,
    mime_type: fileObj.mime_type || fileObj.type || 'application/octet-stream',
    size: fileObj.size || 0,
    extension: fileObj.extension || (filename.includes('.') ? filename.split('.').pop() : null),
    uri: path,
    base64_data: fileObj.b64_data || fileObj.base64_data || fileObj.content || null,
    provider_file_id: fileObj.provider_file_id || null,
    source: fileObj.source || (path ? 'local' : (fileObj.b64_data || fileObj.base64_data) ? 'base64' : 'local')
  };
}

// Helper phân loại và chuyển đổi từng phần tử thành MessageContentPart chuẩn
function processContentPart(item, rootMetadata = {}) {
  if (!item) return null;

  // 1. Chuỗi thuần túy -> Text Part
  if (typeof item === 'string') {
    return item.trim() ? { type: 'text', text: item } : null;
  }

  if (typeof item !== 'object') return null;

  const citations = item.citations || item.metadata?.citations || rootMetadata?.citations || null;
  const metadata = { ...(item.metadata || {}), ...(citations ? { citations } : {}) };
  const rawType = (item.type || '').toLowerCase();

  // 2. Dynamic Type Mapping theo MessageContentType Enum

  // Block suy nghĩ (THINKING / THOUGHT)
  if (rawType === 'thinking' || rawType === 'thought' || item.thinking || item.thought) {
    return {
      type: 'thinking',
      text: item.text || item.thinking || item.thought || (typeof item.data === 'string' ? item.data : ''),
      ...(Object.keys(metadata).length ? { metadata } : {})
    };
  }

  // Block văn bản (TEXT)
  if (rawType === 'text' || typeof item.text === 'string') {
    const textVal = item.text || (typeof item.data === 'string' ? item.data : item.content);
    if (!textVal) return null;
    return {
      type: 'text',
      text: textVal,
      ...(Object.keys(metadata).length ? { metadata } : {})
    };
  }

  // Block hình ảnh (IMAGE)
  if (rawType === 'image' || item.mime_type?.startsWith('image/')) {
    return {
      type: 'image',
      data: {
        attachment: buildGatewayAttachment(item.data?.attachment || item.attachment || item),
        detail: item.detail || 'auto'
      }
    };
  }

  // Block âm thanh (AUDIO)
  if (rawType === 'audio' || item.mime_type?.startsWith('audio/')) {
    return {
      type: 'audio',
      data: {
        attachment: buildGatewayAttachment(item.data?.attachment || item.attachment || item)
      }
    };
  }

  // Block video (VIDEO)
  if (rawType === 'video' || item.mime_type?.startsWith('video/')) {
    return {
      type: 'video',
      data: {
        attachment: buildGatewayAttachment(item.data?.attachment || item.attachment || item)
      }
    };
  }

  // Block liên kết URL (URL)
  if (rawType === 'url' || item.url) {
    return {
      type: 'url',
      data: {
        url: item.url || item.data?.url,
        crawl: item.crawl ?? true,
        title: item.title || null
      }
    };
  }

  // Block tài liệu / tệp tin (FILE / DOCUMENT / ATTACHMENT)
  if (rawType === 'file' || rawType === 'document' || rawType === 'attachment' || item.filename || item.mime_type) {
    return {
      type: 'file',
      data: {
        attachment: buildGatewayAttachment(item.data?.attachment || item.attachment || item)
      }
    };
  }

  // Kết quả Tool (TOOL_RESULT)
  if (rawType === 'tool_result') {
    return {
      type: 'tool_result',
      text: typeof item.content === 'string' ? item.content : JSON.stringify(item.content || item.data || '')
    };
  }

  // Fallback nếu có thuộc tính data là string
  if (item.data && typeof item.data === 'string') {
    return { type: 'text', text: item.data };
  }

  return null;
}

export function normalizeToContentParts(data) {
  if (!data) return [];

  const parts = [];

  // 1. Xử lý dữ liệu nằm trong data.data
  const innerData = data.data?.response?.choices[0]?.message;
  if (innerData) {
    if (Array.isArray(innerData.content)) {
      innerData.content.forEach(item => {
        const part = processContentPart(item, innerData.metadata);
        if (part) parts.push(part);
      });
    } else if (typeof innerData.content === 'string' || typeof innerData.text === 'string' || innerData.thought) {
      const part = processContentPart(innerData, innerData.metadata);
      if (part) parts.push(part);
    }
  }

  // 4. Xử lý mảng files đính kèm
  // if (Array.isArray(innerData?.files)) {
  //   files.forEach(f => {
  //     parts.push({
  //       type: 'file',
  //       data: {
  //         attachment: buildGatewayAttachment(f)
  //       }
  //     });
  //   });
  // }

  return parts;
}
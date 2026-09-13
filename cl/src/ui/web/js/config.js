// Lấy thư viện marked từ phạm vi global (do nạp qua thẻ script ở index.html)
const marked = window.marked;

marked.setOptions({
  highlight: function(code, lang) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
    return hljs.highlight(code, { language }).value;
  },
  breaks: true
});

export { marked };
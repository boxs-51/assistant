const SPECIAL_FILES = {
  'dockerfile': '🐳',
  'package.json': '📦',
  'tsconfig.json': '⚙️',
  'workspace.json': '⚙️',
  '.gitignore': '👁️‍🗨️',
  '.env': '🔑',
  'readme.md': '📖',
  'makefile': '🛠️'
};

const FILE_ICONS = {
  // Programming & Scripts
  py: '🐍', js: '🟨', ts: '🟦', jsx: '⚛️', tsx: '⚛️',
  cpp: '⚡', c: '⚡', h: '🛡️', hpp: '🛡️', cs: '🔷', java: '☕',
  sh: '🐚', bat: '🐚', ps1: '🐚', rb: '💎', go: '🐹', rs: '🦀',
  
  // Web & Styles
  html: '🌐', css: '🎨', scss: '🎨', less: '🎨', php: '🐘',
  
  // Data & Config
  json: '⚙️', yaml: '📋', yml: '📋', xml: '📰', toml: '⚙️',
  sql: '🗄️', db: '🗄️', sqlite: '🗄️', env: '🔑',
  
  // Documents & Media
  md: '📝', txt: '📄', pdf: '📕', doc: '📘', docx: '📘',
  png: '🖼️', jpg: '🖼️', jpeg: '🖼️', svg: '🖼️', ico: '🖼️', gif: '🎞️'
};

export function getFileIcon(filename) {
  if (!filename) return '📄';
  const lowerName = filename.toLowerCase();

  // 1. Kiểm tra file tên đặc biệt
  if (SPECIAL_FILES[lowerName]) {
    return SPECIAL_FILES[lowerName];
  }

  // 2. Lấy extension
  const parts = lowerName.split('.');
  if (parts.length > 1) {
    const ext = parts.pop();
    return FILE_ICONS[ext] || '📄';
  }

  return '📄';
}
import { openFileInEditor } from './editor.js';
import { getFileIcon } from '../utils/fileIcons.js';

let clipboard = null; 
let currentSelectedItem = null; 
let contextMenuElem = null;
let rootFolderName = 'WORKSPACE';
let isRootOpen = true; // Trạng thái đóng/mở của Root

function sortTreeItems(items) {
  if (!items || !Array.isArray(items)) return [];
  return items
    .slice()
    .sort((a, b) => {
      if (a.type === 'folder' && b.type !== 'folder') return -1;
      if (a.type !== 'folder' && b.type === 'folder') return 1;
      return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    })
    .map(item => {
      if (item.type === 'folder' && item.children) {
        return { ...item, children: sortTreeItems(item.children) };
      }
      return item;
    });
}

function getParentPath(filePath) {
  if (!filePath) return '';
  const parts = filePath.split(/[/\\]/);
  parts.pop();
  return parts.join('/');
}

function getTargetFolderPath() {
  if (!currentSelectedItem || currentSelectedItem.isRoot) return '';
  if (currentSelectedItem.type === 'folder') return currentSelectedItem.path;
  return getParentPath(currentSelectedItem.path);
}

// ==========================================
// CONTEXT MENU & ACTIONS
// ==========================================
function createContextMenu() {
  if (document.getElementById('sidebar-context-menu')) return;

  contextMenuElem = document.createElement('div');
  contextMenuElem.id = 'sidebar-context-menu';
  contextMenuElem.className = 'context-menu hidden';
  document.body.appendChild(contextMenuElem);

  document.addEventListener('click', () => hideContextMenu());
}

function showContextMenu(e, item) {
  e.preventDefault();
  e.stopPropagation();

  if (!contextMenuElem) createContextMenu();

  if (item && item.element) {
    ExplorerPage.setActiveItem(item.element, item);
  } else {
    ExplorerPage.clearActiveItem();
  }

  const hasClipboard = !!clipboard;
  const isRoot = !item || item.isRoot;

  contextMenuElem.innerHTML = `
    <div class="context-menu-item" data-action="new-file">
      <span class="icon">📄</span> Tạo tệp mới
    </div>
    <div class="context-menu-item" data-action="new-folder">
      <span class="icon">📁</span> Tạo thư mục mới
    </div>
    <div class="context-menu-divider"></div>
    <div class="context-menu-item ${isRoot ? 'disabled' : ''}" data-action="copy">
      <span class="icon">📋</span> Sao chép <span class="shortcut">Ctrl+C</span>
    </div>
    <div class="context-menu-item ${isRoot ? 'disabled' : ''}" data-action="cut">
      <span class="icon">✂️</span> Di chuyển <span class="shortcut">Ctrl+X</span>
    </div>
    <div class="context-menu-item ${hasClipboard ? '' : 'disabled'}" data-action="paste">
      <span class="icon">📥</span> Dán <span class="shortcut">Ctrl+V</span>
    </div>
    <div class="context-menu-divider"></div>
    <div class="context-menu-item ${isRoot ? 'disabled' : ''}" data-action="rename">
      <span class="icon">✏️</span> Đổi tên <span class="shortcut">F2</span>
    </div>
    <div class="context-menu-item danger ${isRoot ? 'disabled' : ''}" data-action="delete">
      <span class="icon">🗑️</span> Xóa <span class="shortcut">Delete</span>
    </div>
  `;

  contextMenuElem.style.left = `${e.clientX}px`;
  contextMenuElem.style.top = `${e.clientY}px`;
  contextMenuElem.classList.remove('hidden');

  contextMenuElem.querySelectorAll('.context-menu-item').forEach(btn => {
    btn.addEventListener('click', (evt) => {
      evt.stopPropagation();
      if (btn.classList.contains('disabled')) return;
      executeAction(btn.dataset.action, currentSelectedItem);
      hideContextMenu();
    });
  });
}

function hideContextMenu() {
  if (contextMenuElem) contextMenuElem.classList.add('hidden');
}

// Xử lý tạo File/Folder bằng Inline Input (không dùng prompt native)
function renderInlineInput(type) {
  const targetFolder = getTargetFolderPath();
  let parentContainer;

  if (!currentSelectedItem || currentSelectedItem.isRoot) {
    parentContainer = document.querySelector('.root-children');
  } else if (currentSelectedItem.type === 'folder') {
    parentContainer = currentSelectedItem.element.nextElementSibling;
    if (parentContainer && parentContainer.style.display === 'none') {
      parentContainer.style.display = 'block';
    }
  } else {
    parentContainer = currentSelectedItem.element.parentElement;
  }

  if (!parentContainer) return;

  const inputWrapper = document.createElement('div');
  inputWrapper.className = 'tree-item inline-input-item';
  inputWrapper.innerHTML = `
    <span class="tree-icon">${type === 'file' ? '📄' : '📁'}</span>
    <input type="text" class="tree-inline-input" placeholder="Tên ${type === 'file' ? 'tệp' : 'thư mục'}..." />
  `;

  parentContainer.prepend(inputWrapper);
  const inputEl = inputWrapper.querySelector('input');
  inputEl.focus();

  const handleCreate = async () => {
    const val = inputEl.value.trim();
    if (val) {
      if (type === 'file' && window.pywebview?.api?.create_file) {
        await window.pywebview.api.create_file(targetFolder, val);
      } else if (type === 'folder' && window.pywebview?.api?.create_folder) {
        await window.pywebview.api.create_folder(targetFolder, val);
      }
      await ExplorerPage.load();
    } else {
      inputWrapper.remove();
    }
  };

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleCreate();
    if (e.key === 'Escape') inputWrapper.remove();
  });

  inputEl.addEventListener('blur', () => {
    if (!inputEl.value.trim()) inputWrapper.remove();
  });
}

async function executeAction(action, item) {
  const targetFolder = getTargetFolderPath();

  switch (action) {
    case 'new-file':
      renderInlineInput('file');
      break;

    case 'new-folder':
      renderInlineInput('folder');
      break;

    case 'copy':
      if (item && !item.isRoot) clipboard = { action: 'copy', item };
      break;

    case 'cut':
      if (item && !item.isRoot) clipboard = { action: 'cut', item };
      break;

    case 'paste':
      if (!clipboard) return;
      if (window.pywebview?.api?.paste_item) {
        await window.pywebview.api.paste_item(clipboard.action, clipboard.item.path, targetFolder);
      }
      if (clipboard.action === 'cut') clipboard = null;
      await ExplorerPage.load();
      break;

    case 'rename':
      if (!item || item.isRoot) return;
      renderRenameInput(item);
      break;

    case 'delete':
      if (!item || item.isRoot) return;
      if (confirm(`Bạn có chắc muốn xóa "${item.name}"?`)) {
        if (window.pywebview?.api?.delete_file_content) {
          await window.pywebview.api.delete_file_content(item.path);
        }
        await ExplorerPage.load();
      }
      break;
  }
}

function renderRenameInput(item) {
  const nameSpan = item.element.querySelector('.tree-item-name');
  if (!nameSpan) return;

  const currentName = item.name;
  nameSpan.innerHTML = `<input type="text" class="tree-inline-input" value="${currentName}" />`;
  const inputEl = nameSpan.querySelector('input');
  inputEl.focus();
  inputEl.select();

  const handleRename = async () => {
    const newName = inputEl.value.trim();
    if (newName && newName !== currentName) {
      if (window.pywebview?.api?.rename_file_content) {
        await window.pywebview.api.rename_file_content(item.path, newName);
      }
      await ExplorerPage.load();
    } else {
      nameSpan.textContent = currentName;
    }
  };

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleRename();
    if (e.key === 'Escape') nameSpan.textContent = currentName;
  });

  inputEl.addEventListener('blur', handleRename);
}

function initHotkeys() {
  document.addEventListener('keydown', (e) => {
    const activeTag = document.activeElement?.tagName;
    if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;

    if (e.ctrlKey && e.key.toLowerCase() === 'c' && currentSelectedItem) {
      e.preventDefault();
      executeAction('copy', currentSelectedItem);
    } else if (e.ctrlKey && e.key.toLowerCase() === 'x' && currentSelectedItem) {
      e.preventDefault();
      executeAction('cut', currentSelectedItem);
    } else if (e.ctrlKey && e.key.toLowerCase() === 'v' && clipboard) {
      e.preventDefault();
      executeAction('paste', currentSelectedItem);
    } else if (e.key === 'F2' && currentSelectedItem) {
      e.preventDefault();
      executeAction('rename', currentSelectedItem);
    } else if (e.key === 'Delete' && currentSelectedItem) {
      e.preventDefault();
      executeAction('delete', currentSelectedItem);
    }
  });
}

// ==========================================
// EXPLORER PAGE ENGINE
// ==========================================
export const ExplorerPage = {
  containerId: 'explorer-tree',

  async load() {
    const container = document.getElementById(this.containerId);
    if (!container) return;

    container.innerHTML = '<div class="sidebar-loading">Đang tải file...</div>';

    try {
      let files = [];
      if (window.pywebview?.api?.get_workspace_files) {
        files = await window.pywebview.api.get_workspace_files();
      }
      if (window.pywebview?.api?.get_workspace_info) {
        const info = await window.pywebview.api.get_workspace_info();
        if (info?.name) rootFolderName = info.name;
      }

      const sortedFiles = sortTreeItems(files);
      this.renderRootTree(sortedFiles, container);
      this.bindOuterEvents(container);

    } catch (err) {
      console.error(err);
      container.innerHTML = `<div class="sidebar-error">Lỗi tải danh sách file</div>`;
    }
  },

  // Sự kiện xử lý click & contextmenu vào vùng trống của Sidebar
  bindOuterEvents(container) {
    const pageExplorer = document.getElementById('page-explorer');

    // Deselect khi click vào vùng trống
    pageExplorer.addEventListener('click', (e) => {
      if (!e.target.closest('.tree-item')) {
        this.clearActiveItem();
      }
    });

    // Hiện Custom Context Menu khi nhấp chuột phải vào vùng trống
    pageExplorer.addEventListener('contextmenu', (e) => {
      if (!e.target.closest('.tree-item')) {
        showContextMenu(e, null);
      }
    });
  },

  renderRootTree(files, container) {
    container.innerHTML = '';

    const rootItem = document.createElement('div');
    rootItem.className = `tree-item folder root-folder ${isRootOpen ? 'open' : ''}`;
    rootItem.innerHTML = `
      <span class="folder-arrow">${isRootOpen ? '▼' : '▶'}</span>
      <span class="tree-icon">📦</span>
      <span class="tree-item-name"><strong>${rootFolderName}</strong></span>
      <div class="root-actions">
        <button class="root-btn btn-new-file" title="Tạo tệp mới">📄+</button>
        <button class="root-btn btn-new-folder" title="Tạo thư mục mới">📁+</button>
        <button class="root-btn btn-refresh" title="Làm mới">🔄</button>
      </div>
    `;

    const rootChildrenContainer = document.createElement('div');
    rootChildrenContainer.className = 'folder-children root-children';
    rootChildrenContainer.style.display = isRootOpen ? 'block' : 'none';

    const rootData = { name: rootFolderName, path: '', type: 'folder', isRoot: true, element: rootItem };

    // Toggle đóng/mở Root
    rootItem.addEventListener('click', (e) => {
      if (e.target.closest('.root-actions')) return; // Không đóng/mở khi click nút action
      e.stopPropagation();
      this.setActiveItem(rootItem, rootData);

      isRootOpen = !isRootOpen;
      rootChildrenContainer.style.display = isRootOpen ? 'block' : 'none';
      rootItem.classList.toggle('open', isRootOpen);
      rootItem.querySelector('.folder-arrow').innerText = isRootOpen ? '▼' : '▶';
    });

    // Gán sự kiện trực tiếp cho các nút Action trên rootFolder
    rootItem.querySelector('.btn-new-file').addEventListener('click', (e) => {
      e.stopPropagation();
      this.setActiveItem(rootItem, rootData);
      executeAction('new-file', rootData);
    });

    rootItem.querySelector('.btn-new-folder').addEventListener('click', (e) => {
      e.stopPropagation();
      this.setActiveItem(rootItem, rootData);
      executeAction('new-folder', rootData);
    });

    rootItem.querySelector('.btn-refresh').addEventListener('click', (e) => {
      e.stopPropagation();
      this.load();
    });

    rootItem.addEventListener('contextmenu', (e) => {
      showContextMenu(e, rootData);
    });

    container.appendChild(rootItem);
    container.appendChild(rootChildrenContainer);

    if (files.length === 0) {
      rootChildrenContainer.innerHTML = '<div class="sidebar-empty">Thư mục trống</div>';
      return;
    }

    this.renderTree(files, rootChildrenContainer);
  },

  renderTree(items, parentElem) {
    parentElem.innerHTML = '';

    items.forEach(item => {
      const el = document.createElement('div');
      const itemData = { ...item, element: el };

      if (item.type === 'folder') {
        el.className = 'tree-item folder';
        el.innerHTML = `
          <span class="folder-arrow">▶</span>
          <span class="tree-icon">📁</span>
          <span class="tree-item-name">${item.name}</span>
        `;

        const childContainer = document.createElement('div');
        childContainer.className = 'folder-children';
        childContainer.style.display = 'none';

        el.addEventListener('click', (e) => {
          e.stopPropagation();
          this.setActiveItem(el, itemData);
          const isHidden = childContainer.style.display === 'none';
          childContainer.style.display = isHidden ? 'block' : 'none';
          el.querySelector('.folder-arrow').innerText = isHidden ? '▼' : '▶';
        });

        el.addEventListener('contextmenu', (e) => {
          showContextMenu(e, itemData);
        });

        parentElem.appendChild(el);
        if (item.children && item.children.length > 0) {
          this.renderTree(item.children, childContainer);
          parentElem.appendChild(childContainer);
        }
      } else {
        const icon = getFileIcon(item.name);
        el.className = 'tree-item file';
        el.innerHTML = `
          <span class="tree-icon">${icon}</span>
          <span class="tree-item-name">${item.name}</span>
        `;

        el.addEventListener('click', (e) => {
          e.stopPropagation();
          this.setActiveItem(el, itemData);
          openFileInEditor(item.path, item.name);
        });

        el.addEventListener('contextmenu', (e) => {
          showContextMenu(e, itemData);
        });

        parentElem.appendChild(el);
      }
    });
  },

  setActiveItem(element, item) {
    document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
    element.classList.add('active');
    currentSelectedItem = item;
  },

  clearActiveItem() {
    document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
    currentSelectedItem = null;
  }
};

// ==========================================
// SESSIONS PAGE & INIT
// ==========================================
export const SessionsPage = {
  containerId: 'sessions-list',

  async load() {
    const container = document.getElementById(this.containerId);
    if (!container) return;

    container.innerHTML = '<div class="sidebar-loading">Đang tải phiên làm việc...</div>';

    try {
      let sessions = [];
      if (window.pywebview?.api?.get_sessions) {
        sessions = await window.pywebview.api.get_sessions();
      }
      this.renderSessions(sessions, container);
    } catch (err) {
      console.error(err);
      container.innerHTML = `<div class="sidebar-error">Lỗi tải Session</div>`;
    }
  },

  renderSessions(sessions, parentElem) {
    parentElem.innerHTML = '';
    if (!sessions || sessions.length === 0) {
      parentElem.innerHTML = '<div class="sidebar-empty">Không có session nào</div>';
      return;
    }

    sessions.forEach(session => {
      const el = document.createElement('div');
      el.className = 'session-item';
      el.innerHTML = `
        <span class="session-icon">💬</span> 
        <span class="session-title">${session.name || 'Untitled Session'}</span>
      `;

      el.addEventListener('click', () => {
        document.querySelectorAll('.session-item').forEach(item => item.classList.remove('active'));
        el.classList.add('active');
      });

      parentElem.appendChild(el);
    });
  }
};

export function initSidebar() {
  const sidebar = document.getElementById('sidebar');

  document.querySelectorAll('.btn-toggle-sidebar').forEach(btn => {
    btn.addEventListener('click', () => {
      sidebar.classList.toggle('collapsed');
    });
  });

  const tabButtons = document.querySelectorAll('.sidebar-tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      switchSidebarPage(btn.dataset.page);
    });
  });

  initHotkeys();
  createContextMenu();

  ExplorerPage.load();
  SessionsPage.load();
}

export function switchSidebarPage(pageName) {
  document.getElementById('sidebar')?.classList.toggle('studio-mode', pageName === 'gateway');
  document.querySelectorAll('.sidebar-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.page === pageName);
  });

  document.querySelectorAll('.sidebar-page').forEach(page => {
    page.style.display = page.id === `page-${pageName}` ? 'block' : 'none';
  });
}

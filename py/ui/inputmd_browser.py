"""inputmd_browser.py

Browse and manage the inputMD archive (per-event Markdown cache).

Structure (workspace_root/inputMD/):
  {stem_hash}/           <- Source  (e.g. "blocking_12a3b4c5")
    {run_tag}/           <- Run tag (e.g. "20260201_2105")
      {YYYYMMDD}/        <- Date folder
        *.md             <- Individual event Markdown files
    index_*.md           <- Index files (at source level)

The browser provides:
  Source list | Sub-folder (drill-down) | File list | MD Preview
plus in-place cleanup (move selected to trash).

Sub-folder pane supports drill-down navigation:
  Level 1: run_tag folders (with file counts)
  Level 2: YYYYMMDD date folders under selected run_tag (with file counts)
Search is scoped to the currently selected folder for fast results.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

import markdown as mdlib

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .delete_utils import is_within, trash_paths

# Sentinel value stored in UserRole to indicate "go back" item
_BACK_SENTINEL = "__BACK__"


class InputMdBrowserWidget(QWidget):
    """4-pane browser for workspace_root/inputMD/.

    Pane 1 - Source        : {stem_hash} directories
    Pane 2 - Sub-folder    : drill-down (run_tag -> YYYYMMDD)
    Pane 3 - File          : .md files in selected leaf folder
    Pane 4 - Preview       : rendered Markdown of selected file
    """

    def __init__(self, workspace_root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._workspace_root: Optional[str] = workspace_root
        self._inputmd_root: Optional[str] = (
            os.path.join(workspace_root, "inputMD") if workspace_root else None
        )

        self._search_mode = False
        # Track drill-down state in sub-folder pane
        self._sub_parent_path: Optional[str] = None  # source path (level 1)
        self._sub_current_path: Optional[str] = None  # run_tag path when drilled in (level 2)
        self._sub_level = 1  # 1 = run_tags, 2 = date folders

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- Info label ----
        self._info = QLabel("inputMD: (ワークスペース未設定)")
        self._info.setSizePolicy(self._info.sizePolicy().horizontalPolicy(),
                                 QSizePolicy.Policy.Fixed)
        layout.addWidget(self._info)

        # ---- Search bar ----
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("キーワード（選択中フォルダ内を検索）")
        self._search_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_edit)
        self._btn_search = QPushButton("検索実行")
        self._btn_search.clicked.connect(self._do_search)
        search_row.addWidget(self._btn_search)
        self._btn_clear_search = QPushButton("クリア")
        self._btn_clear_search.clicked.connect(self._clear_search)
        self._btn_clear_search.setEnabled(False)
        search_row.addWidget(self._btn_clear_search)
        self._search_result_label = QLabel("")
        search_row.addWidget(self._search_result_label)
        layout.addLayout(search_row)

        # ---- 4-pane splitter ----
        splitter = QSplitter(Qt.Horizontal)

        self._src_list = QListWidget()
        self._src_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._src_list.setMaximumWidth(200)

        self._sub_list = QListWidget()
        self._sub_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._sub_list.setMaximumWidth(180)

        self._file_list = QListWidget()
        self._file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._preview.setOpenLinks(False)
        self._preview.anchorClicked.connect(self._on_link)

        splitter.addWidget(self._src_list)
        splitter.addWidget(self._sub_list)
        splitter.addWidget(self._file_list)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 2)
        splitter.setStretchFactor(3, 4)
        layout.addWidget(splitter, 1)

        # ---- Bottom toolbar ----
        bottom = QHBoxLayout()
        self._btn_refresh = QPushButton("再読み込み")
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_trash = QPushButton("選択をゴミ箱へ移動")
        self._btn_trash.clicked.connect(self._trash_selected)
        self._btn_trash.setEnabled(False)
        bottom.addWidget(self._btn_refresh)
        bottom.addStretch()
        bottom.addWidget(self._btn_trash)
        layout.addLayout(bottom)

        # ---- Signals ----
        self._src_list.itemSelectionChanged.connect(self._on_select_source)
        self._sub_list.itemSelectionChanged.connect(self._on_select_sub)
        self._file_list.itemSelectionChanged.connect(self._on_select_file)
        self._file_list.itemSelectionChanged.connect(self._update_trash_btn)

        # Initial load
        if workspace_root:
            self.refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_workspace(self, workspace_root: str) -> None:
        """Update workspace and reload the source list."""
        self._workspace_root = workspace_root
        self._inputmd_root = os.path.join(workspace_root, "inputMD")
        self.refresh()

    def refresh(self) -> None:
        """Reload the source list from disk."""
        self._sub_list.clear()
        self._file_list.clear()
        self._preview.clear()
        self._src_list.clear()
        self._sub_level = 1
        self._sub_parent_path = None
        self._sub_current_path = None

        if not self._inputmd_root or not os.path.isdir(self._inputmd_root):
            self._info.setText("inputMD: (フォルダなし — 実行後に生成されます)")
            return

        sources = sorted(
            d for d in os.listdir(self._inputmd_root)
            if os.path.isdir(os.path.join(self._inputmd_root, d))
        )
        if not sources:
            self._info.setText("inputMD: (空)")
            return

        # Count per source (fast: just count immediate children depth)
        total = 0
        for name in sources:
            src_path = os.path.join(self._inputmd_root, name)
            cnt = self._count_md_files_shallow(src_path)
            total += cnt
            label = f"{name}  ({cnt})" if cnt else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, src_path)
            self._src_list.addItem(item)

        self._info.setText(
            f"inputMD: {os.path.basename(self._workspace_root or '')} | "
            f"ソース {len(sources)} 件 | 合計 {total} ファイル"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_md_files_shallow(root: str) -> int:
        """Count .md files under root (full walk)."""
        n = 0
        for dirpath, _, files in os.walk(root):
            n += sum(1 for f in files if f.endswith(".md"))
        return n

    @staticmethod
    def _count_md_immediate(folder: str) -> int:
        """Count .md files in a single directory (non-recursive)."""
        try:
            return sum(1 for f in os.listdir(folder) if f.endswith(".md"))
        except OSError:
            return 0

    @staticmethod
    def _count_md_recursive_fast(folder: str) -> int:
        """Count .md files recursively (for sub-folder counts)."""
        n = 0
        for dirpath, _, files in os.walk(folder):
            n += sum(1 for f in files if f.endswith(".md"))
        return n

    def _list_subdirs(self, path: str) -> List[str]:
        if not os.path.isdir(path):
            return []
        return sorted(
            d for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d))
        )

    def _list_md_files_in_folder(self, path: str) -> List[Tuple[str, str]]:
        """Return list of (filename, abs_path) for .md files in a single dir (non-recursive)."""
        results: List[Tuple[str, str]] = []
        if not os.path.isdir(path):
            return results
        for fn in sorted(os.listdir(path)):
            if fn.endswith(".md"):
                abs_path = os.path.join(path, fn)
                if os.path.isfile(abs_path):
                    results.append((fn, abs_path))
        return results

    def _list_md_files_recursive(self, path: str) -> List[Tuple[str, str]]:
        """Return list of (display_label, abs_path) for all .md files under path."""
        results: List[Tuple[str, str]] = []
        for dirpath, _, files in os.walk(path):
            for fn in sorted(files):
                if fn.endswith(".md"):
                    abs_path = os.path.join(dirpath, fn)
                    rel = os.path.relpath(abs_path, path)
                    results.append((rel, abs_path))
        return results

    def _on_link(self, url: QUrl):
        try:
            QDesktopServices.openUrl(url)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Selection handlers
    # ------------------------------------------------------------------

    def _on_select_source(self):
        self._sub_list.clear()
        self._file_list.clear()
        self._preview.clear()
        self._sub_level = 1
        self._sub_current_path = None

        items = self._src_list.selectedItems()
        if not items:
            return
        src_path = items[0].data(Qt.ItemDataRole.UserRole)
        self._sub_parent_path = src_path

        subdirs = self._list_subdirs(src_path)
        if not subdirs:
            # No sub-folders: show MD files directly under source
            self._populate_files_from_folder(src_path)
            return

        # Show run_tags with counts
        for name in subdirs:
            sub_path = os.path.join(src_path, name)
            cnt = self._count_md_recursive_fast(sub_path)
            label = f"{name}  ({cnt})" if cnt else name
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, sub_path)
            self._sub_list.addItem(it)
        if self._sub_list.count():
            self._sub_list.setCurrentRow(0)

    def _on_select_sub(self):
        self._file_list.clear()
        self._preview.clear()
        items = self._sub_list.selectedItems()
        if not items:
            return

        path = items[0].data(Qt.ItemDataRole.UserRole)

        # Handle "← 戻る" item
        if path == _BACK_SENTINEL:
            self._drill_up()
            return

        if self._sub_level == 1:
            # Level 1: user clicked a run_tag
            # Check if it has date subdirectories
            child_dirs = self._list_subdirs(path)
            if child_dirs:
                # Drill into this run_tag to show date folders
                self._drill_into(path, child_dirs)
            else:
                # No child dirs: show files directly (non-recursive)
                self._populate_files_from_folder(path)
        else:
            # Level 2: user clicked a date folder → show files
            self._populate_files_from_folder(path)

    def _drill_into(self, run_tag_path: str, child_dirs: List[str]):
        """Drill into a run_tag folder to show its date subfolders."""
        self._sub_current_path = run_tag_path
        self._sub_level = 2
        self._sub_list.blockSignals(True)
        self._sub_list.clear()

        # "← 戻る" item
        back_item = QListWidgetItem("← 戻る")
        back_item.setData(Qt.ItemDataRole.UserRole, _BACK_SENTINEL)
        self._sub_list.addItem(back_item)

        # Date folders with counts
        for name in child_dirs:
            date_path = os.path.join(run_tag_path, name)
            cnt = self._count_md_recursive_fast(date_path)
            label = f"{name}  ({cnt})" if cnt else name
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, date_path)
            self._sub_list.addItem(it)

        # Also show .md files directly under run_tag (e.g. index files)
        direct_md = self._count_md_immediate(run_tag_path)
        if direct_md:
            it = QListWidgetItem(f"(直下ファイル)  ({direct_md})")
            it.setData(Qt.ItemDataRole.UserRole, run_tag_path)
            self._sub_list.addItem(it)

        self._sub_list.blockSignals(False)
        # Auto-select first date (skip "← 戻る")
        if self._sub_list.count() > 1:
            self._sub_list.setCurrentRow(1)

    def _drill_up(self):
        """Go back from date folders to run_tag list."""
        self._sub_level = 1
        self._sub_current_path = None
        self._file_list.clear()
        self._preview.clear()
        # Re-trigger source selection to rebuild run_tag list
        self._on_select_source()

    def _populate_files_from_folder(self, folder: str):
        """Populate the file list from a single folder (non-recursive for leaf dirs)."""
        self._file_list.clear()
        self._preview.clear()

        # Check if the folder has child directories
        child_dirs = self._list_subdirs(folder)
        if child_dirs:
            # Has child dirs: list files recursively but this should be a
            # small set (single date folder typically has no children)
            entries = self._list_md_files_recursive(folder)
        else:
            # Leaf folder: list .md files directly (fast)
            entries = self._list_md_files_in_folder(folder)

        for label, abs_path in entries:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, abs_path)
            self._file_list.addItem(it)
        if self._file_list.count():
            self._file_list.setCurrentRow(0)

    def _on_select_file(self):
        items = self._file_list.selectedItems()
        if not items:
            self._preview.clear()
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if not path or not os.path.exists(path):
            self._preview.setPlainText("(ファイルが見つかりません)")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            html = mdlib.markdown(text, extensions=["tables", "fenced_code"])
            self._preview.setHtml(html)
        except Exception as e:
            self._preview.setPlainText(f"読み込みエラー: {e}")

    # ------------------------------------------------------------------
    # Search (scoped to current selection)
    # ------------------------------------------------------------------

    def _get_search_root(self) -> Optional[str]:
        """Determine the search scope based on current pane selections."""
        # If a sub-folder item is selected (and it's not "← 戻る")
        sub_items = self._sub_list.selectedItems()
        if sub_items:
            path = sub_items[0].data(Qt.ItemDataRole.UserRole)
            if path and path != _BACK_SENTINEL:
                return path

        # If a source is selected
        src_items = self._src_list.selectedItems()
        if src_items:
            return src_items[0].data(Qt.ItemDataRole.UserRole)

        # Fallback: entire inputMD
        return self._inputmd_root

    def _do_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query:
            return

        search_root = self._get_search_root()
        if not search_root or not os.path.isdir(search_root):
            return

        self._search_mode = True
        self._btn_clear_search.setEnabled(True)
        self._file_list.clear()
        self._preview.clear()

        query_lower = query.lower()
        matches: list[Tuple[str, str]] = []  # (label, abs_path)
        for dirpath, _, files in os.walk(search_root):
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                abs_path = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_path, search_root)
                # Match filename
                if query_lower in fn.lower():
                    matches.append((rel, abs_path))
                    continue
                # Match file content
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if query_lower in content.lower():
                        matches.append((rel, abs_path))
                except Exception:
                    pass

        scope_name = os.path.basename(search_root)
        self._search_result_label.setText(f"{len(matches)} 件ヒット (範囲: {scope_name})")
        for label, abs_path in matches:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, abs_path)
            self._file_list.addItem(it)
        if self._file_list.count():
            self._file_list.setCurrentRow(0)

    def _clear_search(self) -> None:
        self._search_mode = False
        self._search_edit.clear()
        self._search_result_label.setText("")
        self._btn_clear_search.setEnabled(False)
        self._file_list.clear()
        self._preview.clear()
        # Restore view from current sub-folder selection
        sub_items = self._sub_list.selectedItems()
        if sub_items:
            path = sub_items[0].data(Qt.ItemDataRole.UserRole)
            if path and path != _BACK_SENTINEL:
                self._populate_files_from_folder(path)

    # ------------------------------------------------------------------
    # Trash / cleanup
    # ------------------------------------------------------------------

    def _update_trash_btn(self):
        self._btn_trash.setEnabled(bool(self._file_list.selectedItems()))

    def _trash_selected(self):
        if not self._workspace_root:
            return
        sel = self._file_list.selectedItems()
        if not sel:
            return
        paths = [it.data(Qt.ItemDataRole.UserRole) for it in sel if it.data(Qt.ItemDataRole.UserRole)]
        paths = [p for p in paths if p and os.path.exists(p)]
        if not paths:
            return

        # Safety check
        for p in paths:
            if not is_within(self._workspace_root, p):
                QMessageBox.critical(self, "安全確認", f"ワークスペース外のパスは削除できません:\n{p}")
                return

        msg = f"{len(paths)} 件のファイルをゴミ箱へ移動しますか？"
        if QMessageBox.question(self, "確認", msg) != QMessageBox.StandardButton.Yes:
            return

        trashed = trash_paths(paths)
        QMessageBox.information(self, "完了", f"ゴミ箱へ移動: {len(trashed)} 件")

        # Refresh current view
        sub_items = self._sub_list.selectedItems()
        if sub_items:
            path = sub_items[0].data(Qt.ItemDataRole.UserRole)
            if path and path != _BACK_SENTINEL:
                self._populate_files_from_folder(path)
        else:
            src_items = self._src_list.selectedItems()
            if src_items:
                self._populate_files_from_folder(src_items[0].data(Qt.ItemDataRole.UserRole))
        self._update_trash_btn()

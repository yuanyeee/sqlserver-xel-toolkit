"""inputmd_browser.py

Browse and manage the inputMD archive (per-event Markdown cache).

Structure (workspace_root/inputMD/):
  {stem_hash}/           ← Source  (e.g. "blocking_12a3b4c5")
    {run_tag}/           ← Run tag (e.g. "20260201_2105")
      {YYYYMMDD}/        ← Date folder
        *.md             ← Individual event Markdown files
    index_*.md           ← Index files (at source level)

The browser provides:
  Source list | Sub-folder list | File list | MD Preview
plus in-place cleanup (move selected to trash).
"""

from __future__ import annotations

import os
from typing import List, Optional

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
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .delete_utils import is_within, trash_paths


class InputMdBrowserWidget(QWidget):
    """4-pane browser for workspace_root/inputMD/.

    Pane 1 – Source        : {stem_hash} directories
    Pane 2 – Sub-folder    : run_tag / YYYYMMDD directories under selected source
    Pane 3 – File          : .md files under selected sub-folder (recursive)
    Pane 4 – Preview       : rendered Markdown of selected file
    """

    def __init__(self, workspace_root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._workspace_root: Optional[str] = workspace_root
        self._inputmd_root: Optional[str] = (
            os.path.join(workspace_root, "inputMD") if workspace_root else None
        )

        self._search_mode = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # ---- Info label ----
        self._info = QLabel("inputMD: (ワークスペース未設定)")
        layout.addWidget(self._info)

        # ---- Search bar ----
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("キーワード（ファイル名・本文を検索）")
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
        layout.addWidget(splitter)

        # ---- Header labels ----
        # (placed above each list using a secondary horizontal layout is complex;
        #  use list headers instead)
        self._src_list.addItem("── Source ──")
        self._src_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        self._src_list.clear()

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

        total = self._count_md_files(self._inputmd_root)
        self._info.setText(
            f"inputMD: {os.path.basename(self._workspace_root or '')} | "
            f"ソース {len(sources)} 件 | 合計 {total} ファイル"
        )

        for name in sources:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, os.path.join(self._inputmd_root, name))
            self._src_list.addItem(item)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_md_files(root: str) -> int:
        n = 0
        for dirpath, _, files in os.walk(root):
            n += sum(1 for f in files if f.endswith(".md"))
        return n

    def _list_subdirs(self, path: str) -> List[str]:
        if not os.path.isdir(path):
            return []
        return sorted(
            d for d in os.listdir(path)
            if os.path.isdir(os.path.join(path, d))
        )

    def _list_md_files_recursive(self, path: str) -> List[tuple[str, str]]:
        """Return list of (display_label, abs_path) for all .md files under path."""
        results: List[tuple[str, str]] = []
        for dirpath, _, files in os.walk(path):
            for fn in sorted(files):
                if fn.endswith(".md"):
                    abs_path = os.path.join(dirpath, fn)
                    # relative label: e.g. "20260201/event_deadlock_1.md"
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
        items = self._src_list.selectedItems()
        if not items:
            return
        src_path = items[0].data(Qt.ItemDataRole.UserRole)
        subdirs = self._list_subdirs(src_path)
        if not subdirs:
            # No sub-folders: show MD files directly under source
            self._populate_files(src_path)
            return
        for name in subdirs:
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, os.path.join(src_path, name))
            self._sub_list.addItem(it)
        # Auto-select first sub-folder
        if self._sub_list.count():
            self._sub_list.setCurrentRow(0)

    def _on_select_sub(self):
        self._file_list.clear()
        self._preview.clear()
        items = self._sub_list.selectedItems()
        if not items:
            return
        self._populate_files(items[0].data(Qt.ItemDataRole.UserRole))

    def _populate_files(self, folder: str):
        self._file_list.clear()
        self._preview.clear()
        entries = self._list_md_files_recursive(folder)
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
    # Search
    # ------------------------------------------------------------------

    def _do_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query or not self._inputmd_root or not os.path.isdir(self._inputmd_root):
            return
        self._search_mode = True
        self._btn_clear_search.setEnabled(True)
        self._src_list.clearSelection()
        self._sub_list.clear()
        self._file_list.clear()
        self._preview.clear()

        query_lower = query.lower()
        matches: list[tuple[str, str]] = []  # (label, abs_path)
        for dirpath, _, files in os.walk(self._inputmd_root):
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                abs_path = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_path, self._inputmd_root)
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

        self._search_result_label.setText(f"{len(matches)} 件ヒット")
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
        # Restore source selection if any
        sel = self._src_list.selectedItems()
        if sel:
            self._on_select_source()

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

        # Refresh current sub-folder view
        sub_items = self._sub_list.selectedItems()
        if sub_items:
            self._populate_files(sub_items[0].data(Qt.ItemDataRole.UserRole))
        else:
            src_items = self._src_list.selectedItems()
            if src_items:
                self._populate_files(src_items[0].data(Qt.ItemDataRole.UserRole))
        self._update_trash_btn()

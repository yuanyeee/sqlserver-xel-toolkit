"""inputmd_browser.py

Browse and manage the inputMD archive (per-event Markdown cache).

Structure (workspace_root/inputMD/):
  {EventType}/           <- Source  (e.g. "Blocking", "SlowQuery", "DeadLock")
    {YYYYMMDD}/          <- Date folder (JST date)
      *.md               <- Individual event Markdown files

The browser provides:
  Source list | Sub-folder (date) | File list | MD Preview
plus in-place cleanup (move selected to trash).

Search / filter behaviour:
  - Typing in the search box filters the file list in real-time (filename match).
  - Pressing Enter (or the search button) launches a background content search
    via QThread, adding content-matched files incrementally.
  - The filter text persists across folder navigation.
  - Clearing the search box restores the full file list.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple

import markdown as mdlib

from PySide6.QtCore import Qt, QTimer, QUrl
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

from PySide6.QtCore import QThread, Signal as QSignal

from .delete_utils import is_within, trash_paths

# Sentinel value stored in UserRole to indicate "go back" item
_BACK_SENTINEL = "__BACK__"

# Help text shown in the search help dialog
_SEARCH_HELP_TEXT = (
    "<h3>検索機能の使い方</h3>"
    "<p><b>キーワード検索:</b> ファイル名と内容を検索します。</p>"
    "<ul>"
    "<li><code>keyword</code> : 部分一致検索</li>"
    "<li><code>\"exact phrase\"</code> : 完全一致検索</li>"
    "<li><code>term1 term2</code> : AND検索 (両方を含む)</li>"
    "<li><code>term1 OR term2</code> : OR検索 (いずれかを含む)</li>"
    "<li><code>-term</code> : 除外検索 (含まない)</li>"
    "</ul>"
)


def _parse_query(raw: str) -> dict:
    """Parse a search query string into a structured dict.

    Returns
    -------
    dict with keys:
        'must'     : list[str]  – all tokens that MUST appear (AND)
        'exclude'  : list[str]  – tokens that must NOT appear
        'or_groups': list[list[str]] – groups of OR-ed tokens

    Examples:
        'foo bar'          -> must=['foo','bar']
        'foo OR bar'       -> or_groups=[['foo','bar']]
        '-secret foo'      -> must=['foo'], exclude=['secret']
        '"exact phrase"'   -> must=['exact phrase']
    """
    must: list[str] = []
    exclude: list[str] = []
    or_groups: list[list[str]] = []

    try:
        tokens = shlex.split(raw)
    except ValueError:
        # Unmatched quotes – fall back to simple split
        tokens = raw.split()

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # Exclusion
        if tok.startswith("-") and len(tok) > 1:
            exclude.append(tok[1:].lower())
            i += 1
            continue

        # OR operator: collect "A OR B OR C"
        if (i + 2 < len(tokens)
                and tokens[i + 1].upper() == "OR"):
            group = [tok.lower()]
            while i + 2 < len(tokens) and tokens[i + 1].upper() == "OR":
                group.append(tokens[i + 2].lower())
                i += 2
            or_groups.append(group)
            i += 1
            continue

        # Skip bare "OR" if it appears alone
        if tok.upper() == "OR":
            i += 1
            continue

        must.append(tok.lower())
        i += 1

    return {"must": must, "exclude": exclude, "or_groups": or_groups}


def _match_query(text_lower: str, parsed: dict) -> bool:
    """Return True if *text_lower* satisfies the parsed query."""
    # Exclusions
    for ex in parsed["exclude"]:
        if ex in text_lower:
            return False
    # Must-have tokens (AND)
    for m in parsed["must"]:
        if m not in text_lower:
            return False
    # OR groups: each group must have at least one match
    for group in parsed["or_groups"]:
        if not any(t in text_lower for t in group):
            return False
    return True


# ======================================================================
# Background content-search worker
# ======================================================================

class _SearchWorker(QThread):
    """Search .md file contents in a background thread.

    Emits *hit* for every matching file found, and *finished_search*
    when the walk is complete (or cancelled).
    """

    hit = QSignal(str, str)          # (display_label, abs_path)
    finished_search = QSignal(int)   # total_hits

    def __init__(self, search_root: str, query: str, parsed: dict, parent=None):
        super().__init__(parent)
        self._search_root = search_root
        self._query = query.lower()
        self._parsed = parsed
        self._cancelled = False

    # -- public ----------------------------------------------------------

    def cancel(self):
        self._cancelled = True

    # -- thread entry ----------------------------------------------------

    def run(self):  # noqa: D401 (QThread override)
        total = 0
        for dirpath, _, files in os.walk(self._search_root):
            if self._cancelled:
                break
            for fn in sorted(files):
                if self._cancelled:
                    break
                if not fn.endswith(".md"):
                    continue
                abs_path = os.path.join(dirpath, fn)
                # Skip filename-only matches (already shown by instant filter)
                if _match_query(fn.lower(), self._parsed):
                    continue
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if _match_query(content.lower(), self._parsed):
                        rel = os.path.relpath(abs_path, self._search_root)
                        self.hit.emit(rel, abs_path)
                        total += 1
                except Exception:
                    pass
        self.finished_search.emit(total)


# ======================================================================
# Main widget
# ======================================================================

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

        # Track drill-down state in sub-folder pane
        self._sub_parent_path: Optional[str] = None  # source path (level 1)
        self._sub_current_path: Optional[str] = None  # run_tag path when drilled in (level 2)
        self._sub_level = 1  # 1 = run_tags, 2 = date folders

        # Full (unfiltered) file list kept in memory for instant filter
        self._all_files: List[Tuple[str, str]] = []   # (label, abs_path)
        self._current_folder: Optional[str] = None     # folder that _all_files came from

        # Background search state
        self._search_worker: Optional[_SearchWorker] = None
        self._content_hit_paths: set[str] = set()  # tracks content-match paths already added

        # Debounce timer for real-time filter
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)  # ms
        self._filter_timer.timeout.connect(self._apply_filter)

        # ---- Build UI --------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Info label
        self._info = QLabel("inputMD: (ワークスペース未設定)")
        self._info.setSizePolicy(self._info.sizePolicy().horizontalPolicy(),
                                 QSizePolicy.Policy.Fixed)
        layout.addWidget(self._info)

        # Search bar
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("キーワード（ファイル名: 即時 / 内容: Enter）")
        self._search_edit.textChanged.connect(self._on_filter_text_changed)
        self._search_edit.returnPressed.connect(self._do_content_search)
        search_row.addWidget(self._search_edit)
        self._btn_search = QPushButton("内容検索")
        self._btn_search.clicked.connect(self._do_content_search)
        search_row.addWidget(self._btn_search)
        self._btn_clear_search = QPushButton("クリア")
        self._btn_clear_search.clicked.connect(self._clear_search)
        self._btn_clear_search.setEnabled(False)
        search_row.addWidget(self._btn_clear_search)
        self._btn_help = QPushButton("ℹ")
        self._btn_help.setFixedWidth(28)
        self._btn_help.setToolTip("検索ヘルプ")
        self._btn_help.clicked.connect(self._show_search_help)
        search_row.addWidget(self._btn_help)
        self._filter_status = QLabel("")
        search_row.addWidget(self._filter_status)
        layout.addLayout(search_row)

        # 4-pane splitter
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

        # Bottom toolbar
        bottom = QHBoxLayout()
        self._btn_refresh = QPushButton("再読み込み")
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_trash = QPushButton("選択をゴミ箱へ移動")
        self._btn_trash.clicked.connect(self._trash_selected)
        self._btn_trash.setEnabled(False)
        self._count_label = QLabel("")
        bottom.addWidget(self._btn_refresh)
        bottom.addStretch()
        bottom.addWidget(self._count_label)
        bottom.addWidget(self._btn_trash)
        layout.addLayout(bottom)

        # Signals
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
        self._cancel_search()
        self._sub_list.clear()
        self._file_list.clear()
        self._preview.clear()
        self._src_list.clear()
        self._sub_level = 1
        self._sub_parent_path = None
        self._sub_current_path = None
        self._all_files.clear()
        self._current_folder = None
        self._update_count_label()

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

        # Count per source
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

    def _update_count_label(self):
        """Update the bottom count label with current file counts."""
        total = len(self._all_files)
        visible = self._file_list.count()
        if total == 0:
            self._count_label.setText("")
        elif visible == total:
            self._count_label.setText(f"対象: {total} 件")
        else:
            self._count_label.setText(f"対象: {visible} / {total} 件")

    # ------------------------------------------------------------------
    # Selection handlers
    # ------------------------------------------------------------------

    def _on_select_source(self):
        self._cancel_search()
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
        self._cancel_search()
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
            child_dirs = self._list_subdirs(path)
            if child_dirs:
                self._drill_into(path, child_dirs)
            else:
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
        """Populate the file list from a single folder.

        Stores the full list in ``_all_files`` for instant filtering, then
        applies the current filter text (if any).
        """
        self._file_list.clear()
        self._preview.clear()
        self._current_folder = folder

        # Build full list
        child_dirs = self._list_subdirs(folder)
        if child_dirs:
            self._all_files = self._list_md_files_recursive(folder)
        else:
            self._all_files = self._list_md_files_in_folder(folder)

        # Apply filter (shows everything if filter is empty)
        self._apply_filter()

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
    # Real-time filter (filename)
    # ------------------------------------------------------------------

    def _on_filter_text_changed(self, text: str):
        """Called on every keystroke; debounce before applying."""
        self._cancel_search()  # cancel any running content search
        self._filter_timer.start()  # restart debounce timer
        has_text = bool(text.strip())
        self._btn_clear_search.setEnabled(has_text)
        if not has_text:
            self._filter_status.setText("")

    def _apply_filter(self):
        """Filter _all_files by filename and update file list widget.

        This is purely in-memory so it's instant even for thousands of files.
        Supports AND, OR, "exact phrase", and -exclude operators.
        """
        raw = self._search_edit.text().strip()
        self._file_list.clear()
        self._preview.clear()
        self._content_hit_paths.clear()

        if not raw:
            # No filter: show everything
            for label, abs_path in self._all_files:
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, abs_path)
                self._file_list.addItem(it)
        else:
            parsed = _parse_query(raw)
            # Filename filter
            for label, abs_path in self._all_files:
                fn = os.path.basename(abs_path)
                if _match_query(fn.lower(), parsed):
                    it = QListWidgetItem(label)
                    it.setData(Qt.ItemDataRole.UserRole, abs_path)
                    self._file_list.addItem(it)

            shown = self._file_list.count()
            self._filter_status.setText(
                f"{shown} 件 (ファイル名一致) — Enter で内容検索"
                if shown < len(self._all_files)
                else f"{shown} 件"
            )

        if self._file_list.count():
            self._file_list.setCurrentRow(0)
        self._update_count_label()

    # ------------------------------------------------------------------
    # Background content search
    # ------------------------------------------------------------------

    def _cancel_search(self):
        """Cancel any running background search."""
        if self._search_worker is not None:
            self._search_worker.cancel()
            self._search_worker.hit.disconnect(self._on_search_hit)
            self._search_worker.finished_search.disconnect(self._on_search_done)
            self._search_worker.quit()
            self._search_worker.wait(500)
            self._search_worker = None

    def _do_content_search(self):
        """Start background content search (triggered by Enter or button)."""
        query = self._search_edit.text().strip()
        if not query:
            return

        search_root = self._get_search_root()
        if not search_root or not os.path.isdir(search_root):
            return

        # Cancel previous
        self._cancel_search()

        parsed = _parse_query(query)

        # Record which paths are already shown (filename matches)
        already_shown = set()
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            p = item.data(Qt.ItemDataRole.UserRole)
            if p:
                already_shown.add(p)
        self._content_hit_paths = already_shown.copy()

        self._filter_status.setText("検索中...")
        self._btn_search.setEnabled(False)

        self._search_worker = _SearchWorker(search_root, query, parsed, parent=self)
        self._search_worker.hit.connect(self._on_search_hit)
        self._search_worker.finished_search.connect(self._on_search_done)
        self._search_worker.start()

    def _on_search_hit(self, label: str, abs_path: str):
        """Receive a single content-match hit from the worker thread."""
        if abs_path in self._content_hit_paths:
            return  # already shown
        self._content_hit_paths.add(abs_path)
        it = QListWidgetItem(f"📄 {label}")
        it.setData(Qt.ItemDataRole.UserRole, abs_path)
        self._file_list.addItem(it)

        # Update status
        total_shown = self._file_list.count()
        self._filter_status.setText(f"検索中... {total_shown} 件ヒット")
        self._update_count_label()

    def _on_search_done(self, content_hits: int):
        """Background search finished."""
        total_shown = self._file_list.count()
        scope_name = ""
        search_root = self._get_search_root()
        if search_root:
            scope_name = os.path.basename(search_root)
        self._filter_status.setText(
            f"{total_shown} 件ヒット (範囲: {scope_name})"
        )
        self._btn_search.setEnabled(True)
        self._search_worker = None
        self._update_count_label()
        if self._file_list.count() and not self._file_list.selectedItems():
            self._file_list.setCurrentRow(0)

    def _get_search_root(self) -> Optional[str]:
        """Determine the search scope based on current pane selections."""
        sub_items = self._sub_list.selectedItems()
        if sub_items:
            path = sub_items[0].data(Qt.ItemDataRole.UserRole)
            if path and path != _BACK_SENTINEL:
                return path

        src_items = self._src_list.selectedItems()
        if src_items:
            return src_items[0].data(Qt.ItemDataRole.UserRole)

        return self._inputmd_root

    def _clear_search(self) -> None:
        """Clear filter text and restore full file list."""
        self._cancel_search()
        self._search_edit.clear()
        self._filter_status.setText("")
        self._btn_clear_search.setEnabled(False)
        # _apply_filter will be triggered by textChanged → debounce → _apply_filter
        # but we can also call it directly for instant response
        self._apply_filter()

    def _show_search_help(self):
        """Show the search syntax help dialog."""
        msg = QMessageBox(self)
        msg.setWindowTitle("検索ヘルプ")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(_SEARCH_HELP_TEXT)
        msg.exec()

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

        # Remove trashed files from _all_files cache
        trashed_set = set(trashed)
        self._all_files = [
            (lbl, p) for lbl, p in self._all_files if p not in trashed_set
        ]

        # Re-apply filter to refresh the view
        self._apply_filter()
        self._update_trash_btn()

from __future__ import annotations

import os
import re
import sys
import subprocess
import hashlib
import shutil
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from ._qt import (
    Qt,
    QThread,
    Signal,
    QApplication,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QScrollArea,
    QTabWidget,
    QTextEdit,
)

import markdown as mdlib
import json
import glob

from .workspace import (
    RunRow,
    add_file,
    add_input,
    add_report,
    add_run,
    replace_run_by_out_dir,
    connect_db,
    get_report,
    init_db,
    list_files,
    list_reports,
    list_runs,
    search_reports,
)

from .time_range_dialog import TimeRangeDialog, TimeRangeWidget, load_ranges
from .aggregate_dialog import AggregateWidget
from .inputmd_browser import InputMdBrowserWidget
from .cleanup_inputmd_dialog import CleanupInputMdDialog
from .delete_run_dialog import DeleteRunDialog
from .delete_file_dialog import DeleteFileDialog
from .delete_report_dialog import DeleteReportDialog
from .workspace import delete_run_db, delete_file_db, delete_report_db, delete_reports_by_file_db
from .delete_utils import trash_paths
from .workspace_select_dialog import WorkspaceSelectDialog


@dataclass
class WorkspaceState:
    root: str
    db_path: str


def _safe_stem(path: str) -> str:
    s = Path(path).name
    if "." in s:
        s = s.rsplit(".", 1)[0]
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or "input"


def _hash8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:8]


def inputmd_dir(workspace_root: str, input_path: str) -> str:
    """Return the inputMD root directory (shared across all inputs)."""
    return str(Path(workspace_root) / "inputMD")


def stable_run_dir(workspace_root: str, input_path: str) -> str:
    key = _hash8(str(Path(input_path).resolve()))
    safe = _safe_stem(input_path)
    return str(Path(workspace_root) / "runs" / f"{safe}_{key}")


def clear_dir(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for child in p.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception:
            pass


class RunWorker(QThread):
    log = Signal(str)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, repo_root: str, xel_paths: List[str], out_dir: str, slow_threshold: float, workspace_root: Optional[str], ranges_path: Optional[str]):
        super().__init__()
        self.repo_root = repo_root
        self.xel_paths = xel_paths
        self.out_dir = out_dir
        self.slow_threshold = slow_threshold
        self.workspace_root = workspace_root
        self.ranges_path = ranges_path

    def run(self):
        try:
            env = os.environ.copy()
            if self.workspace_root:
                env["XEL_TOOLKIT_WORKSPACE"] = self.workspace_root
            if self.ranges_path and os.path.exists(self.ranges_path):
                env["XEL_TOOLKIT_RANGES_JSON"] = self.ranges_path

            if os.environ.get("XEL_TOOLKIT_INPUTMD_MODE"):
                env["XEL_TOOLKIT_INPUTMD_MODE"] = os.environ.get("XEL_TOOLKIT_INPUTMD_MODE")

            if os.name == "nt":
                entry = os.path.join(self.repo_root, "run.ps1")
                cmd = [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    entry,
                    *self.xel_paths,
                    "-o",
                    self.out_dir,
                    "-SlowThresholdSec",
                    str(self.slow_threshold),
                ]
            else:
                entry = os.path.join(self.repo_root, "run.sh")
                cmd = [entry, *self.xel_paths, "-o", self.out_dir, "--slow-threshold", str(self.slow_threshold)]

            self.log.emit("$ " + " ".join(cmd))
            p = subprocess.Popen(
                cmd,
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert p.stdout
            for line in p.stdout:
                self.log.emit(line.rstrip("\n"))
            rc = p.wait()
            if rc == 0:
                self.finished_ok.emit(self.out_dir)
            else:
                self.finished_err.emit(f"runner failed with code {rc}")
        except Exception as e:
            self.finished_err.emit(str(e))


class ReportRegenWorker(QThread):
    log = Signal(str)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, repo_root: str, *, out_dir: str, file_out_dir: str, source_xel: str, prefix: str, ranges_path: Optional[str]):
        super().__init__()
        self.repo_root = repo_root
        self.out_dir = out_dir
        self.file_out_dir = file_out_dir
        self.source_xel = source_xel
        self.prefix = prefix
        self.ranges_path = ranges_path

    def run(self):
        try:
            tmpdir = os.path.join(self.out_dir, "tmp")
            deadlock = os.path.join(tmpdir, f"{self.prefix}_deadlock.jsonl")
            blocking = os.path.join(tmpdir, f"{self.prefix}_blocking.jsonl")
            slow = os.path.join(tmpdir, f"{self.prefix}_slow.jsonl")

            args = [sys.executable, os.path.join(self.repo_root, "py", "generate_reports.py"), "--out", self.file_out_dir, "--source-xel", self.source_xel, "--prefix", self.prefix]
            if os.path.exists(deadlock):
                args += ["--deadlock-jsonl", deadlock]
            if os.path.exists(blocking):
                args += ["--blocking-jsonl", blocking]
            if os.path.exists(slow):
                args += ["--slowquery-jsonl", slow]

            env = os.environ.copy()
            if self.ranges_path and os.path.exists(self.ranges_path):
                env["XEL_TOOLKIT_RANGES_JSON"] = self.ranges_path

            self.log.emit("$ " + " ".join(args))
            p = subprocess.Popen(
                args,
                cwd=self.repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert p.stdout
            for line in p.stdout:
                self.log.emit(line.rstrip("\n"))
            rc = p.wait()
            if rc == 0:
                self.finished_ok.emit(self.file_out_dir)
            else:
                self.finished_err.emit(f"generate_reports failed with code {rc}")
        except Exception as e:
            self.finished_err.emit(str(e))


# ---------------------------------------------------------------------------
# Tab widgets
# ---------------------------------------------------------------------------

class ReportBrowserTab(QWidget):
    """Tab 0: 4-pane report browser."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Search
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("全文検索 (FTS5)...")
        layout.addWidget(self.search_box)

        # 4-pane splitter
        splitter = QSplitter(Qt.Horizontal)
        self.run_list = QListWidget()
        self.run_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.report_list = QListWidget()
        self.report_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setOpenLinks(False)

        splitter.addWidget(self.run_list)
        splitter.addWidget(self.file_list)
        splitter.addWidget(self.report_list)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setStretchFactor(3, 6)
        layout.addWidget(splitter)

        # Bottom action row
        act_row = QHBoxLayout()
        self.btn_regen = QPushButton("レポート再生成")
        self.btn_del_run = QPushButton("Run削除")
        self.btn_del_file = QPushButton("File削除")
        self.btn_del_report = QPushButton("Report削除")
        act_row.addWidget(self.btn_regen)
        act_row.addStretch()
        act_row.addWidget(self.btn_del_run)
        act_row.addWidget(self.btn_del_file)
        act_row.addWidget(self.btn_del_report)
        layout.addLayout(act_row)


class RunTab(QWidget):
    """Tab 1: New run execution."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # File selection
        file_group = QGroupBox("入力ファイル")
        fg_layout = QVBoxLayout(file_group)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setMaximumHeight(120)
        fg_layout.addWidget(self.file_list)
        file_btns = QHBoxLayout()
        self.btn_add_files = QPushButton("ファイルを追加… (.xel/.csv/.xlsx)")
        self.btn_clear_files = QPushButton("クリア")
        file_btns.addWidget(self.btn_add_files)
        file_btns.addWidget(self.btn_clear_files)
        file_btns.addStretch()
        fg_layout.addLayout(file_btns)
        layout.addWidget(file_group)

        # Options
        opt_group = QGroupBox("実行オプション")
        form = QFormLayout(opt_group)
        self.slow_thr_edit = QLineEdit("3.0")
        self.slow_thr_edit.setMaximumWidth(80)
        form.addRow("SlowQuery閾値(秒):", self.slow_thr_edit)

        inputmd_row = QHBoxLayout()
        self.radio_overwrite = QCheckBox("上書き")
        self.radio_overwrite.setChecked(True)
        self.radio_append = QCheckBox("追記")
        inputmd_row.addWidget(self.radio_overwrite)
        inputmd_row.addWidget(self.radio_append)
        inputmd_row.addStretch()
        self.radio_overwrite.stateChanged.connect(lambda s: self.radio_append.setChecked(not bool(s)))
        self.radio_append.stateChanged.connect(lambda s: self.radio_overwrite.setChecked(not bool(s)))
        form.addRow("inputMD モード:", inputmd_row)

        layout.addWidget(opt_group)

        # Run button
        self.btn_run = QPushButton("▶  XEL 解析・レポート生成")
        self.btn_run.setMinimumHeight(36)
        layout.addWidget(self.btn_run)

        # Log
        layout.addWidget(QLabel("実行ログ:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

    def append_log(self, line: str):
        self.log_view.append(line)

    def clear_log(self):
        self.log_view.clear()

    def selected_files(self) -> List[str]:
        return [self.file_list.item(i).text() for i in range(self.file_list.count())]


class SettingsTab(QWidget):
    """Tab 4: Workspace + maintenance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Workspace
        ws_group = QGroupBox("ワークスペース")
        ws_layout = QVBoxLayout(ws_group)
        self.ws_label = QLabel("(未設定)")
        self.ws_label.setWordWrap(True)
        ws_layout.addWidget(self.ws_label)
        ws_btns = QHBoxLayout()
        self.btn_open_ws = QPushButton("フォルダを開く…")
        self.btn_change_ws = QPushButton("切り替え…")
        ws_btns.addWidget(self.btn_open_ws)
        ws_btns.addWidget(self.btn_change_ws)
        ws_btns.addStretch()
        ws_layout.addLayout(ws_btns)
        layout.addWidget(ws_group)

        # Maintenance
        maint_group = QGroupBox("メンテナンス")
        maint_layout = QVBoxLayout(maint_group)
        self.btn_cleanup = QPushButton("inputMD をクリーンアップ…")
        maint_layout.addWidget(self.btn_cleanup)
        layout.addWidget(maint_group)

        layout.addStretch()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("sqlserver-xel-toolkit")
        self.resize(1280, 800)

        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.ws: Optional[WorkspaceState] = None

        # ---- Status bar (top label in toolbar) ----
        from PySide6.QtWidgets import QToolBar
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self._status_label = QLabel("")
        tb.addWidget(self._status_label)

        # ---- Tabs ----
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        # Tab 0: レポート閲覧
        self._report_tab = ReportBrowserTab()
        self._tabs.addTab(self._report_tab, "📋 レポート閲覧")

        # Tab 1: 新規実行
        self._run_tab = RunTab()
        self._tabs.addTab(self._run_tab, "▶ 新規実行")

        # Tab 2: inputMD 閲覧
        self._inputmd_tab = InputMdBrowserWidget()
        self._tabs.addTab(self._inputmd_tab, "📁 inputMD")

        # Tab 3: 集計・分析 (AggregateWidget – workspace set later)
        self._agg_widget = AggregateWidget(
            workspace_root="",
            db_path="",
            ranges_path=None,
        )
        self._tabs.addTab(self._agg_widget, "📊 集計・分析")

        # Tab 4: 時間範囲
        self._range_widget = TimeRangeWidget(None)
        self._range_widget.ranges_saved.connect(self._on_ranges_saved)
        self._tabs.addTab(self._range_widget, "⏰ 時間範囲")

        # Tab 5: 設定・管理
        self._settings_tab = SettingsTab()
        self._tabs.addTab(self._settings_tab, "⚙ 設定・管理")

        # Tab-change: lazy-refresh inputMD browser when tab becomes active
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ---- Slim menu bar ----
        mbar = self.menuBar()

        m_ws = mbar.addMenu("ワークスペース")
        act_open_ws = QAction("フォルダを開く…", self)
        act_open_ws.triggered.connect(self.open_workspace)
        m_ws.addAction(act_open_ws)
        act_change_ws = QAction("切り替え…", self)
        act_change_ws.triggered.connect(self.change_workspace)
        m_ws.addAction(act_change_ws)

        m_view = mbar.addMenu("表示")
        self.act_apply_ranges = QAction("時間範囲フィルタを表示に適用", self)
        self.act_apply_ranges.setCheckable(True)
        self.act_apply_ranges.setChecked(True)
        self.act_apply_ranges.triggered.connect(lambda _: self.reload_lists())
        m_view.addAction(self.act_apply_ranges)
        act_refresh = QAction("再読み込み", self)
        act_refresh.triggered.connect(self.reload_lists)
        m_view.addAction(act_refresh)

        # ---- Connect signals ----
        # Report tab
        self._report_tab.run_list.itemSelectionChanged.connect(self.on_select_run)
        self._report_tab.file_list.itemSelectionChanged.connect(self.on_select_file)
        self._report_tab.report_list.itemSelectionChanged.connect(self.on_select_report)
        self._report_tab.preview.anchorClicked.connect(self.on_preview_link_clicked)
        self._report_tab.search_box.returnPressed.connect(self.do_search)
        self._report_tab.btn_regen.clicked.connect(self.regen_reports_for_selected_files)
        self._report_tab.btn_del_run.clicked.connect(self.delete_selected_run)
        self._report_tab.btn_del_file.clicked.connect(self.delete_selected_file)
        self._report_tab.btn_del_report.clicked.connect(self.delete_selected_report)

        # Run tab
        self._run_tab.btn_add_files.clicked.connect(self._run_tab_add_files)
        self._run_tab.btn_clear_files.clicked.connect(self._run_tab.file_list.clear)
        self._run_tab.btn_run.clicked.connect(self.new_run)

        # Settings tab
        self._settings_tab.btn_open_ws.clicked.connect(self.open_workspace)
        self._settings_tab.btn_change_ws.clicked.connect(self.change_workspace)
        self._settings_tab.btn_cleanup.clicked.connect(self.cleanup_inputmd)

    # -----------------------------------------------------------------------
    # Workspace helpers
    # -----------------------------------------------------------------------

    def _ranges_path(self) -> Optional[str]:
        if not self.ws:
            return None
        return os.path.join(self.ws.root, "ranges.json")

    def _ranges_summary(self) -> str:
        if not self.ws:
            return ""
        rp = self._ranges_path()
        if not rp or not os.path.exists(rp):
            return "Range: (none)"
        items = load_ranges(rp)
        if not items:
            return "Range: (empty)"
        first = items[0]
        last = items[-1]
        if len(items) == 1:
            return f"Range: 1 ({first.start}~{first.end})"
        return f"Range: {len(items)} ({first.start}~{first.end}, ... , {last.start}~{last.end})"

    def _update_status(self):
        if not self.ws:
            self._status_label.setText("ワークスペース: (未設定)")
            return
        apply = "ON" if self.act_apply_ranges.isChecked() else "OFF"
        self._status_label.setText(
            f"ワークスペース: {os.path.basename(self.ws.root)}  |  "
            f"時間範囲フィルタ: {apply}  |  {self._ranges_summary()}"
        )

    def _set_workspace(self, d: str):
        if not d:
            return
        db_path = os.path.join(d, "workspace.db")
        init_db(db_path)
        self.ws = WorkspaceState(root=d, db_path=db_path)
        os.environ["XEL_TOOLKIT_WORKSPACE"] = d
        self._update_status()
        # Update embedded widgets
        rp = self._ranges_path()
        self._agg_widget.refresh_workspace(d, db_path, rp)
        self._range_widget.set_ranges_path(rp)
        self._inputmd_tab.set_workspace(d)
        self._settings_tab.ws_label.setText(d)
        self.reload_lists()

    def open_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "ワークスペースフォルダを選択")
        if not d:
            return
        from .settings import load_config, save_config, push_recent
        cfg = load_config()
        cfg.recent_workspaces = push_recent(cfg.recent_workspaces, d)
        save_config(cfg)
        self._set_workspace(d)

    def change_workspace(self):
        d = WorkspaceSelectDialog.select_workspace(self)
        if not d:
            return
        self._set_workspace(d)

    # -----------------------------------------------------------------------
    # Report browser tab
    # -----------------------------------------------------------------------

    def reload_lists(self):
        if not self.ws:
            return
        self._update_status()
        conn = connect_db(self.ws.db_path)
        try:
            self._report_tab.run_list.clear()
            for r in list_runs(conn):
                item = QListWidgetItem(f"#{r.id} {r.started_at}  (slow>={r.slow_threshold_sec}s)")
                item.setData(Qt.UserRole, r.id)
                self._report_tab.run_list.addItem(item)

            self._report_tab.file_list.clear()
            self.populate_reports(conn, run_id=None, file_id=None)
        finally:
            conn.close()

    def _report_in_current_ranges(self, rep) -> bool:
        if not self.ws:
            return True
        if not self.act_apply_ranges.isChecked():
            return True
        rp = self._ranges_path()
        if not rp or not os.path.exists(rp):
            return True
        items = load_ranges(rp)
        if not items:
            return True
        if not rep.event_time_min or not rep.event_time_max:
            return True
        a0 = rep.event_time_min
        a1 = rep.event_time_max
        for it in items:
            if a0 <= it.end and it.start <= a1:
                return True
        return False

    def populate_reports(self, conn, run_id: Optional[int], file_id: Optional[int]):
        self._report_tab.report_list.clear()
        for rep in list_reports(conn, run_id=run_id, file_id=file_id):
            if not self._report_in_current_ranges(rep):
                continue
            title = rep.title or os.path.basename(rep.md_path or rep.xlsx_path or "")
            span = ""
            if rep.event_time_min and rep.event_time_max:
                span = f" ({rep.event_time_min}~{rep.event_time_max})"
            extra = " [xlsx]" if rep.xlsx_path and os.path.exists(rep.xlsx_path) else ""
            item = QListWidgetItem(f"[{rep.type}] {title}{extra}{span}")
            item.setData(Qt.UserRole, rep.id)
            self._report_tab.report_list.addItem(item)

    def on_select_run(self):
        if not self.ws:
            return
        items = self._report_tab.run_list.selectedItems()
        run_id = int(items[0].data(Qt.UserRole)) if items else None

        self._report_tab.file_list.clear()
        self._report_tab.report_list.clear()
        self._report_tab.preview.setPlainText("")

        if run_id is None:
            return

        conn = connect_db(self.ws.db_path)
        try:
            for f in list_files(conn, run_id):
                it = QListWidgetItem(f.name)
                it.setData(Qt.UserRole, f.id)
                self._report_tab.file_list.addItem(it)
            if self._report_tab.file_list.count() == 0:
                self.populate_reports(conn, run_id=run_id, file_id=None)
        finally:
            conn.close()

    def on_select_file(self):
        if not self.ws:
            return
        run_items = self._report_tab.run_list.selectedItems()
        run_id = int(run_items[0].data(Qt.UserRole)) if run_items else None
        file_items = self._report_tab.file_list.selectedItems()
        file_id = int(file_items[0].data(Qt.UserRole)) if file_items else None
        conn = connect_db(self.ws.db_path)
        try:
            self.populate_reports(conn, run_id=run_id, file_id=file_id)
        finally:
            conn.close()

    def on_preview_link_clicked(self, url: QUrl):
        try:
            QDesktopServices.openUrl(url)
        except Exception:
            pass

    def on_select_report(self):
        if not self.ws:
            return
        items = self._report_tab.report_list.selectedItems()
        if not items:
            return
        report_id = int(items[0].data(Qt.UserRole))
        conn = connect_db(self.ws.db_path)
        try:
            rep = get_report(conn, report_id)
        finally:
            conn.close()

        links = []
        if rep:
            if rep.md_path and os.path.exists(rep.md_path):
                md_url = QUrl.fromLocalFile(rep.md_path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
                links.append(f'<a href="{md_url}">MDを開く</a>')
            if rep.xlsx_path and os.path.exists(rep.xlsx_path):
                xlsx_url = QUrl.fromLocalFile(rep.xlsx_path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
                links.append(f'<a href="{xlsx_url}">Excelを開く</a>')
        header = ("<p>" + " | ".join(links) + "</p><hr/>") if links else ""

        if not rep or not rep.md_path or not os.path.exists(rep.md_path):
            self._report_tab.preview.setHtml(header + "<p>(no markdown)</p>")
            return

        with open(rep.md_path, "r", encoding="utf-8") as f:
            text = f.read()
        html = mdlib.markdown(text, extensions=["tables", "fenced_code"])
        self._report_tab.preview.setHtml(header + html)

    def do_search(self):
        if not self.ws:
            return
        q = self._report_tab.search_box.text().strip()
        if not q:
            self.reload_lists()
            return
        conn = connect_db(self.ws.db_path)
        try:
            ids = set(search_reports(conn, q))
            run_items = self._report_tab.run_list.selectedItems()
            run_id = int(run_items[0].data(Qt.UserRole)) if run_items else None

            self._report_tab.report_list.clear()
            for rep in list_reports(conn, run_id=None, file_id=None):
                if rep.id not in ids:
                    continue
                if run_id is not None and rep.run_id != run_id:
                    continue
                if not self._report_in_current_ranges(rep):
                    continue
                title = rep.title or os.path.basename(rep.md_path or rep.xlsx_path or "")
                span = ""
                if rep.event_time_min and rep.event_time_max:
                    span = f" ({rep.event_time_min}~{rep.event_time_max})"
                extra = " [xlsx]" if rep.xlsx_path and os.path.exists(rep.xlsx_path) else ""
                item = QListWidgetItem(f"[{rep.type}] {title}{extra}{span}")
                item.setData(Qt.UserRole, rep.id)
                self._report_tab.report_list.addItem(item)
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Tab change – lazy refresh
    # -----------------------------------------------------------------------

    # Tab index constants (update here if tabs are reordered)
    TAB_REPORTS   = 0
    TAB_NEW_RUN   = 1
    TAB_INPUTMD   = 2
    TAB_AGGREGATE = 3
    TAB_RANGES    = 4
    TAB_SETTINGS  = 5

    def _on_tab_changed(self, index: int) -> None:
        """Lazy-refresh content when switching to certain tabs."""
        if index == self.TAB_INPUTMD and self.ws:
            # Refresh inputMD file counts in case a new run created new files
            self._inputmd_tab.refresh()

    # -----------------------------------------------------------------------
    # Time range tab
    # -----------------------------------------------------------------------

    def _on_ranges_saved(self):
        """Called when TimeRangeWidget saves ranges.json."""
        self._update_status()
        self.reload_lists()
        # Refresh aggregate widget's ranges checkbox
        rp = self._ranges_path()
        if rp:
            self._agg_widget.ranges_path = rp
            self._agg_widget._chk_ranges.setChecked(os.path.exists(rp))

    # -----------------------------------------------------------------------
    # Run tab
    # -----------------------------------------------------------------------

    def _run_tab_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "入力ファイルを選択 (.xel/.csv/.xlsx)",
            os.path.expanduser("~/Downloads"),
            "All supported (*.xel *.csv *.xlsx *.xls);;XEL (*.xel);;CSV (*.csv);;Excel (*.xlsx *.xls)"
        )
        for f in files:
            # Avoid duplicates
            existing = [self._run_tab.file_list.item(i).text() for i in range(self._run_tab.file_list.count())]
            if f not in existing:
                self._run_tab.file_list.addItem(f)

    def new_run(self):
        if not self.ws:
            QMessageBox.warning(self, "ワークスペース", "先にワークスペースを開いてください")
            self._tabs.setCurrentIndex(self.TAB_SETTINGS)
            return

        files = self._run_tab.selected_files()
        if not files:
            QMessageBox.information(self, "新規実行", "ファイルを追加してください")
            return

        try:
            slow = float(self._run_tab.slow_thr_edit.text() or "3.0")
        except ValueError:
            slow = 3.0

        # inputMD: dedup is handled by xel_to_md.py; no overwrite dialog needed

        out_dir = os.path.join(self.ws.root, "runs", datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(out_dir, exist_ok=True)

        self._run_tab.clear_log()
        self._run_tab.btn_run.setEnabled(False)
        self._tabs.setCurrentIndex(self.TAB_NEW_RUN)  # stay on run tab to show log

        self.worker = RunWorker(
            self.repo_root, files, out_dir, slow,
            self.ws.root if self.ws else None,
            self._ranges_path() if self.ws else None,
        )
        self.worker.log.connect(self._run_tab.append_log)

        def _err(msg: str):
            self._run_tab.btn_run.setEnabled(True)
            QMessageBox.warning(self, "実行", f"失敗しました\n{msg}")
            self.reload_lists()

        self.worker.finished_err.connect(_err)
        self.worker.finished_ok.connect(lambda _: self.on_run_finished(files, out_dir, slow))
        self.worker.start()

    def on_run_finished(self, xel_files: List[str], out_dir: str, slow: float):
        conn = connect_db(self.ws.db_path)
        try:
            run_id = replace_run_by_out_dir(
                conn,
                started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                out_dir=out_dir,
                slow_threshold_sec=slow,
            )
            for x in xel_files:
                add_input(conn, run_id=run_id, path=x)

            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for sub in sorted(os.listdir(out_dir)):
                if sub == "tmp":
                    continue
                subdir = os.path.join(out_dir, sub)
                if not os.path.isdir(subdir):
                    continue

                file_id = add_file(conn, run_id=run_id, name=sub, out_dir=subdir)

                for fn in sorted(os.listdir(subdir)):
                    p = os.path.join(subdir, fn)
                    if fn.endswith("_deadlock_report.md"):
                        base2 = fn.replace("_deadlock_report.md", "")
                        xlsx = os.path.join(subdir, base2 + "_deadlock.xlsx")
                        mn, mx = self._read_span_meta(p)
                        add_report(conn, run_id=run_id, file_id=file_id, type_="deadlock", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at, event_time_min=mn, event_time_max=mx)
                    elif fn.endswith("_slowquery_report.md"):
                        base = fn.replace("_slowquery_report.md", "")
                        xlsx = os.path.join(subdir, base + "_slowquery.xlsx")
                        mn, mx = self._read_span_meta(p)
                        add_report(conn, run_id=run_id, file_id=file_id, type_="slowquery", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at, event_time_min=mn, event_time_max=mx)
                    elif fn.endswith("_blocking_report.md"):
                        base = fn.replace("_blocking_report.md", "")
                        xlsx = os.path.join(subdir, base + "_blocking.xlsx")
                        mn, mx = self._read_span_meta(p)
                        add_report(conn, run_id=run_id, file_id=file_id, type_="blocking", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at, event_time_min=mn, event_time_max=mx)

                md_root = os.path.join(subdir, "md")
                if os.path.isdir(md_root):
                    for root2, _, files2 in os.walk(md_root):
                        for fn2 in sorted(files2):
                            if not fn2.endswith(".md"):
                                continue
                            p2 = os.path.join(root2, fn2)
                            rel = os.path.relpath(p2, subdir)
                            add_report(conn, run_id=run_id, file_id=file_id, type_="mditem", title=rel, md_path=p2, xlsx_path=None, created_at=created_at)

            conn.commit()
        finally:
            conn.close()

        self._run_tab.btn_run.setEnabled(True)
        QMessageBox.information(self, "完了", f"レポート生成先: {out_dir}")
        self.reload_lists()
        # Refresh aggregate run list and inputMD browser
        rp = self._ranges_path()
        self._agg_widget.refresh_workspace(self.ws.root, self.ws.db_path, rp)
        self._inputmd_tab.refresh()
        # Switch to report tab
        self._tabs.setCurrentIndex(self.TAB_REPORTS)

    def _read_span_meta(self, meta_for_path: str):
        mp = meta_for_path + ".meta.json"
        if not os.path.exists(mp):
            return None, None
        try:
            with open(mp, "r", encoding="utf-8") as f:
                obj = json.load(f) or {}
            return obj.get("event_time_min"), obj.get("event_time_max")
        except Exception:
            return None, None

    # -----------------------------------------------------------------------
    # Regen
    # -----------------------------------------------------------------------

    def _latest_prefix_from_tmp(self, run_out_dir: str, *, file_hash: str) -> Optional[str]:
        tmpdir = os.path.join(run_out_dir, "tmp")
        if not os.path.isdir(tmpdir):
            return None
        patterns = [
            f"*_{file_hash}_*_deadlock.jsonl",
            f"*_{file_hash}_*_blocking.jsonl",
            f"*_{file_hash}_*_slow.jsonl",
        ]
        cand: list[str] = []
        for pat in patterns:
            cand.extend(glob.glob(os.path.join(tmpdir, pat)))
        if not cand:
            return None
        cand.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        fn = os.path.basename(cand[0])
        for suf in ("_deadlock.jsonl", "_blocking.jsonl", "_slow.jsonl"):
            if fn.endswith(suf):
                return fn[: -len(suf)]
        return None

    def _clear_report_outputs(self, file_out_dir: str) -> None:
        patterns = [
            "*_deadlock_report.md", "*_blocking_report.md", "*_blocking.xlsx",
            "*_slowquery_report.md", "*_slowquery.xlsx", "*.meta.json",
        ]
        for pat in patterns:
            for p in glob.glob(os.path.join(file_out_dir, pat)):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def _run_regen_for_file(self, *, run_id: int, file_id: int, run_out_dir: str, file_out_dir: str, source_xel: str):
        if not self.ws:
            return
        bn = os.path.basename(file_out_dir.rstrip(os.sep))
        m = re.search(r"_([0-9a-f]{8})$", bn)
        file_hash = m.group(1) if m else ""

        prefix = self._latest_prefix_from_tmp(run_out_dir, file_hash=file_hash) if file_hash else None
        if not prefix:
            QMessageBox.warning(self, "再生成", f"tmp/jsonl が見つからないためスキップしました。\n{run_out_dir}")
            return

        self._clear_report_outputs(file_out_dir)

        conn = connect_db(self.ws.db_path)
        try:
            delete_reports_by_file_db(conn, file_id, types=["deadlock", "blocking", "slowquery"])
        finally:
            conn.close()

        rp = self._ranges_path() if self.ws else None

        self._regen_worker = ReportRegenWorker(
            self.repo_root,
            out_dir=run_out_dir,
            file_out_dir=file_out_dir,
            source_xel=source_xel,
            prefix=prefix,
            ranges_path=rp,
        )
        self._regen_worker.log.connect(self._run_tab.append_log)

        def _done(_):
            conn2 = connect_db(self.ws.db_path)
            try:
                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for fn in sorted(os.listdir(file_out_dir)):
                    p = os.path.join(file_out_dir, fn)
                    if fn.endswith("_deadlock_report.md"):
                        base2 = fn.replace("_deadlock_report.md", "")
                        xlsx = os.path.join(file_out_dir, base2 + "_deadlock.xlsx")
                        mn, mx = self._read_span_meta(p)
                        add_report(conn2, run_id=run_id, file_id=file_id, type_="deadlock", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at, event_time_min=mn, event_time_max=mx)
                    elif fn.endswith("_slowquery_report.md"):
                        base = fn.replace("_slowquery_report.md", "")
                        xlsx = os.path.join(file_out_dir, base + "_slowquery.xlsx")
                        mn, mx = self._read_span_meta(p)
                        add_report(conn2, run_id=run_id, file_id=file_id, type_="slowquery", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at, event_time_min=mn, event_time_max=mx)
                    elif fn.endswith("_blocking_report.md"):
                        base = fn.replace("_blocking_report.md", "")
                        xlsx = os.path.join(file_out_dir, base + "_blocking.xlsx")
                        mn, mx = self._read_span_meta(p)
                        add_report(conn2, run_id=run_id, file_id=file_id, type_="blocking", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at, event_time_min=mn, event_time_max=mx)
                conn2.commit()
            finally:
                conn2.close()

            self.reload_lists()
            QMessageBox.information(self, "再生成", f"レポートを再生成しました。\n{file_out_dir}")

        def _err(msg: str):
            QMessageBox.warning(self, "再生成", f"再生成に失敗しました\n{msg}")
            self.reload_lists()

        self._regen_worker.finished_ok.connect(_done)
        self._regen_worker.finished_err.connect(_err)
        self._regen_worker.start()

    def regen_reports_for_selected_files(self):
        if not self.ws:
            QMessageBox.warning(self, "再生成", "ワークスペースを開いてください")
            return
        items = self._report_tab.file_list.selectedItems()
        if not items:
            QMessageBox.information(self, "再生成", "対象のFileを選択してください")
            return
        if len(items) > 1:
            QMessageBox.information(self, "再生成", "現在は1つのFileのみ対応です（先頭のみ処理します）")

        file_id = int(items[0].data(Qt.UserRole))

        conn = connect_db(self.ws.db_path)
        try:
            fr = conn.execute("SELECT run_id, out_dir FROM files WHERE id=?", (file_id,)).fetchone()
            if not fr:
                return
            run_id = int(fr[0])
            file_out_dir = str(fr[1])
            rr = conn.execute("SELECT out_dir FROM runs WHERE id=?", (run_id,)).fetchone()
            run_out_dir = rr[0] if rr else ""
            src = conn.execute("SELECT path FROM inputs WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
            source_xel = src[0] if src else ""
        finally:
            conn.close()

        if QMessageBox.question(self, "確認", "選択したFileのレポートを再生成しますか？\n(既存のレポートは上書きされます)") != QMessageBox.StandardButton.Yes:
            return

        self._tabs.setCurrentIndex(self.TAB_NEW_RUN)  # show run tab (log)
        self._run_regen_for_file(run_id=run_id, file_id=file_id, run_out_dir=run_out_dir, file_out_dir=file_out_dir, source_xel=source_xel)

    # -----------------------------------------------------------------------
    # Delete
    # -----------------------------------------------------------------------

    def delete_selected_run(self):
        if not self.ws:
            QMessageBox.warning(self, "ワークスペース", "先にワークスペースを開いてください")
            return
        items = self._report_tab.run_list.selectedItems()
        if not items:
            QMessageBox.information(self, "Run", "削除する Run を選択してください")
            return

        run_ids = [int(it.data(Qt.UserRole)) for it in items]

        conn = connect_db(self.ws.db_path)
        try:
            out_dirs = []
            for run_id in run_ids:
                r = conn.execute("SELECT out_dir FROM runs WHERE id=?", (run_id,)).fetchone()
                if r and r[0]:
                    out_dirs.append(r[0])
        finally:
            conn.close()

        if not out_dirs:
            QMessageBox.warning(self, "Run", "選択した Run の出力先が見つかりません")
            return

        dlg = DeleteRunDialog(self.ws.root, out_dirs, self)
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return

        if dlg.choice.delete_files:
            trash_paths([d for d in out_dirs if os.path.exists(d)])

        conn = connect_db(self.ws.db_path)
        try:
            for run_id in run_ids:
                delete_run_db(conn, run_id)
        finally:
            conn.close()

        self.reload_lists()

    def delete_selected_file(self):
        if not self.ws:
            QMessageBox.warning(self, "ワークスペース", "先にワークスペースを開いてください")
            return
        file_items = self._report_tab.file_list.selectedItems()
        if not file_items:
            QMessageBox.information(self, "File", "削除する File を選択してください")
            return

        file_ids = [int(it.data(Qt.UserRole)) for it in file_items]
        file_names = [it.text() for it in file_items]

        conn = connect_db(self.ws.db_path)
        try:
            out_dirs = []
            for file_id in file_ids:
                r = conn.execute("SELECT out_dir FROM files WHERE id=?", (file_id,)).fetchone()
                if r and r[0]:
                    out_dirs.append(r[0])
        finally:
            conn.close()

        if not out_dirs:
            QMessageBox.warning(self, "File", "選択した File の出力先が見つかりません")
            return

        dlg = DeleteFileDialog(self.ws.root, out_dirs, file_names, self)
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return

        if dlg.choice.delete_files:
            trash_paths([d for d in out_dirs if os.path.exists(d)])

        conn = connect_db(self.ws.db_path)
        try:
            for file_id in file_ids:
                delete_file_db(conn, file_id)
        finally:
            conn.close()

        self.reload_lists()

    def delete_selected_report(self):
        if not self.ws:
            QMessageBox.warning(self, "ワークスペース", "先にワークスペースを開いてください")
            return
        rep_items = self._report_tab.report_list.selectedItems()
        if not rep_items:
            QMessageBox.information(self, "Report", "削除する Report を選択してください")
            return

        report_ids = [int(it.data(Qt.UserRole)) for it in rep_items]

        conn = connect_db(self.ws.db_path)
        try:
            reps = [get_report(conn, rid) for rid in report_ids]
        finally:
            conn.close()

        reps = [r for r in reps if r]
        if not reps:
            QMessageBox.warning(self, "Report", "選択した Report が見つかりません")
            return

        title = f"{len(reps)} Reports"
        paths = []
        for rep in reps:
            for p in [rep.md_path, rep.xlsx_path]:
                if p:
                    paths.append(p)

        dlg = DeleteReportDialog(self.ws.root, title, paths, self)
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return

        if dlg.choice.delete_files:
            trash_paths(paths)

        conn = connect_db(self.ws.db_path)
        try:
            for rid in report_ids:
                delete_report_db(conn, rid)
        finally:
            conn.close()

        self.reload_lists()

    # -----------------------------------------------------------------------
    # Maintenance
    # -----------------------------------------------------------------------

    def cleanup_inputmd(self):
        if not self.ws:
            QMessageBox.warning(self, "ワークスペース", "先にワークスペースを開いてください")
            return
        dlg = CleanupInputMdDialog(self.ws.root, self)
        dlg.exec()


def main():
    app = QApplication([])
    w = MainWindow()

    last = WorkspaceSelectDialog.last_workspace()
    if last:
        w._set_workspace(last)

    w.show()
    app.exec()


if __name__ == "__main__":
    main()

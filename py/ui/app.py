from __future__ import annotations

import os
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
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtCore import QUrl

from PySide6.QtWidgets import QDialog, QAbstractItemView, QCheckBox

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

from .time_range_dialog import TimeRangeDialog, load_ranges
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
    import re
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._-")
    return s or "input"


def _hash8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:8]


def inputmd_dir(workspace_root: str, input_path: str) -> str:
    """Stable per-file inputMD dir to avoid collisions (uses path hash)."""
    key = _hash8(str(Path(input_path).resolve()))
    safe = _safe_stem(input_path)
    return str(Path(workspace_root) / "inputMD" / f"{safe}_{key}")


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

            # inputMD overwrite/append mode (shared config)
            if os.environ.get("XEL_TOOLKIT_INPUTMD_MODE"):
                env["XEL_TOOLKIT_INPUTMD_MODE"] = os.environ.get("XEL_TOOLKIT_INPUTMD_MODE")

            # Windows cannot execute .sh directly; use PowerShell runner.
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
            # On Windows, subprocess text decoding can crash due to cp932/UTF-8 mismatch.
            # Force UTF-8 with replacement to keep the UI responsive.
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("sqlserver-xel-toolkit")
        self.resize(1200, 800)

        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        self.ws: Optional[WorkspaceState] = None

        # UI
        self.run_list = QListWidget()
        self.run_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.report_list = QListWidget()
        self.report_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.preview = QTextBrowser()
        # Handle clicks ourselves to avoid QTextBrowser trying to render local files (can show mojibake)
        self.preview.setOpenExternalLinks(False)
        self.preview.setOpenLinks(False)
        self.preview.anchorClicked.connect(self.on_preview_link_clicked)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("全文検索 (FTS5)...")
        self.search_box.returnPressed.connect(self.do_search)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.run_list)
        splitter.addWidget(self.file_list)
        splitter.addWidget(self.report_list)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setStretchFactor(3, 6)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.search_box)
        layout.addWidget(splitter)
        self.setCentralWidget(root)

        self.run_list.itemSelectionChanged.connect(self.on_select_run)
        self.file_list.itemSelectionChanged.connect(self.on_select_file)
        self.report_list.itemSelectionChanged.connect(self.on_select_report)

        # Menus (reduce toolbar clutter)
        mbar = self.menuBar()

        m_ws = mbar.addMenu("ワークスペース")
        act_open_ws = QAction("開く…", self)
        act_open_ws.triggered.connect(self.open_workspace)
        m_ws.addAction(act_open_ws)

        act_change_ws = QAction("切り替え…", self)
        act_change_ws.triggered.connect(self.change_workspace)
        m_ws.addAction(act_change_ws)

        m_run = mbar.addMenu("実行")
        act_new_run = QAction("新規実行…", self)
        act_new_run.triggered.connect(self.new_run)
        m_run.addAction(act_new_run)

        act_regen_file = QAction("選択したFileのレポート再生成…", self)
        act_regen_file.triggered.connect(self.regen_reports_for_selected_files)
        m_run.addAction(act_regen_file)

        m_run.addSeparator()

        act_refresh = QAction("再読み込み", self)
        act_refresh.triggered.connect(self.reload_lists)
        m_run.addAction(act_refresh)

        m_ranges = mbar.addMenu("時間範囲")
        act_edit_ranges = QAction("編集…", self)
        act_edit_ranges.triggered.connect(self.open_time_ranges)
        m_ranges.addAction(act_edit_ranges)

        self.act_apply_ranges = QAction("表示に適用", self)
        self.act_apply_ranges.setCheckable(True)
        self.act_apply_ranges.setChecked(True)
        self.act_apply_ranges.triggered.connect(lambda _: self.reload_lists())
        m_ranges.addAction(self.act_apply_ranges)

        m_tools = mbar.addMenu("ツール")
        act_open_it = QAction("統合ツールを開く", self)
        act_open_it.triggered.connect(self.open_integratedtool)
        m_tools.addAction(act_open_it)

        m_maint = mbar.addMenu("メンテナンス")
        act_cleanup = QAction("inputMD をクリーンアップ…", self)
        act_cleanup.triggered.connect(self.cleanup_inputmd)
        m_maint.addAction(act_cleanup)

        m_del = mbar.addMenu("削除")
        act_del_run = QAction("Run を削除…", self)
        act_del_run.triggered.connect(self.delete_selected_run)
        m_del.addAction(act_del_run)

        act_del_file = QAction("File を削除…", self)
        act_del_file.triggered.connect(self.delete_selected_file)
        m_del.addAction(act_del_file)

        act_del_report = QAction("Report を削除…", self)
        act_del_report.triggered.connect(self.delete_selected_report)
        m_del.addAction(act_del_report)

        # Minimal toolbar
        tb = QToolBar("Main")
        self.addToolBar(tb)

        btn_ws = QPushButton("ワークスペース…")
        btn_ws.clicked.connect(self.change_workspace)
        tb.addWidget(btn_ws)

        btn_new_run = QPushButton("新規実行")
        btn_new_run.clicked.connect(self.new_run)
        tb.addWidget(btn_new_run)

        btn_ranges = QPushButton("時間範囲…")
        btn_ranges.clicked.connect(self.open_time_ranges)
        tb.addWidget(btn_ranges)

        btn_refresh = QPushButton("再読み込み")
        btn_refresh.clicked.connect(self.reload_lists)
        tb.addWidget(btn_refresh)

        btn_it = QPushButton("統合ツール")
        btn_it.clicked.connect(self.open_integratedtool)
        tb.addWidget(btn_it)

        tb.addSeparator()

        self.status = QLabel("")
        tb.addWidget(self.status)

    def _ranges_summary(self) -> str:
        if not self.ws:
            return ""
        rp = self._ranges_path()
        if not rp or not os.path.exists(rp):
            return "Range: (none)"
        items = load_ranges(rp)
        if not items:
            return "Range: (empty)"

        # Compact display: show count + first/last
        first = items[0]
        last = items[-1]
        if len(items) == 1:
            return f"Range: 1 ({first.start}~{first.end})"
        return f"Range: {len(items)} ({first.start}~{first.end}, ... , {last.start}~{last.end})"

    def _update_status(self):
        if not self.ws:
            self.status.setText("")
            return
        apply = "ON" if self.act_apply_ranges.isChecked() else "OFF"
        self.status.setText(f"{os.path.basename(self.ws.root)} | RangeView:{apply} | {self._ranges_summary()}")

    def _set_workspace(self, d: str):
        if not d:
            return
        db_path = os.path.join(d, "workspace.db")
        init_db(db_path)
        self.ws = WorkspaceState(root=d, db_path=db_path)
        # keep env consistent for integrated tools
        os.environ["XEL_TOOLKIT_WORKSPACE"] = d
        self._update_status()
        self.reload_lists()

    def open_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "Choose workspace directory")
        if not d:
            return
        # Save to shared recent list
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

    def reload_lists(self):
        if not self.ws:
            return
        self._update_status()
        conn = connect_db(self.ws.db_path)
        try:
            self.run_list.clear()
            for r in list_runs(conn):
                item = QListWidgetItem(f"#{r.id} {r.started_at}  (slow>={r.slow_threshold_sec}s)")
                item.setData(Qt.UserRole, r.id)
                self.run_list.addItem(item)

            self.file_list.clear()
            # default: all reports
            self.populate_reports(conn, run_id=None, file_id=None)
        finally:
            conn.close()

    def _report_in_current_ranges(self, rep) -> bool:
        """View filter: if enabled, only show reports overlapping current ranges.json."""
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

        # If report has no span (older DB), keep it visible for compatibility
        if not rep.event_time_min or not rep.event_time_max:
            return True

        a0 = rep.event_time_min
        a1 = rep.event_time_max
        # String compare works for YYYY-mm-dd HH:MM format
        for it in items:
            b0 = it.start
            b1 = it.end
            if a0 <= b1 and b0 <= a1:
                return True
        return False

    def populate_reports(self, conn, run_id: Optional[int], file_id: Optional[int]):
        self.report_list.clear()
        for rep in list_reports(conn, run_id=run_id, file_id=file_id):
            if not self._report_in_current_ranges(rep):
                continue
            title = rep.title or os.path.basename(rep.md_path or rep.xlsx_path or "")
            span = ""
            if rep.event_time_min and rep.event_time_max:
                span = f" ({rep.event_time_min}~{rep.event_time_max})"
            extra = ""
            if rep.xlsx_path and os.path.exists(rep.xlsx_path):
                extra = " [xlsx]"
            item = QListWidgetItem(f"[{rep.type}] {title}{extra}{span}")
            item.setData(Qt.UserRole, rep.id)
            self.report_list.addItem(item)

    def on_select_run(self):
        if not self.ws:
            return
        items = self.run_list.selectedItems()
        run_id = int(items[0].data(Qt.UserRole)) if items else None

        self.file_list.clear()
        self.report_list.clear()
        self.preview.setPlainText("")

        if run_id is None:
            return

        conn = connect_db(self.ws.db_path)
        try:
            for f in list_files(conn, run_id):
                it = QListWidgetItem(f.name)
                it.setData(Qt.UserRole, f.id)
                self.file_list.addItem(it)
            # If no file rows (older runs), fall back to showing reports by run
            if self.file_list.count() == 0:
                self.populate_reports(conn, run_id=run_id, file_id=None)
        finally:
            conn.close()

    def on_select_file(self):
        if not self.ws:
            return
        run_items = self.run_list.selectedItems()
        run_id = int(run_items[0].data(Qt.UserRole)) if run_items else None
        file_items = self.file_list.selectedItems()
        file_id = int(file_items[0].data(Qt.UserRole)) if file_items else None
        conn = connect_db(self.ws.db_path)
        try:
            self.populate_reports(conn, run_id=run_id, file_id=file_id)
        finally:
            conn.close()

    def on_preview_link_clicked(self, url: QUrl):
        # Always open links externally (Excel/Explorer/editor) rather than rendering inside QTextBrowser.
        try:
            QDesktopServices.openUrl(url)
        except Exception:
            pass

    def on_select_report(self):
        if not self.ws:
            return
        items = self.report_list.selectedItems()
        if not items:
            return
        report_id = int(items[0].data(Qt.UserRole))
        conn = connect_db(self.ws.db_path)
        try:
            rep = get_report(conn, report_id)
        finally:
            conn.close()

        # Header links (open externally)
        links = []
        if rep:
            if rep.md_path and os.path.exists(rep.md_path):
                md_url = QUrl.fromLocalFile(rep.md_path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
                links.append(f'<a href="{md_url}">MDを開く</a>')
            if rep.xlsx_path and os.path.exists(rep.xlsx_path):
                xlsx_url = QUrl.fromLocalFile(rep.xlsx_path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
                links.append(f'<a href="{xlsx_url}">Excelを開く</a>')
        header = ""
        if links:
            header = "<p>" + " | ".join(links) + "</p><hr/>"

        if not rep or not rep.md_path or not os.path.exists(rep.md_path):
            if header:
                self.preview.setHtml(header + "<p>(no markdown)</p>")
            else:
                self.preview.setPlainText("(no markdown)\n")
            return

        with open(rep.md_path, "r", encoding="utf-8") as f:
            text = f.read()
        html = mdlib.markdown(text, extensions=["tables", "fenced_code"])
        self.preview.setHtml(header + html)

    def do_search(self):
        if not self.ws:
            return
        q = self.search_box.text().strip()
        if not q:
            self.reload_lists()
            return
        conn = connect_db(self.ws.db_path)
        try:
            ids = set(search_reports(conn, q))

            # Optional scope: if a run is selected, only show matches in that run
            run_items = self.run_list.selectedItems()
            run_id = int(run_items[0].data(Qt.UserRole)) if run_items else None

            self.report_list.clear()
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
                extra = ""
                if rep.xlsx_path and os.path.exists(rep.xlsx_path):
                    extra = " [xlsx]"
                item = QListWidgetItem(f"[{rep.type}] {title}{extra}{span}")
                item.setData(Qt.UserRole, rep.id)
                self.report_list.addItem(item)
        finally:
            conn.close()

    def _ranges_path(self) -> str | None:
        if not self.ws:
            return None
        return os.path.join(self.ws.root, "ranges.json")

    def open_time_ranges(self):
        if not self.ws:
            QMessageBox.warning(self, "Workspace", "Open workspace first")
            return
        path = self._ranges_path()
        assert path
        dlg = TimeRangeDialog(path, self)
        dlg.exec()
        # reflect changes
        self._update_status()
        self.reload_lists()

    def cleanup_inputmd(self):
        if not self.ws:
            QMessageBox.warning(self, "Workspace", "Open workspace first")
            return
        dlg = CleanupInputMdDialog(self.ws.root, self)
        dlg.exec()

    def delete_selected_run(self):
        if not self.ws:
            QMessageBox.warning(self, "Workspace", "Open workspace first")
            return
        items = self.run_list.selectedItems()
        if not items:
            QMessageBox.information(self, "Run", "请选择要删除的 Run")
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
            QMessageBox.warning(self, "Run", "找不到选中 Run 的 out_dir")
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
            QMessageBox.warning(self, "Workspace", "Open workspace first")
            return
        file_items = self.file_list.selectedItems()
        if not file_items:
            QMessageBox.information(self, "File", "请选择要删除的 File")
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
            QMessageBox.warning(self, "File", "找不到选中 File 的 out_dir")
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
            QMessageBox.warning(self, "Workspace", "Open workspace first")
            return
        rep_items = self.report_list.selectedItems()
        if not rep_items:
            QMessageBox.information(self, "Report", "请选择要删除的 Report")
            return

        report_ids = [int(it.data(Qt.UserRole)) for it in rep_items]

        conn = connect_db(self.ws.db_path)
        try:
            reps = [get_report(conn, rid) for rid in report_ids]
        finally:
            conn.close()

        reps = [r for r in reps if r]
        if not reps:
            QMessageBox.warning(self, "Report", "找不到选中 Report")
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

    def open_integratedtool(self):
        it_dir = os.path.join(self.repo_root, "integratedtool")
        entry = os.path.join(it_dir, "unified_report_viewer.py")
        if not os.path.exists(entry):
            QMessageBox.warning(self, "IntegratedTool", f"Not found: {entry}\nDid you init submodules?")
            return

        # Run IntegratedTool using a python that exists on this OS.
        # Prefer IntegratedTool's own venv if present; otherwise fall back to the current interpreter.
        try:
            env = os.environ.copy()
            if self.ws:
                env["XEL_TOOLKIT_WORKSPACE"] = self.ws.root
                rp = self._ranges_path()
                if rp and os.path.exists(rp):
                    env["XEL_TOOLKIT_RANGES_JSON"] = rp

            # IMPORTANT: On Windows, always prefer the current interpreter (sys.executable)
            # because it's typically the uv-managed venv with PySide6 installed.
            if os.name == "nt":
                py = sys.executable
                # If IntegratedTool has its own venv, we can prefer it, but only if it exists.
                it_venv_py = os.path.join(it_dir, ".venv", "Scripts", "python.exe")
                if os.path.exists(it_venv_py):
                    py = it_venv_py
            else:
                py = os.path.join(it_dir, ".venv", "bin", "python")
                if not os.path.exists(py):
                    py = sys.executable or "python3"

            if not py:
                raise RuntimeError("No python interpreter found to launch IntegratedTool")

            subprocess.Popen([py, entry], cwd=it_dir, env=env)
        except Exception as e:
            QMessageBox.critical(self, "IntegratedTool", str(e))

    def new_run(self):
        if not self.ws:
            QMessageBox.warning(self, "Workspace", "Open workspace first")
            return

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select input files (.xel/.csv/.xlsx)",
            os.path.expanduser("~/Downloads"),
            "All supported (*.xel *.csv *.xlsx *.xls);;XEL (*.xel);;CSV (*.csv);;Excel (*.xlsx *.xls)"
        )
        if not files:
            return

        # Settings dialog (minimal): ask slow threshold
        slow = 3.0

        # inputMD overwrite/append
        from .settings import load_config, save_config
        cfg = load_config()

        # Detect existing inputMD for selected files (path-based)
        existing = []
        for x in files:
            d = inputmd_dir(self.ws.root, x)
            if os.path.isdir(d) and os.listdir(d):
                existing.append(d)

        if existing and cfg.confirm_inputmd_overwrite:
            mb = QMessageBox(self)
            mb.setWindowTitle("inputMD")
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setText("同じ入力（パス一致）の inputMD が既に存在します。\nどう処理しますか？")
            mb.setInformativeText("デフォルト: 上書き（既存inputMDを削除して再生成）\n\n対象: " + "\n".join(existing[:5]) + ("\n..." if len(existing) > 5 else ""))
            btn_over = mb.addButton("上書き", QMessageBox.ButtonRole.AcceptRole)
            btn_app = mb.addButton("追記", QMessageBox.ButtonRole.DestructiveRole)
            mb.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
            chk = QCheckBox("次回から確認しない", mb)
            mb.setCheckBox(chk)
            mb.exec()
            clicked = mb.clickedButton()
            if clicked == btn_over:
                cfg.inputmd_mode = "overwrite"
            elif clicked == btn_app:
                cfg.inputmd_mode = "append"
            else:
                return
            if chk.isChecked():
                cfg.confirm_inputmd_overwrite = False
            save_config(cfg)

        # Export current mode to runner
        os.environ["XEL_TOOLKIT_INPUTMD_MODE"] = cfg.inputmd_mode

        # One Run per click (Run 1:N File). Runner handles multiple inputs and generates per-file subfolders.
        out_dir = os.path.join(self.ws.root, "runs", datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(out_dir, exist_ok=True)

        self.preview.setPlainText("Running... see log below\n")

        self.worker = RunWorker(
            self.repo_root,
            files,
            out_dir,
            slow,
            self.ws.root if self.ws else None,
            self._ranges_path() if self.ws else None,
        )
        self.worker.log.connect(self.append_log)

        def _err(msg: str):
            QMessageBox.warning(self, "実行", f"失敗しました\n{msg}")
            self.reload_lists()

        self.worker.finished_err.connect(_err)
        self.worker.finished_ok.connect(lambda _: self.on_run_finished(files, out_dir, slow))
        self.worker.start()

    def append_log(self, line: str):
        # append to preview temporarily
        cur = self.preview.toPlainText()
        self.preview.setPlainText(cur + line + "\n")

    def on_run_error(self, msg: str):
        QMessageBox.critical(self, "Run failed", msg)
        self.reload_lists()

    def _latest_prefix_from_tmp(self, run_out_dir: str, *, file_hash: str) -> str | None:
        tmpdir = os.path.join(run_out_dir, "tmp")
        if not os.path.isdir(tmpdir):
            return None

        # Pick most recent jsonl for this file (prefix includes file_hash)
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
        # Only remove report outputs; keep tmp/ and md/ folders
        patterns = [
            "*_deadlock_report.md",
            "*_blocking_report.md",
            "*_blocking.xlsx",
            "*_slowquery_report.md",
            "*_slowquery.xlsx",
            "*.meta.json",
        ]
        for pat in patterns:
            for p in glob.glob(os.path.join(file_out_dir, pat)):
                try:
                    os.remove(p)
                except Exception:
                    pass

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

    def on_run_finished(self, xel_files: List[str], out_dir: str, slow: float):
        # Scan outputs and insert into DB

        conn = connect_db(self.ws.db_path)
        try:
            # Keep only one DB row per stable out_dir
            run_id = replace_run_by_out_dir(
                conn,
                started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                out_dir=out_dir,
                slow_threshold_sec=slow,
            )
            for x in xel_files:
                add_input(conn, run_id=run_id, path=x)

            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Per-input subfolders: scan each folder and register file + reports
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
                        mn, mx = self._read_span_meta(p)
                        add_report(conn, run_id=run_id, file_id=file_id, type_="deadlock", title=fn, md_path=p, xlsx_path=None, created_at=created_at, event_time_min=mn, event_time_max=mx)
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

                # CSV/Excel generated MD items
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

        QMessageBox.information(self, "完了", f"レポート生成先: {out_dir}")
        self.reload_lists()

    def _run_regen_for_file(self, *, run_id: int, file_id: int, run_out_dir: str, file_out_dir: str, source_xel: str):
        if not self.ws:
            return
        # file_hash is embedded in per-file output folder name: <safe>_<hash8>
        bn = os.path.basename(file_out_dir.rstrip(os.sep))
        m = re.search(r"_([0-9a-f]{8})$", bn)
        file_hash = m.group(1) if m else ""

        prefix = self._latest_prefix_from_tmp(run_out_dir, file_hash=file_hash) if file_hash else None
        if not prefix:
            QMessageBox.warning(self, "再生成", f"tmp/jsonl が見つからないためスキップしました。\n{run_out_dir}")
            return

        # Clear current outputs (keep tmp)
        self._clear_report_outputs(file_out_dir)

        # Remove existing report rows for this file (keep mditem)
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
        self._regen_worker.log.connect(self.append_log)

        def _done(_):
            # Re-scan just this file_out_dir and insert reports
            conn2 = connect_db(self.ws.db_path)
            try:
                created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for fn in sorted(os.listdir(file_out_dir)):
                    p = os.path.join(file_out_dir, fn)
                    if fn.endswith("_deadlock_report.md"):
                        mn, mx = self._read_span_meta(p)
                        add_report(conn2, run_id=run_id, file_id=file_id, type_="deadlock", title=fn, md_path=p, xlsx_path=None, created_at=created_at, event_time_min=mn, event_time_max=mx)
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
        items = self.file_list.selectedItems()
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

        self._run_regen_for_file(run_id=run_id, file_id=file_id, run_out_dir=run_out_dir, file_out_dir=file_out_dir, source_xel=source_xel)


def main():
    app = QApplication([])
    w = MainWindow()

    # Auto-open last workspace if available
    last = WorkspaceSelectDialog.last_workspace()
    if last:
        w._set_workspace(last)

    w.show()
    app.exec()


if __name__ == "__main__":
    main()

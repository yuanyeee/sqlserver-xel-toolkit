from __future__ import annotations

import os
import subprocess
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

import markdown as mdlib

from .workspace import (
    RunRow,
    add_file,
    add_input,
    add_report,
    add_run,
    connect_db,
    get_report,
    init_db,
    list_files,
    list_reports,
    list_runs,
    search_reports,
)

from .time_range_dialog import TimeRangeDialog


@dataclass
class WorkspaceState:
    root: str
    db_path: str


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
            cmd = [os.path.join(self.repo_root, "run.sh"), *self.xel_paths, "-o", self.out_dir, "--slow-threshold", str(self.slow_threshold)]
            self.log.emit("$ " + " ".join(cmd))
            env = os.environ.copy()
            if self.workspace_root:
                env["XEL_TOOLKIT_WORKSPACE"] = self.workspace_root
            if self.ranges_path and os.path.exists(self.ranges_path):
                env["XEL_TOOLKIT_RANGES_JSON"] = self.ranges_path
            p = subprocess.Popen(cmd, cwd=self.repo_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            assert p.stdout
            for line in p.stdout:
                self.log.emit(line.rstrip("\n"))
            rc = p.wait()
            if rc == 0:
                self.finished_ok.emit(self.out_dir)
            else:
                self.finished_err.emit(f"run.sh failed with code {rc}")
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
        self.file_list = QListWidget()
        self.report_list = QListWidget()
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)

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

        tb = QToolBar("Main")
        self.addToolBar(tb)

        btn_open_ws = QPushButton("Open Workspace")
        btn_open_ws.clicked.connect(self.open_workspace)
        tb.addWidget(btn_open_ws)

        btn_new_run = QPushButton("New Run")
        btn_new_run.clicked.connect(self.new_run)
        tb.addWidget(btn_new_run)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.reload_lists)
        tb.addWidget(btn_refresh)

        btn_ranges = QPushButton("出力時間範囲…")
        btn_ranges.clicked.connect(self.open_time_ranges)
        tb.addWidget(btn_ranges)

        btn_it = QPushButton("Open IntegratedTool")
        btn_it.clicked.connect(self.open_integratedtool)
        tb.addWidget(btn_it)

        self.status = QLabel("")
        tb.addWidget(self.status)

    def open_workspace(self):
        d = QFileDialog.getExistingDirectory(self, "Choose workspace directory")
        if not d:
            return
        db_path = os.path.join(d, "workspace.db")
        init_db(db_path)
        self.ws = WorkspaceState(root=d, db_path=db_path)
        self.status.setText(os.path.basename(d))
        self.reload_lists()

    def reload_lists(self):
        if not self.ws:
            return
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

    def populate_reports(self, conn, run_id: Optional[int], file_id: Optional[int]):
        self.report_list.clear()
        for rep in list_reports(conn, run_id=run_id, file_id=file_id):
            title = rep.title or os.path.basename(rep.md_path or rep.xlsx_path or "")
            item = QListWidgetItem(f"[{rep.type}] {title}")
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

        if not rep or not rep.md_path or not os.path.exists(rep.md_path):
            self.preview.setPlainText("(no markdown)\n")
            return

        with open(rep.md_path, "r", encoding="utf-8") as f:
            text = f.read()
        html = mdlib.markdown(text, extensions=["tables", "fenced_code"])
        self.preview.setHtml(html)

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
                title = rep.title or os.path.basename(rep.md_path or rep.xlsx_path or "")
                item = QListWidgetItem(f"[{rep.type}] {title}")
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

    def open_integratedtool(self):
        it_dir = os.path.join(self.repo_root, "integratedtool")
        entry = os.path.join(it_dir, "unified_report_viewer.py")
        if not os.path.exists(entry):
            QMessageBox.warning(self, "IntegratedTool", f"Not found: {entry}\nDid you init submodules?")
            return

        # Run IntegratedTool with system python; user can manage its own venv separately.
        try:
            env = os.environ.copy()
            if self.ws:
                env["XEL_TOOLKIT_WORKSPACE"] = self.ws.root
                rp = self._ranges_path()
                if rp and os.path.exists(rp):
                    env["XEL_TOOLKIT_RANGES_JSON"] = rp
            subprocess.Popen(["python3", entry], cwd=it_dir, env=env)
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

        # Settings dialog (minimal): ask slow threshold and output dir
        slow = 3.0
        out_dir = os.path.join(self.ws.root, "runs", datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.makedirs(out_dir, exist_ok=True)

        # Run in background
        self.worker = RunWorker(
            self.repo_root,
            files,
            out_dir,
            slow,
            self.ws.root if self.ws else None,
            self._ranges_path() if self.ws else None,
        )
        self.worker.log.connect(self.append_log)
        self.worker.finished_ok.connect(lambda _: self.on_run_finished(files, out_dir, slow))
        self.worker.finished_err.connect(self.on_run_error)
        self.preview.setPlainText("Running... see log below\n")
        self.worker.start()

    def append_log(self, line: str):
        # append to preview temporarily
        cur = self.preview.toPlainText()
        self.preview.setPlainText(cur + line + "\n")

    def on_run_error(self, msg: str):
        QMessageBox.critical(self, "Run failed", msg)
        self.reload_lists()

    def on_run_finished(self, xel_files: List[str], out_dir: str, slow: float):
        # Scan outputs and insert into DB
        conn = connect_db(self.ws.db_path)
        try:
            run_id = add_run(conn, started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), out_dir=out_dir, slow_threshold_sec=slow)
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
                        add_report(conn, run_id=run_id, file_id=file_id, type_="deadlock", title=fn, md_path=p, xlsx_path=None, created_at=created_at)
                    elif fn.endswith("_slowquery_report.md"):
                        base = fn.replace("_slowquery_report.md", "")
                        xlsx = os.path.join(subdir, base + "_slowquery.xlsx")
                        add_report(conn, run_id=run_id, file_id=file_id, type_="slowquery", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at)
                    elif fn.endswith("_blocking_report.md"):
                        base = fn.replace("_blocking_report.md", "")
                        xlsx = os.path.join(subdir, base + "_blocking.xlsx")
                        add_report(conn, run_id=run_id, file_id=file_id, type_="blocking", title=fn, md_path=p, xlsx_path=(xlsx if os.path.exists(xlsx) else None), created_at=created_at)

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

        QMessageBox.information(self, "Done", f"Reports generated in: {out_dir}")
        self.reload_lists()


def main():
    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()

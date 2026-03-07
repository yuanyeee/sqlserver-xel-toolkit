"""aggregate_dialog.py

Native aggregation dialog — replaces the IntegratedTool submodule launcher.

AggregateWidget can be embedded directly in a tab or wrapped in AggregateDialog.
"""

from __future__ import annotations

import glob
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

from ._qt import (
    Qt,
    QThread,
    Signal,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QProgressBar,
    QTextEdit,
)
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


class AggregateWorker(QThread):
    log = Signal(str)
    finished_ok = Signal(dict)   # {type: xlsx_path}
    finished_err = Signal(str)

    def __init__(
        self,
        *,
        deadlock_jsonl: List[str],
        blocking_jsonl: List[str],
        slowquery_jsonl: List[str],
        out_dir: str,
        prefix: str,
        start_jst=None,
        end_jst=None,
        ranges=None,
        slow_threshold_sec: float = 3.0,
        split_by_date: bool = False,
    ):
        super().__init__()
        self.deadlock_jsonl = deadlock_jsonl
        self.blocking_jsonl = blocking_jsonl
        self.slowquery_jsonl = slowquery_jsonl
        self.out_dir = out_dir
        self.prefix = prefix
        self.start_jst = start_jst
        self.end_jst = end_jst
        self.ranges = ranges or []
        self.slow_threshold_sec = slow_threshold_sec
        self.split_by_date = split_by_date

    def run(self):
        try:
            # Add py/ to sys.path so toolkit is importable
            py_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if py_dir not in sys.path:
                sys.path.insert(0, py_dir)

            from toolkit.aggregation_processor import AggregationProcessor

            self.log.emit(f"集計開始: {self.prefix}")
            self.log.emit(f"  DeadLock JSONL: {len(self.deadlock_jsonl)} ファイル")
            self.log.emit(f"  Blocking JSONL: {len(self.blocking_jsonl)} ファイル")
            self.log.emit(f"  SlowQuery JSONL: {len(self.slowquery_jsonl)} ファイル")

            ap = AggregationProcessor()
            out = ap.aggregate_from_jsonl_files(
                deadlock_jsonl_files=self.deadlock_jsonl,
                blocking_jsonl_files=self.blocking_jsonl,
                slowquery_jsonl_files=self.slowquery_jsonl,
                out_dir=self.out_dir,
                prefix=self.prefix,
                start_jst=self.start_jst,
                end_jst=self.end_jst,
                ranges=self.ranges,
                slow_threshold_sec=self.slow_threshold_sec,
                split_by_date=self.split_by_date,
            )

            for typ, path in out.items():
                self.log.emit(f"  [{typ}] → {path}")

            self.finished_ok.emit(out)
        except Exception:
            import traceback
            self.finished_err.emit(traceback.format_exc())


class AggregateWidget(QWidget):
    """Embeddable aggregation widget (used in tab or wrapped in dialog)."""

    def __init__(
        self,
        workspace_root: str,
        db_path: str,
        ranges_path: Optional[str],
        parent=None,
        *,
        show_close_button: bool = False,
    ):
        super().__init__(parent)
        self.workspace_root = workspace_root
        self.db_path = db_path
        self.ranges_path = ranges_path
        self._worker: Optional[AggregateWorker] = None

        layout = QVBoxLayout(self)

        # ---- Options ----
        opt_group = QGroupBox("集計オプション")
        opt_layout = QVBoxLayout(opt_group)

        h_type = QHBoxLayout()
        self._chk_dead = QCheckBox("DeadLock")
        self._chk_dead.setChecked(True)
        self._chk_block = QCheckBox("Blocking")
        self._chk_block.setChecked(True)
        self._chk_slow = QCheckBox("SlowQuery")
        self._chk_slow.setChecked(True)
        h_type.addWidget(QLabel("種類:"))
        h_type.addWidget(self._chk_dead)
        h_type.addWidget(self._chk_block)
        h_type.addWidget(self._chk_slow)
        h_type.addStretch()
        opt_layout.addLayout(h_type)

        h_thr = QHBoxLayout()
        h_thr.addWidget(QLabel("SlowQuery閾値(秒):"))
        self._thr = QLineEdit("3.0")
        self._thr.setMaximumWidth(80)
        h_thr.addWidget(self._thr)
        h_thr.addStretch()
        opt_layout.addLayout(h_thr)

        h_range = QHBoxLayout()
        h_range.addWidget(QLabel("時間範囲フィルタ:"))
        self._chk_ranges = QCheckBox("ranges.json を適用")
        self._chk_ranges.setChecked(bool(ranges_path and os.path.exists(ranges_path)))
        h_range.addWidget(self._chk_ranges)
        h_range.addStretch()
        opt_layout.addLayout(h_range)

        h_split = QHBoxLayout()
        self._chk_split_date = QCheckBox("日付で分割（日付ごとにサブフォルダを作成）")
        self._chk_split_date.setChecked(False)
        h_split.addWidget(self._chk_split_date)
        h_split.addStretch()
        opt_layout.addLayout(h_split)

        h_out = QHBoxLayout()
        default_out = os.path.join(workspace_root, "aggregate", datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._out_edit = QLineEdit(default_out)
        btn_browse = QPushButton("…")
        btn_browse.setMaximumWidth(30)
        btn_browse.clicked.connect(self._browse_out)
        h_out.addWidget(QLabel("出力先:"))
        h_out.addWidget(self._out_edit)
        h_out.addWidget(btn_browse)
        opt_layout.addLayout(h_out)

        layout.addWidget(opt_group)

        # ---- Run list ----
        run_group = QGroupBox("対象 Run (空=全Run)")
        run_layout = QVBoxLayout(run_group)
        self._run_list = QListWidget()
        self._run_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        run_layout.addWidget(self._run_list)
        layout.addWidget(run_group)

        # ---- Log ----
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(160)
        layout.addWidget(QLabel("ログ:"))
        layout.addWidget(self._log)

        # _load_runs は self._log 生成後に呼ぶ（例外メッセージをログに出すため）
        self._load_runs()

        # ---- Progress ----
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ---- Buttons ----
        self._btn_run = QPushButton("集計実行")
        self._btn_run.clicked.connect(self._run_aggregate)
        self._btn_open = QPushButton("出力フォルダを開く")
        self._btn_open.clicked.connect(self._open_out)
        self._btn_open.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_run)
        btn_row.addWidget(self._btn_open)
        btn_row.addStretch()

        if show_close_button:
            self._btn_close = QPushButton("閉じる")
            btn_row.addWidget(self._btn_close)
            # caller must connect this to dialog.accept()

        layout.addLayout(btn_row)

        self._out_dir_result: Optional[str] = None

    def refresh_workspace(self, workspace_root: str, db_path: str, ranges_path: Optional[str]) -> None:
        """Update workspace context and reload the run list."""
        self.workspace_root = workspace_root
        self.db_path = db_path
        self.ranges_path = ranges_path
        default_out = os.path.join(workspace_root, "aggregate", datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._out_edit.setText(default_out)
        self._chk_ranges.setChecked(bool(ranges_path and os.path.exists(ranges_path)))
        self._run_list.clear()
        self._log.clear()
        self._btn_open.setEnabled(False)
        self._out_dir_result = None
        self._load_runs()

    def _load_runs(self):
        """Populate run list from workspace DB."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT id, started_at, out_dir FROM runs ORDER BY id DESC"
            ).fetchall()
            conn.close()
            for r in rows:
                item = QListWidgetItem(f"#{r[0]}  {r[1]}  {r[2]}")
                item.setData(Qt.UserRole, r[0])
                self._run_list.addItem(item)
        except Exception as e:
            self._log.append(f"Run一覧の読み込みに失敗: {e}")

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "出力先を選択", self._out_edit.text())
        if d:
            self._out_edit.setText(d)

    def _open_out(self):
        d = self._out_dir_result or self._out_edit.text()
        if d and os.path.isdir(d):
            QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _collect_jsonl(self, run_ids: List[int]) -> Dict[str, List[str]]:
        """Collect JSONL file paths for the given run IDs (or all runs if empty)."""
        import sqlite3

        try:
            conn = sqlite3.connect(self.db_path)
            if run_ids:
                placeholders = ",".join("?" * len(run_ids))
                rows = conn.execute(
                    f"SELECT out_dir FROM runs WHERE id IN ({placeholders})", tuple(run_ids)
                ).fetchall()
            else:
                rows = conn.execute("SELECT out_dir FROM runs").fetchall()
            conn.close()
        except Exception:
            rows = []

        dead: List[str] = []
        block: List[str] = []
        slow: List[str] = []

        for (out_dir,) in rows:
            if not out_dir or not os.path.isdir(out_dir):
                continue
            tmp = os.path.join(out_dir, "tmp")
            if not os.path.isdir(tmp):
                continue
            dead.extend(sorted(glob.glob(os.path.join(tmp, "*_deadlock.jsonl"))))
            block.extend(sorted(glob.glob(os.path.join(tmp, "*_blocking.jsonl"))))
            slow.extend(sorted(glob.glob(os.path.join(tmp, "*_slow.jsonl"))))

        return {"deadlock": dead, "blocking": block, "slowquery": slow}

    def _run_aggregate(self):
        sel = self._run_list.selectedItems()
        run_ids = [int(it.data(Qt.UserRole)) for it in sel] if sel else []

        jsonl = self._collect_jsonl(run_ids)

        if not any(jsonl.values()):
            QMessageBox.warning(self, "集計", "対象となる JSONL ファイルが見つかりません。\n先に XEL の読み込みを実行してください。")
            return

        out_dir = self._out_edit.text().strip() or os.path.join(
            self.workspace_root, "aggregate", datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        os.makedirs(out_dir, exist_ok=True)
        prefix = f"agg_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        ranges = []
        if self._chk_ranges.isChecked() and self.ranges_path and os.path.exists(self.ranges_path):
            try:
                py_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                if py_dir not in sys.path:
                    sys.path.insert(0, py_dir)
                from toolkit.ranges import load_ranges_json
                ranges = load_ranges_json(self.ranges_path)
            except Exception as e:
                self._log.append(f"ranges.json 読み込みエラー: {e}")

        try:
            slow_thr = float(self._thr.text() or "3.0")
        except ValueError:
            slow_thr = 3.0

        dead_files = jsonl["deadlock"] if self._chk_dead.isChecked() else []
        block_files = jsonl["blocking"] if self._chk_block.isChecked() else []
        slow_files = jsonl["slowquery"] if self._chk_slow.isChecked() else []

        self._log.append(f"=== 集計開始 ({datetime.now().strftime('%H:%M:%S')}) ===")
        self._log.append(f"出力先: {out_dir}")
        self._btn_run.setEnabled(False)
        self._progress.setVisible(True)

        split_date = self._chk_split_date.isChecked()

        self._worker = AggregateWorker(
            deadlock_jsonl=dead_files,
            blocking_jsonl=block_files,
            slowquery_jsonl=slow_files,
            out_dir=out_dir,
            prefix=prefix,
            ranges=ranges,
            slow_threshold_sec=slow_thr,
            split_by_date=split_date,
        )
        self._worker.log.connect(self._log.append)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.finished_err.connect(self._on_err)
        self._worker.start()

    def _on_done(self, out: dict):
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)
        self._out_dir_result = self._out_edit.text()
        self._btn_open.setEnabled(True)
        self._log.append(f"=== 完了 ({datetime.now().strftime('%H:%M:%S')}) ===")
        for typ, path in out.items():
            self._log.append(f"  [{typ}] {os.path.basename(path)}")
        if not out:
            self._log.append("  (対象イベントなし)")

    def _on_err(self, msg: str):
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)
        self._log.append(f"エラー:\n{msg}")
        # Show full traceback in a scrollable dialog instead of truncating
        from PySide6.QtWidgets import QDialog, QTextEdit, QVBoxLayout, QPushButton
        dlg = QDialog(self)
        dlg.setWindowTitle("集計エラー")
        dlg.resize(700, 400)
        layout = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(msg)
        layout.addWidget(te)
        btn = QPushButton("OK")
        btn.clicked.connect(dlg.accept)
        layout.addWidget(btn)
        dlg.exec()


class AggregateDialog(QDialog):
    """Thin dialog wrapper around AggregateWidget."""

    def __init__(self, workspace_root: str, db_path: str, ranges_path: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("統合集計・分析")
        self.resize(700, 620)

        layout = QVBoxLayout(self)
        self._widget = AggregateWidget(
            workspace_root, db_path, ranges_path, self, show_close_button=True
        )
        self._widget._btn_close.clicked.connect(self.accept)
        layout.addWidget(self._widget)

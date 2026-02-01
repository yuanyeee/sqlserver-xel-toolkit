from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QCheckBox,
)

from .delete_utils import is_within, trash_paths


@dataclass
class CleanupPlan:
    paths: List[str]


class CleanupInputMdDialog(QDialog):
    """Delete inputMD content by source-stem and date subfolders."""

    def __init__(self, workspace_root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("清理 inputMD")
        self.resize(720, 420)

        self.workspace_root = workspace_root
        self.inputmd_root = os.path.join(workspace_root, "inputMD")

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("inputMD の削除を行います（既定: ゴミ箱へ移動）。"))

        row = QHBoxLayout()
        row.addWidget(QLabel("対象:"))
        self.source_combo = QComboBox()
        row.addWidget(self.source_combo)
        self.refresh_btn = QPushButton("更新")
        self.refresh_btn.clicked.connect(self.load_sources)
        row.addWidget(self.refresh_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.delete_all_chk = QCheckBox("この source を丸ごと削除")
        self.delete_all_chk.stateChanged.connect(self.on_toggle_all)
        layout.addWidget(self.delete_all_chk)

        layout.addWidget(QLabel("削除する日付フォルダ（複数選択可）:"))
        self.dates_list = QListWidget()
        self.dates_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.dates_list)

        btns = QHBoxLayout()
        self.btn_delete = QPushButton("ゴミ箱へ移動")
        self.btn_delete.clicked.connect(self.do_delete)
        btns.addWidget(self.btn_delete)

        btns.addStretch(1)

        self.btn_close = QPushButton("閉じる")
        self.btn_close.clicked.connect(self.reject)
        btns.addWidget(self.btn_close)
        layout.addLayout(btns)

        self.source_combo.currentTextChanged.connect(self.load_dates)
        self.load_sources()

    def load_sources(self):
        self.source_combo.clear()
        if not os.path.isdir(self.inputmd_root):
            return
        sources = []
        for name in sorted(os.listdir(self.inputmd_root)):
            p = os.path.join(self.inputmd_root, name)
            if os.path.isdir(p):
                sources.append(name)
        self.source_combo.addItems(sources)
        if sources:
            self.load_dates(sources[0])

    def load_dates(self, source: str):
        self.dates_list.clear()
        self.delete_all_chk.setChecked(False)
        if not source:
            return
        root = os.path.join(self.inputmd_root, source)
        if not os.path.isdir(root):
            return
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                it = QListWidgetItem(name)
                it.setData(256, p)
                self.dates_list.addItem(it)

    def on_toggle_all(self):
        if self.delete_all_chk.isChecked():
            self.dates_list.clearSelection()
            self.dates_list.setEnabled(False)
        else:
            self.dates_list.setEnabled(True)

    def build_plan(self) -> Optional[CleanupPlan]:
        source = self.source_combo.currentText().strip()
        if not source:
            return None

        source_dir = os.path.join(self.inputmd_root, source)
        if not os.path.isdir(source_dir):
            return None

        paths: List[str] = []
        if self.delete_all_chk.isChecked():
            paths.append(source_dir)
        else:
            for idx in self.dates_list.selectedIndexes():
                item = self.dates_list.item(idx.row())
                p = item.data(256)
                if p:
                    paths.append(p)

        # safety
        for p in paths:
            if not is_within(self.workspace_root, p):
                QMessageBox.critical(self, "安全检查", f"拒绝删除 workspace 外路径: {p}")
                return None

        return CleanupPlan(paths=paths)

    def do_delete(self):
        plan = self.build_plan()
        if not plan or not plan.paths:
            QMessageBox.information(self, "提示", "请选择要删除的对象")
            return

        msg = "以下路径将移动到回收站:\n\n" + "\n".join(plan.paths)
        if QMessageBox.question(self, "确认", msg) != QMessageBox.StandardButton.Yes:
            return

        trashed = trash_paths(plan.paths)
        QMessageBox.information(self, "完成", f"已移动到回收站: {len(trashed)} 项")
        self.load_sources()

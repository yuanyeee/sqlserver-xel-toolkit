from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List

from PyQt5.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QLineEdit,
)


@dataclass
class RangeItem:
    start: str  # "YYYY-mm-dd HH:MM" (JST)
    end: str


def load_ranges(path: str) -> List[RangeItem]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f) or {}
        items = obj.get("ranges", []) if isinstance(obj, dict) else []
        out: List[RangeItem] = []
        for it in items:
            if isinstance(it, dict) and it.get("start") and it.get("end"):
                out.append(RangeItem(start=str(it["start"]).strip(), end=str(it["end"]).strip()))
        out.sort(key=lambda x: x.start)
        return out
    except Exception:
        return []


def save_ranges(path: str, items: List[RangeItem]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ranges": [{"start": i.start, "end": i.end} for i in items]}, f, ensure_ascii=False, indent=2)


class TimeRangeDialog(QDialog):
    def __init__(self, ranges_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("出力時間範囲")
        self.resize(560, 380)

        self.ranges_path = ranges_path
        self.items: List[RangeItem] = load_ranges(ranges_path)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("複数の時間帯を追加できます（JST / 分単位）。\n例: 2026-01-05 08:00 〜 2026-01-05 17:00"))

        self.list = QListWidget()
        layout.addWidget(self.list)

        form = QFormLayout()
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("YYYY-mm-dd HH:MM")
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("YYYY-mm-dd HH:MM")
        form.addRow("開始", self.start_edit)
        form.addRow("終了", self.end_edit)
        layout.addLayout(form)

        quick = QHBoxLayout()
        quick.addWidget(QLabel("テンプレ:"))

        btn1 = QPushButton("08:00-17:00")
        btn1.clicked.connect(lambda: self._apply_template("08:00", "17:00"))
        quick.addWidget(btn1)

        btn2 = QPushButton("07:00-21:00")
        btn2.clicked.connect(lambda: self._apply_template("07:00", "21:00"))
        quick.addWidget(btn2)

        quick.addStretch(1)
        layout.addLayout(quick)

        btns = QHBoxLayout()
        self.btn_add = QPushButton("追加")
        self.btn_add.clicked.connect(self.add_item)
        btns.addWidget(self.btn_add)

        self.btn_del = QPushButton("削除")
        self.btn_del.clicked.connect(self.delete_selected)
        btns.addWidget(self.btn_del)

        self.btn_clear = QPushButton("全クリア")
        self.btn_clear.clicked.connect(self.clear_all)
        btns.addWidget(self.btn_clear)

        btns.addStretch(1)

        self.btn_ok = QPushButton("OK")
        self.btn_ok.clicked.connect(self.accept)
        btns.addWidget(self.btn_ok)

        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)

        layout.addLayout(btns)

        self.refresh()

    def _apply_template(self, start_hm: str, end_hm: str):
        # If user already typed a date, keep it.
        s = self.start_edit.text().strip()
        e = self.end_edit.text().strip()
        if len(s) >= 10:
            date = s[:10]
            self.start_edit.setText(f"{date} {start_hm}")
        if len(e) >= 10:
            date = e[:10]
            self.end_edit.setText(f"{date} {end_hm}")
        # If empty, just fill time portion (user will add date)
        if not self.start_edit.text().strip():
            self.start_edit.setText(f"YYYY-mm-dd {start_hm}")
        if not self.end_edit.text().strip():
            self.end_edit.setText(f"YYYY-mm-dd {end_hm}")

    def refresh(self):
        self.list.clear()
        for it in self.items:
            self.list.addItem(QListWidgetItem(f"{it.start} 〜 {it.end}"))

    def add_item(self):
        s = self.start_edit.text().strip()
        e = self.end_edit.text().strip()
        if not s or not e:
            QMessageBox.warning(self, "入力", "開始/終了を入力してください")
            return
        if "YYYY" in s or "YYYY" in e:
            QMessageBox.warning(self, "入力", "日付を YYYY-mm-dd の形で入力してください")
            return
        if s >= e:
            QMessageBox.warning(self, "入力", "開始は終了より前にしてください")
            return
        self.items.append(RangeItem(start=s, end=e))
        self.items.sort(key=lambda x: x.start)
        self.start_edit.clear()
        self.end_edit.clear()
        self.refresh()

    def delete_selected(self):
        idx = self.list.currentRow()
        if idx < 0:
            return
        self.items.pop(idx)
        self.refresh()

    def clear_all(self):
        self.items = []
        self.refresh()

    def accept(self):
        try:
            save_ranges(self.ranges_path, self.items)
        except Exception as e:
            QMessageBox.critical(self, "保存失敗", str(e))
            return
        super().accept()

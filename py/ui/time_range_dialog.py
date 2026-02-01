from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import List

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
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
        self.resize(860, 520)

        self.ranges_path = ranges_path
        self.items: List[RangeItem] = load_ranges(ranges_path)

        self.selected_dates: List[QDate] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("複数の時間帯を追加できます（JST / 分単位）。\n日付はカレンダーから複数選択できます。"))

        top = QHBoxLayout()

        # Left: calendar + selected date list
        left = QVBoxLayout()
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        left.addWidget(self.calendar)

        left.addWidget(QLabel("選択した日付"))
        self.date_list = QListWidget()
        self.date_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        left.addWidget(self.date_list)

        date_btns = QHBoxLayout()
        btn_add_date = QPushButton("追加")
        btn_add_date.clicked.connect(self.add_selected_date)
        date_btns.addWidget(btn_add_date)

        btn_del_date = QPushButton("削除")
        btn_del_date.clicked.connect(self.remove_selected_dates)
        date_btns.addWidget(btn_del_date)

        btn_clear_dates = QPushButton("日付クリア")
        btn_clear_dates.clicked.connect(self.clear_dates)
        date_btns.addWidget(btn_clear_dates)

        date_btns.addStretch(1)
        left.addLayout(date_btns)

        top.addLayout(left, 3)

        # Right: ranges list + manual editor
        right = QVBoxLayout()
        right.addWidget(QLabel("時間帯一覧"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        right.addWidget(self.list)

        top.addLayout(right, 4)

        layout.addLayout(top)

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
        btn1.clicked.connect(lambda: self.apply_template_to_dates("08:00", "17:00"))
        quick.addWidget(btn1)

        btn2 = QPushButton("07:00-21:00")
        btn2.clicked.connect(lambda: self.apply_template_to_dates("07:00", "21:00"))
        quick.addWidget(btn2)

        btn_manual = QPushButton("入力で追加")
        btn_manual.clicked.connect(self.add_item)
        quick.addWidget(btn_manual)

        quick.addStretch(1)
        layout.addLayout(quick)

        btns = QHBoxLayout()
        self.btn_del = QPushButton("選択削除")
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

    def add_selected_date(self):
        d = self.calendar.selectedDate()
        if d not in self.selected_dates:
            self.selected_dates.append(d)
            self.selected_dates.sort(key=lambda x: x.toJulianDay())
            self.refresh_dates()

    def refresh_dates(self):
        self.date_list.clear()
        for d in self.selected_dates:
            self.date_list.addItem(QListWidgetItem(d.toString("yyyy-MM-dd")))

    def remove_selected_dates(self):
        rows = sorted({i.row() for i in self.date_list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if QMessageBox.question(self, "確認", f"{len(rows)} 件の日付を削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        for r in rows:
            if 0 <= r < len(self.selected_dates):
                self.selected_dates.pop(r)
        self.refresh_dates()

    def clear_dates(self):
        if not self.selected_dates:
            return
        if QMessageBox.question(self, "確認", "選択した日付をすべてクリアしますか？") != QMessageBox.StandardButton.Yes:
            return
        self.selected_dates = []
        self.refresh_dates()

    def apply_template_to_dates(self, start_hm: str, end_hm: str):
        if not self.selected_dates:
            QMessageBox.warning(self, "日付", "カレンダーで日付を選択し、追加してください")
            return
        for d in self.selected_dates:
            date = d.toString("yyyy-MM-dd")
            s = f"{date} {start_hm}"
            e = f"{date} {end_hm}"
            if s >= e:
                continue
            self.items.append(RangeItem(start=s, end=e))
        self.items.sort(key=lambda x: x.start)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for it in self.items:
            self.list.addItem(QListWidgetItem(f"{it.start} 〜 {it.end}"))
        self.refresh_dates()

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
        rows = sorted({i.row() for i in self.list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if QMessageBox.question(self, "確認", f"{len(rows)} 件を削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        for r in rows:
            if 0 <= r < len(self.items):
                self.items.pop(r)
        self.refresh()

    def clear_all(self):
        if not self.items:
            return
        if QMessageBox.question(self, "確認", "すべての時間帯を削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        self.items = []
        self.refresh()

    def accept(self):
        try:
            save_ranges(self.ranges_path, self.items)
        except Exception as e:
            QMessageBox.critical(self, "保存失敗", str(e))
            return
        super().accept()

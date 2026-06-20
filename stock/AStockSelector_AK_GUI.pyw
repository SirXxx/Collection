# -*- coding: utf-8 -*-
"""
AStockSelector_AK_GUI.pyw

akshare A 股筛选框架 — 多标签塔桌版

[筛选器]  可视化输入条件、实时行情+财报筛选、导出 CSV/Excel
[自选列表] 将筛选结果加入自选（SQLite 持久化）
[次日跟踪] 手动/定时拍取自选股票第二天实时行情
[统计分析] 展示胜率趋势和条件相关性图表
"""

import json
import os
import sys
import threading
import traceback
import queue
from types import SimpleNamespace
from datetime import datetime

import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import AStockSelector_AK as core
import db
import watchlist_manager as wm
import tracker
import scheduler
import stats

# matplotlib 可选导入
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False
    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "AStockSelector_AK_GUI_config.json")

SORT_OPTIONS = [
    "price", "pct_chg", "volume", "volume_ratio", "turnover",
    "market_cap", "circ_market_cap", "pe", "pb", "eps", "roe",
    "revenue", "net_profit", "revenue_yoy", "profit_yoy",
    "ma5", "tail_30min_pct",
]

DATA_SOURCE_OPTIONS = core.DATA_SOURCE_OPTIONS  # ["auto", "eastmoney", "sina"]

CHOICE_OPTIONS = {
    "sort_by":     SORT_OPTIONS,
    "data_source": DATA_SOURCE_OPTIONS,
}

RESULT_COLUMNS = [
    ("code",          "代码",       80),
    ("name",          "名称",      110),
    ("price",         "现价",       80),
    ("pct_chg",       "涨跌幅%",     90),
    ("today_high",    "今日最高",     80),
    ("today_low",     "今日最低",     80),
    ("low_1",         "最低_1",      75),
    ("low_2",         "最低_2",      75),
    ("low_3",         "最低_3",      75),
    ("low_4",         "最低_4",      75),
    ("low_5",         "最低_5",      75),
    ("ma5",           "MA5",        80),
    ("ma10",          "MA10",       80),
    ("ma20",          "MA20",       80),
    ("ma5_trend",     "MA5趋势",    80),
    ("tail_30min_pct","尾盘30min%",  90),
    ("volume",        "成交量",     100),
    ("volume_ratio",  "量比",       80),
    ("turnover",      "换手%",      80),
    ("market_cap",    "总市值(亿)",  100),
    ("pe",            "PE",         80),
    ("pb",            "PB",         75),
    ("eps",           "EPS",        80),
    ("roe",           "ROE%",       80),
    ("revenue",       "营收(亿)",   100),
    ("net_profit",    "净利润(亿)",  100),
    ("revenue_yoy",   "营收同比%",    100),
    ("profit_yoy",    "利润同比%",    100),
    ("industry",      "行业",      120),
    ("notice_date",   "公告日",     100),
    ("report_date",   "报告期",      90),
]

FIELD_DEFS = [
    ("top_n",            "输出数量 Top N",               "int",    20),
    ("sort_by",           "排序字段",                   "choice", "pct_chg"),
    ("ascending",         "升序排序",                   "bool",   False),
    ("add_codes",         "主动添加股票(逗号分隔)",         "str",    ""),
    ("include_bj",        "包含北交所",                 "bool",   False),
    ("data_source",       "数据来源",                   "choice", "auto"),
    ("export_name",       "导出文件名前缀(可空)",      "str",    ""),
    ("spot_retries",      "实时行情重试次数",           "int",    3),
    ("spot_retry_sleep",  "实时行情重试间隔秒",           "int",    3),

    ("min_price",         "最低价格",                   "float",  ""),
    ("max_price",         "最高价格",                   "float",  ""),
    ("min_pct_chg",       "最小涨跌幅%",               "float",  ""),
    ("max_pct_chg",       "最大涨跌幅%",               "float",  ""),
    ("min_volume",        "最小成交量",               "float",  ""),
    ("min_volume_ratio",  "最小量比",                 "float",  ""),
    ("max_volume_ratio",  "最大量比",                 "float",  ""),
    ("min_turnover",      "最小换手率%",               "float",  ""),
    ("max_turnover",      "最大换手率%",               "float",  ""),
    ("min_market_cap",    "最小市值(亿)",              "float",  ""),
    ("max_market_cap",    "最大市值(亿)",              "float",  ""),
    ("min_pe",            "最小 PE",                    "float",  ""),
    ("max_pe",            "最大 PE",                    "float",  ""),
    ("max_pb",            "最大 PB",                    "float",  ""),
    ("min_roe",           "最小 ROE%",                  "float",  ""),
    ("low_rising",        "近3天最低价递增",             "bool",   False),
    ("fetch_indicators",  "获取技术指标(MA、尾盘等)",   "bool",   False),
    ("fetch_roe",         "同时获取 ROE",               "bool",   False),
    ("price_above_ma5",   "价格在 MA5 上方",             "bool",   False),
    ("price_above_ma10",  "价格在 MA10 上方",            "bool",   False),
    ("price_above_ma20",  "价格在 MA20 上方",            "bool",   False),
    ("ma5_trend_up",      "MA5 趋势向上",              "bool",   False),
    ("tail_30min_positive","尾盘30min趋势为正",          "bool",   False),
]

RANGE_LABELS = {
    ("min_pct_chg",       "max_pct_chg"):      "涨幅范围%",
    ("min_volume_ratio",  "max_volume_ratio"):  "量比范围",
    ("min_turnover",      "max_turnover"):      "换手率范围%",
    ("min_market_cap",    "max_market_cap"):    "市值范围(亿)",
}

SECTION_LAYOUT = [
    ("基础参数", [
        "top_n", "sort_by", "ascending", "add_codes",
        "include_bj", "data_source", "export_name", "spot_retries", "spot_retry_sleep"
    ]),
    ("行情条件", [
        "min_price", "max_price",
        ("min_pct_chg", "max_pct_chg"),
        "min_volume",
        ("min_volume_ratio", "max_volume_ratio"),
        ("min_turnover", "max_turnover"),
        ("min_market_cap", "max_market_cap"),
        "min_pe", "max_pe", "max_pb", "min_roe", "low_rising"
    ]),
    ("技术指标", [
        "fetch_indicators", "fetch_roe",
        "price_above_ma5", "price_above_ma10", "price_above_ma20",
        "ma5_trend_up", "tail_30min_positive",
    ]),
]

LABEL_MAP = {k: label for k, label, _t, _d in FIELD_DEFS}
TYPE_MAP = {k: t for k, _label, t, _d in FIELD_DEFS}
DEFAULTS = {k: default for k, _label, _t, default in FIELD_DEFS}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = dict(DEFAULTS)
        if isinstance(data, dict):
            out.update(data)
        return out
    except Exception:
        return dict(DEFAULTS)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def format_value(v, digits=2, default="-"):
    if v is None or v == "":
        return default
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return str(v)


def clean_filename_prefix(text):
    text = (text or "").strip()
    bad = '\\/:*?"<>|'
    for ch in bad:
        text = text.replace(ch, "_")
    return text.strip(" ._")


def export_dataframe(df, prefix, finance_only, used_date):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = clean_filename_prefix(prefix)
    if not prefix:
        prefix = "a_stock_finance_only_gui" if finance_only else "a_stock_selected_ak_gui"
    if used_date:
        prefix = f"{prefix}_{used_date}"
    csv_path = os.path.join(BASE_DIR, f"{prefix}_{timestamp}.csv")
    xlsx_path = os.path.join(BASE_DIR, f"{prefix}_{timestamp}.xlsx")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    try:
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Results")
            ws = writer.sheets["Results"]
            ws.freeze_panes = "A2"
            for col_cells in ws.columns:
                max_len = 0
                for cell in col_cells:
                    try:
                        value = "" if cell.value is None else str(cell.value)
                    except Exception:
                        value = ""
                    max_len = max(max_len, len(value))
                ws.column_dimensions[col_cells[0].column_letter].width = min(max(max_len + 2, 10), 24)
    except Exception:
        xlsx_path = None

    return csv_path, xlsx_path


class SelectorApp:
    BG      = "#F4F7FB"
    PANEL   = "#FFFFFF"
    BORDER  = "#D9E2EF"
    TITLE   = "#1F3B5B"
    TEXT    = "#243447"
    SUBTEXT = "#5B6B7A"
    ACCENT  = "#3B82F6"
    SUCCESS = "#16A34A"
    WARN    = "#B45309"
    ERROR   = "#DC2626"
    UP      = "#16A34A"
    DOWN    = "#DC2626"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("A股筛选器 v2")
        self.root.geometry("1420x880+60+40")
        self.root.minsize(1200, 700)
        self.root.configure(bg=self.BG)

        self.config_data = load_config()
        self.vars = {}
        self.result_rows = []
        self.result_df = pd.DataFrame()
        self.last_used_date = ""
        self.last_csv_path = ""
        self.last_xlsx_path = ""
        self.running = False
        self.ui_queue = queue.Queue()

        # 次日跟踪状态
        self.track_date_var = tk.StringVar()
        self.scheduler_on = tk.BooleanVar(value=False)

        # 统计分析状态
        self.stats_threshold_var = tk.StringVar(value="1.0")
        self.stats_days_var = tk.StringVar(value="30")
        self.stats_label_var = tk.StringVar(value="close")

        self._build_style()
        self._build_ui()
        self._load_to_form(self.config_data)
        self.root.after(200, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        scheduler.stop()
        self.root.destroy()

    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_ui(self):
        top = tk.Frame(self.root, bg=self.BG)
        top.pack(fill="x", padx=14, pady=(12, 6))

        title = tk.Label(top, text="A 股筛选系统 v2", bg=self.BG, fg=self.TITLE,
                         font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor="w")

        subtitle = tk.Label(top, text="筛选器 · 自选列表 · 次日跟踪 · 策略统计",
                            bg=self.BG, fg=self.SUBTEXT, font=("Microsoft YaHei UI", 9))
        subtitle.pack(anchor="w", pady=(4, 0))

        status_bar = tk.Frame(self.root, bg=self.BG)
        status_bar.pack(fill="x", padx=14, pady=(2, 2))
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(status_bar, textvariable=self.status_var, bg=self.BG, fg=self.SUBTEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self._nb = nb

        tab1 = tk.Frame(nb, bg=self.BG)
        tab2 = tk.Frame(nb, bg=self.BG)
        tab3 = tk.Frame(nb, bg=self.BG)
        tab4 = tk.Frame(nb, bg=self.BG)
        nb.add(tab1, text="  筛选器  ")
        nb.add(tab2, text="  自选列表  ")
        nb.add(tab3, text="  次日跟踪  ")
        nb.add(tab4, text="  统计分析  ")
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_screener_tab(tab1)
        self._build_watchlist_tab(tab2)
        self._build_tracking_tab(tab3)
        self._build_stats_tab(tab4)

    def _on_tab_changed(self, event):
        idx = self._nb.index("current")
        if idx == 1:
            self._refresh_watchlist()
        elif idx == 2:
            self._refresh_tracking_dates()

    def _build_screener_tab(self, parent):
        toolbar = tk.Frame(parent, bg=self.BG)
        toolbar.pack(fill="x", padx=8, pady=(8, 4))

        self.run_btn = tk.Button(toolbar, text="▶ 运行筛选", command=self.run_selection,
                                 bg=self.ACCENT, fg="white", relief="flat", padx=16, pady=6,
                                 font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2")
        self.run_btn.pack(side="left")

        tk.Button(toolbar, text="保存参数", command=self.save_current_config,
                  bg="#E5EEF9", fg=self.TEXT, relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=6)
        tk.Button(toolbar, text="重载参数", command=self.reload_config,
                  bg="#E5EEF9", fg=self.TEXT, relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left")
        tk.Button(toolbar, text="重置默认", command=self.reset_defaults,
                  bg="#F3F4F6", fg=self.TEXT, relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=6)
        tk.Button(toolbar, text="导出结果", command=self.export_current_result,
                  bg="#DCFCE7", fg="#166534", relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left")

        self.add_wl_btn = tk.Button(toolbar, text="★ 全部加入自选",
                                    command=self._add_all_to_watchlist,
                                    bg="#FEF9C3", fg="#713F12", relief="flat", padx=12, pady=6,
                                    font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2",
                                    state="disabled")
        self.add_wl_btn.pack(side="left", padx=6)

        body = tk.PanedWindow(parent, orient="horizontal", sashrelief="flat", sashwidth=6,
                              bg=self.BG, bd=0)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        left_wrap  = tk.Frame(body, bg=self.PANEL, bd=1, relief="solid", highlightthickness=0)
        right_wrap = tk.Frame(body, bg=self.PANEL, bd=1, relief="solid", highlightthickness=0)
        body.add(left_wrap,  minsize=360)
        body.add(right_wrap, minsize=740)

        self._build_form_panel(left_wrap)
        self._build_result_panel(right_wrap)

    def _build_form_panel(self, parent):
        header = tk.Frame(parent, bg=self.PANEL)
        header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(header, text="筛选参数", bg=self.PANEL, fg=self.TITLE,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")

        canvas = tk.Canvas(parent, bg=self.PANEL, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.PANEL)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
        scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=(0, 10))

        row = 0
        for section_title, keys in SECTION_LAYOUT:
            sec = tk.LabelFrame(inner, text=section_title, bg=self.PANEL, fg=self.TITLE,
                                font=("Microsoft YaHei UI", 10, "bold"), padx=10, pady=8)
            sec.grid(row=row, column=0, sticky="ew", pady=6, padx=(0, 8))
            sec.columnconfigure(1, weight=1)
            row += 1

            sec_row = 0
            for key in keys:
                # ── 范围型字段（tuple）：同行显示 min ~ max 两个输入框 ──
                if isinstance(key, tuple):
                    min_key, max_key = key
                    label_text = RANGE_LABELS.get(key, f"{LABEL_MAP.get(min_key, min_key)} ~ {LABEL_MAP.get(max_key, max_key)}")
                    tk.Label(sec, text=label_text, bg=self.PANEL, fg=self.TEXT,
                             anchor="w", font=("Microsoft YaHei UI", 9)).grid(row=sec_row, column=0, sticky="w", pady=4)

                    rf = tk.Frame(sec, bg=self.PANEL)
                    rf.grid(row=sec_row, column=1, sticky="ew", pady=4)
                    rf.columnconfigure(0, weight=1)
                    rf.columnconfigure(2, weight=1)

                    min_var = tk.StringVar(value="" if DEFAULTS.get(min_key) is None else str(DEFAULTS.get(min_key, "")))
                    max_var = tk.StringVar(value="" if DEFAULTS.get(max_key) is None else str(DEFAULTS.get(max_key, "")))
                    tk.Entry(rf, textvariable=min_var, relief="solid", bd=1,
                             font=("Consolas", 10), width=8).grid(row=0, column=0, sticky="ew")
                    tk.Label(rf, text=" ~ ", bg=self.PANEL, fg=self.SUBTEXT,
                             font=("Microsoft YaHei UI", 9)).grid(row=0, column=1)
                    tk.Entry(rf, textvariable=max_var, relief="solid", bd=1,
                             font=("Consolas", 10), width=8).grid(row=0, column=2, sticky="ew")

                    self.vars[min_key] = min_var
                    self.vars[max_key] = max_var
                    sec_row += 1
                    continue

                # ── 普通字段 ──
                label_text = LABEL_MAP[key]
                typ = TYPE_MAP[key]
                tk.Label(sec, text=label_text, bg=self.PANEL, fg=self.TEXT,
                         anchor="w", font=("Microsoft YaHei UI", 9)).grid(row=sec_row, column=0, sticky="w", pady=4)

                if typ == "bool":
                    var = tk.BooleanVar(value=bool(DEFAULTS[key]))
                    widget = tk.Checkbutton(sec, variable=var, bg=self.PANEL, activebackground=self.PANEL)
                    widget.grid(row=sec_row, column=1, sticky="w", pady=4)
                    self.vars[key] = var
                elif typ == "choice":
                    var = tk.StringVar(value=str(DEFAULTS[key]))
                    widget = ttk.Combobox(sec, textvariable=var,
                                         values=CHOICE_OPTIONS.get(key, SORT_OPTIONS),
                                         state="readonly")
                    widget.grid(row=sec_row, column=1, sticky="ew", pady=4)
                    self.vars[key] = var
                else:
                    var = tk.StringVar(value="" if DEFAULTS[key] is None else str(DEFAULTS[key]))
                    widget = tk.Entry(sec, textvariable=var, relief="solid", bd=1,
                                      font=("Consolas", 10))
                    widget.grid(row=sec_row, column=1, sticky="ew", pady=4)
                    self.vars[key] = var

                sec_row += 1

    def _build_result_panel(self, parent):
        header = tk.Frame(parent, bg=self.PANEL)
        header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(header, text="筛选结果", bg=self.PANEL, fg=self.TITLE,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")

        self.result_summary_var = tk.StringVar(value="尚未运行")
        tk.Label(header, textvariable=self.result_summary_var, bg=self.PANEL, fg=self.SUBTEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        table_wrap = tk.Frame(parent, bg=self.PANEL)
        table_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        columns = [x[0] for x in RESULT_COLUMNS]
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings")
        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col, title, width in RESULT_COLUMNS:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=70, anchor="center")

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)

        log_title = tk.Frame(parent, bg=self.PANEL)
        log_title.pack(fill="x", padx=12, pady=(4, 4))
        tk.Label(log_title, text="运行日志", bg=self.PANEL, fg=self.TITLE,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")

        self.log_text = tk.Text(parent, height=10, wrap="word", bg="#0F172A", fg="#E2E8F0",
                                insertbackground="#E2E8F0", relief="flat", font=("Consolas", 10))
        self.log_text.pack(fill="x", padx=12, pady=(0, 12))
        self.log_text.insert("end", "就绪。\n")
        self.log_text.configure(state="disabled")

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _queue_log(self, text):
        self.ui_queue.put(("log", text))

    def _load_to_form(self, data):
        for key, var in self.vars.items():
            value = data.get(key, DEFAULTS.get(key))
            typ = TYPE_MAP[key]
            if typ == "bool":
                var.set(bool(value))
            else:
                var.set("" if value is None else str(value))

    def _collect_form_data(self):
        out = {}
        for key, var in self.vars.items():
            typ = TYPE_MAP[key]
            if typ == "bool":
                out[key] = bool(var.get())
            else:
                out[key] = var.get().strip()
        return out

    def _to_args(self):
        data = self._collect_form_data()

        def parse_int(name, default=None):
            text = data.get(name, "")
            if text == "":
                return default
            try:
                return int(text)
            except Exception:
                raise ValueError(f"{LABEL_MAP[name]} 需要整数")

        def parse_float(name, default=None):
            text = data.get(name, "")
            if text == "":
                return default
            try:
                return float(text)
            except Exception:
                raise ValueError(f"{LABEL_MAP[name]} 需要数字")

        top_n = parse_int("top_n", 20)
        if top_n is None or top_n <= 0:
            raise ValueError("输出数量 Top N 必须大于 0")

        args = SimpleNamespace(
            top_n=top_n,
            sort_by=data.get("sort_by") or "pct_chg",
            ascending=bool(data.get("ascending")),
            csv="",
            add_codes=data.get("add_codes", ""),
            include_bj=bool(data.get("include_bj")),
            min_price=parse_float("min_price"),
            max_price=parse_float("max_price"),
            min_pct_chg=parse_float("min_pct_chg"),
            max_pct_chg=parse_float("max_pct_chg"),
            min_volume=parse_float("min_volume"),
            min_volume_ratio=parse_float("min_volume_ratio"),
            max_volume_ratio=parse_float("max_volume_ratio"),
            min_turnover=parse_float("min_turnover"),
            max_turnover=parse_float("max_turnover"),
            min_market_cap=parse_float("min_market_cap"),
            max_market_cap=parse_float("max_market_cap"),
            min_pe=parse_float("min_pe"),
            max_pe=parse_float("max_pe"),
            max_pb=parse_float("max_pb"),
            min_roe=parse_float("min_roe"),
            spot_retries=parse_int("spot_retries", 3),
            spot_retry_sleep=parse_int("spot_retry_sleep", 3),
            low_rising=bool(data.get("low_rising")),
            fetch_indicators=bool(data.get("fetch_indicators")),
            fetch_roe=bool(data.get("fetch_roe")),
            price_above_ma5=bool(data.get("price_above_ma5")),
            price_above_ma10=bool(data.get("price_above_ma10")),
            price_above_ma20=bool(data.get("price_above_ma20")),
            ma5_trend_up=bool(data.get("ma5_trend_up")),
            tail_30min_positive=bool(data.get("tail_30min_positive")),
            data_source=data.get("data_source", "auto") or "auto",
        )

        if args.sort_by not in SORT_OPTIONS:
            raise ValueError("排序字段不合法")
        return args

    def save_current_config(self):
        data = self._collect_form_data()
        save_config(data)
        self.config_data = data
        self.status_var.set("参数已保存")
        self._append_log("参数已保存到 AStockSelector_AK_GUI_config.json")

    def reload_config(self):
        self.config_data = load_config()
        self._load_to_form(self.config_data)
        self.status_var.set("已重载参数")
        self._append_log("已从配置文件重载参数")

    def reset_defaults(self):
        self._load_to_form(DEFAULTS)
        self.status_var.set("已恢复默认值")
        self._append_log("表单已恢复默认值（尚未保存）")

    def run_selection(self):
        if self.running:
            messagebox.showinfo("提示", "当前已有任务在运行，请稍候。")
            return
        try:
            args = self._to_args()
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return

        self.save_current_config()
        self.running = True
        self.run_btn.configure(state="disabled")
        self.status_var.set("运行中...")
        self._append_log("=" * 70)
        self._append_log("开始运行筛选...")

        export_prefix = self._collect_form_data().get("export_name", "")
        t = threading.Thread(target=self._worker_run, args=(args, export_prefix), daemon=True)
        t.start()

    def _worker_run(self, args, export_prefix):
        try:
            self.ui_queue.put(("status", "正在拉取与筛选数据..."))
            self._queue_log(f"模式：实时行情，数据来源：{args.data_source}")
            self._queue_log(f"排序字段：{args.sort_by}，{'升序' if args.ascending else '降序'}")

            self._queue_log("开始拉取实时行情...")
            spot_df = core.fetch_spot_data(
                retries=args.spot_retries,
                sleep_sec=args.spot_retry_sleep,
                include_bj=args.include_bj,
                data_source=args.data_source,
            )
            self._queue_log(
                f"实时行情完成，共 {len(spot_df)} 条；来源：{spot_df.attrs.get('spot_source', '-')}"
            )
            rows = spot_df.to_dict(orient="records")

            before_count = len(rows)
            rows = [x for x in rows if core.pass_filters(x, args)]
            rows.sort(key=lambda x: core.sort_value(x, args.sort_by), reverse=not args.ascending)
            rows = rows[:args.top_n]
            self._queue_log(f"筛选前 {before_count} 条，筛选后 {len(rows)} 条，取前 {args.top_n} 条")

            # 主动添加股票（跳过筛选）
            add_codes_str = (args.add_codes or "").strip()
            if add_codes_str:
                add_codes = [c.strip().zfill(6) for c in add_codes_str.split(",") if c.strip()]
                existing = {r["code"] for r in rows}
                added_rows = spot_df[spot_df["code"].isin(add_codes)].to_dict(orient="records")
                added_count = 0
                for r in added_rows:
                    if r["code"] not in existing:
                        r["_manual"] = True
                        rows.append(r)
                        existing.add(r["code"])
                        added_count += 1
                not_found = [c for c in add_codes if c not in {r["code"] for r in rows}]
                self._queue_log(f"主动添加 {added_count} 只股票" +
                                (f"；未找到：{not_found}" if not_found else ""))

            used_date = ""

            if getattr(args, "low_rising", False):
                self._queue_log(f"正在补充 {len(rows)} 只股票的近5日最低价（并发请求，请稍候）...")
                rows = core.enrich_with_recent_lows(rows, days=5, data_source=args.data_source)
                before_low = len(rows)
                rows = [r for r in rows if core.pass_low_rising_filter(r)]
                api_failed = all(
                    not any(r.get(f"low_{i}") is not None for i in range(1, 6))
                    for r in rows
                ) if rows else False
                if api_failed:
                    self._queue_log("⚠ K线接口不可用，近3天最低价递增过滤已自动跳过（结果为行情筛选结果）")
                else:
                    self._queue_log(f"近3天最低价递增筛选后剩余 {len(rows)}/{before_low} 只")

            df = pd.DataFrame(rows)
            if df.empty:
                df = pd.DataFrame(columns=[x[0] for x in RESULT_COLUMNS])

            preferred_order = [x[0] for x in RESULT_COLUMNS]
            existing = [c for c in preferred_order if c in df.columns]
            others = [c for c in df.columns if c not in existing]
            df = df[existing + others]

            csv_path, xlsx_path = export_dataframe(df, export_prefix, args.finance_only, used_date)
            self._queue_log(f"CSV 已导出：{csv_path}")
            if xlsx_path:
                self._queue_log(f"Excel 已导出：{xlsx_path}")
            else:
                self._queue_log("Excel 导出失败，但 CSV 已成功导出")

            self.ui_queue.put(("done", {
                "rows": rows,
                "df": df,
                "used_date": used_date,
                "csv_path": csv_path,
                "xlsx_path": xlsx_path,
                "finance_only": False,
                "fetch_report": False,
            }))
        except Exception as e:
            detail = f"{e}\n\n{traceback.format_exc()}"
            self.ui_queue.put(("error", detail))

    def _handle_done(self, payload):
        self.result_rows = payload["rows"]
        self.result_df = payload["df"]
        self.last_used_date = payload.get("used_date", "")
        self.last_csv_path = payload.get("csv_path", "")
        self.last_xlsx_path = payload.get("xlsx_path", "") or ""

        if self.result_rows:
            first = self.result_rows[0]
            for idx in range(1, 6):
                date_str = first.get(f"low_date_{idx}", "")
                label = f"最低_{date_str[5:]}" if date_str else f"最低_{idx}"
                self.tree.heading(f"low_{idx}", text=label)

        self._refresh_tree()
        mode_text = "实时行情"
        self.result_summary_var.set(
            f"模式：{mode_text} | 结果：{len(self.result_rows)} 条 | 报告期：{self.last_used_date or '-'}"
        )
        self.status_var.set("运行完成")
        self.run_btn.configure(state="normal")
        self.running = False

        # 激活"加入自选"按钮
        if self.result_rows:
            self.add_wl_btn.configure(state="normal")

        msg = f"筛选完成，共 {len(self.result_rows)} 条。\n\nCSV：{self.last_csv_path}"
        if self.last_xlsx_path:
            msg += f"\nExcel：{self.last_xlsx_path}"
        self._append_log("运行完成。")
        messagebox.showinfo("完成", msg)

    def _handle_error(self, detail):
        self.running = False
        self.run_btn.configure(state="normal")
        self.status_var.set("运行失败")
        self._append_log("运行失败：")
        self._append_log(detail)
        messagebox.showerror("运行失败", detail[:3000])

    def _refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for row in self.result_rows:
            values = []
            for col, _title, _width in RESULT_COLUMNS:
                v = row.get(col)
                if col in ("price", "pct_chg", "volume_ratio", "turnover", "market_cap", "pe", "pb", "roe",
                           "low_1", "low_2", "low_3", "low_4", "low_5"):
                    values.append(format_value(v, 2))
                elif col == "volume":
                    values.append(format_value(v, 0))
                elif col == "_manual":
                    values.append("★" if v else "")
                else:
                    values.append("" if v is None else str(v))
            tags = ("manual",) if row.get("_manual") else ()
            self.tree.insert("", "end", values=values, tags=tags)

        self.tree.tag_configure("manual", background="#FFF9E6")

    def export_current_result(self):
        if self.result_df is None or self.result_df.empty:
            messagebox.showinfo("提示", "当前没有可导出的结果，请先运行筛选。")
            return
        prefix = self._collect_form_data().get("export_name", "")
        try:
            csv_path, xlsx_path = export_dataframe(
                self.result_df,
                prefix,
                False,
                self.last_used_date,
            )
            self.last_csv_path = csv_path
            self.last_xlsx_path = xlsx_path or ""
            self._append_log(f"手动导出 CSV：{csv_path}")
            if xlsx_path:
                self._append_log(f"手动导出 Excel：{xlsx_path}")
            messagebox.showinfo("导出完成", f"CSV：{csv_path}" + (f"\nExcel：{xlsx_path}" if xlsx_path else ""))
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ── 加入自选 ────────────────────────────────────────────────────
    def _add_all_to_watchlist(self):
        if not self.result_rows:
            messagebox.showinfo("提示", "当前没有筛选结果。")
            return
        n = wm.add_rows_to_watchlist(self.result_rows, enrich=False)
        if n > 0:
            self.status_var.set(f"已加入 {n} 只股票到自选")
            self._append_log(f"加入自选：{n} 只股票（今日 {datetime.now().strftime('%Y-%m-%d')}）")
            messagebox.showinfo("完成", f"已将 {n} 只股票加入自选列表。\n（已存在的股票自动跳过）")
        else:
            messagebox.showinfo("提示", "所有股票已在今日自选中，无新增。")

    # ── ② 自选列表 Tab ──────────────────────────────────────────────
    def _build_watchlist_tab(self, parent):
        # 工具栏
        tb = tk.Frame(parent, bg=self.BG)
        tb.pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(tb, text="日期:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.wl_date_var = tk.StringVar()
        self.wl_date_cb = ttk.Combobox(tb, textvariable=self.wl_date_var,
                                        state="readonly", width=14)
        self.wl_date_cb.pack(side="left", padx=6)
        self.wl_date_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_watchlist())

        tk.Button(tb, text="刷新", command=self._refresh_watchlist,
                  bg="#E5EEF9", fg=self.TEXT, relief="flat", padx=10, pady=5,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=4)
        tk.Button(tb, text="删除选中", command=self._delete_watchlist_selected,
                  bg="#FEE2E2", fg=self.ERROR, relief="flat", padx=10, pady=5,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=4)
        tk.Button(tb, text="手动添加代码", command=self._manual_add_watchlist,
                  bg="#F0FDF4", fg="#166534", relief="flat", padx=10, pady=5,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=4)

        self.wl_count_var = tk.StringVar(value="")
        tk.Label(tb, textvariable=self.wl_count_var, bg=self.BG, fg=self.SUBTEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        # 表格
        wrap = tk.Frame(parent, bg=self.PANEL)
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        wl_cols = [
            ("code", "代码", 80), ("name", "名称", 100), ("add_date", "加入日期", 90),
            ("add_price", "加入价", 80), ("add_pct_chg", "加入涨跌%", 90),
            ("ma5", "MA5", 75), ("ma10", "MA10", 75), ("ma20", "MA20", 75),
            ("above_ma5", "在MA5上", 65), ("ma5_trend", "MA5趋势", 70),
            ("tail_30min_pct", "尾盘30min%", 90),
            ("volume_ratio", "量比", 65), ("turnover", "换手%", 65),
            ("market_cap", "市值(亿)", 90), ("pe", "PE", 65), ("pb", "PB", 65),
            ("roe", "ROE%", 65), ("eps", "EPS", 65),
            ("revenue_yoy", "营收同比%", 90), ("profit_yoy", "利润同比%", 90),
            ("industry", "行业", 110), ("note", "备注", 100),
        ]
        self._wl_cols = wl_cols
        cols = [c[0] for c in wl_cols]
        self.wl_tree = ttk.Treeview(wrap, columns=cols, show="headings")
        vsb = ttk.Scrollbar(wrap, orient="vertical",   command=self.wl_tree.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.wl_tree.xview)
        self.wl_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for col, title, width in wl_cols:
            self.wl_tree.heading(col, text=title)
            self.wl_tree.column(col, width=width, minwidth=50, anchor="center")
        self.wl_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        # 存储 id 映射
        self._wl_id_map = {}   # tree iid -> db watchlist id

    def _refresh_watchlist(self):
        dates = wm.get_dates()
        self.wl_date_cb["values"] = dates
        if not self.wl_date_var.get() and dates:
            self.wl_date_var.set(dates[0])

        date_str = self.wl_date_var.get()
        if not date_str:
            return

        items = wm.get_by_date(date_str)
        for row in self.wl_tree.get_children():
            self.wl_tree.delete(row)
        self._wl_id_map.clear()

        for item in items:
            values = []
            for col, _title, _width in self._wl_cols:
                v = item.get(col)
                if col in ("add_price", "ma5", "ma10", "ma20",
                           "add_pct_chg", "tail_30min_pct", "volume_ratio",
                           "turnover", "market_cap", "pe", "pb", "roe",
                           "eps", "revenue_yoy", "profit_yoy"):
                    values.append(format_value(v, 2))
                elif col == "above_ma5":
                    values.append("是" if v == 1 else ("否" if v == 0 else "-"))
                else:
                    values.append("" if v is None else str(v))
            iid = self.wl_tree.insert("", "end", values=values)
            self._wl_id_map[iid] = item["id"]

        self.wl_count_var.set(f"共 {len(items)} 只")

    def _delete_watchlist_selected(self):
        sel = self.wl_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择要删除的股票。")
            return
        if not messagebox.askyesno("确认", f"确定删除选中的 {len(sel)} 条记录？"):
            return
        for iid in sel:
            db_id = self._wl_id_map.get(iid)
            if db_id:
                wm.remove(db_id)
        self._refresh_watchlist()

    def _manual_add_watchlist(self):
        code = simpledialog.askstring("手动添加", "请输入股票代码（6位数字）：",
                                       parent=self.root)
        if not code:
            return
        code = str(code).strip().zfill(6)
        ok = wm.add_manual(code)
        if ok:
            messagebox.showinfo("完成", f"股票 {code} 已加入今日自选。")
        else:
            messagebox.showinfo("提示", f"股票 {code} 今日已在自选列表中。")
        self._refresh_watchlist()

    # ── ③ 次日跟踪 Tab ──────────────────────────────────────────────
    def _build_tracking_tab(self, parent):
        # 工具栏
        tb = tk.Frame(parent, bg=self.BG)
        tb.pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(tb, text="跟踪日期:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.track_date_cb = ttk.Combobox(tb, textvariable=self.track_date_var,
                                           state="readonly", width=14)
        self.track_date_cb.pack(side="left", padx=6)
        self.track_date_cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_tracking_table())

        tk.Label(tb, text="标签:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))
        self.snap_label_var = tk.StringVar(value="15:00")
        snap_label_entry = tk.Entry(tb, textvariable=self.snap_label_var,
                                     width=7, relief="solid", bd=1,
                                     font=("Consolas", 10))
        snap_label_entry.pack(side="left", padx=4)

        tk.Button(tb, text="立即抓取快照", command=self._take_snapshot_now,
                  bg=self.ACCENT, fg="white", relief="flat", padx=12, pady=5,
                  font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2").pack(side="left", padx=6)

        tk.Button(tb, text="刷新", command=self._refresh_tracking_table,
                  bg="#E5EEF9", fg=self.TEXT, relief="flat", padx=10, pady=5,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=4)

        # 定时器开关
        sched_frame = tk.Frame(tb, bg=self.BG)
        sched_frame.pack(side="left", padx=10)
        tk.Label(sched_frame, text="自动定时:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.sched_chk = tk.Checkbutton(sched_frame, variable=self.scheduler_on,
                                         command=self._toggle_scheduler,
                                         bg=self.BG, activebackground=self.BG)
        self.sched_chk.pack(side="left")
        if not scheduler.is_available():
            self.sched_chk.configure(state="disabled")
            tk.Label(sched_frame, text="(需安装 apscheduler)", bg=self.BG,
                     fg=self.SUBTEXT, font=("Microsoft YaHei UI", 8)).pack(side="left")

        self.track_summary_var = tk.StringVar(value="")
        tk.Label(tb, textvariable=self.track_summary_var, bg=self.BG, fg=self.SUBTEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        # 跟踪表格（动态列）
        self._track_table_frame = tk.Frame(parent, bg=self.PANEL)
        self._track_table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # 底部汇总
        self._track_stat_frame = tk.Frame(parent, bg=self.BG, height=28)
        self._track_stat_frame.pack(fill="x", padx=8, pady=(0, 8))

        # 初始化空表
        self._build_empty_tracking_table()
        self._track_snap_log = None

    def _build_empty_tracking_table(self):
        for w in self._track_table_frame.winfo_children():
            w.destroy()
        cols = ["code", "name", "add_price"]
        self._track_tree = ttk.Treeview(self._track_table_frame, columns=cols, show="headings")
        vsb = ttk.Scrollbar(self._track_table_frame, orient="vertical",
                            command=self._track_tree.yview)
        hsb = ttk.Scrollbar(self._track_table_frame, orient="horizontal",
                            command=self._track_tree.xview)
        self._track_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        for c, t, w in [("code","代码",80), ("name","名称",110), ("add_price","加入价",80)]:
            self._track_tree.heading(c, text=t)
            self._track_tree.column(c, width=w, minwidth=50, anchor="center")
        self._track_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self._track_table_frame.rowconfigure(0, weight=1)
        self._track_table_frame.columnconfigure(0, weight=1)

    def _refresh_tracking_dates(self):
        dates = db.get_watchlist_dates()
        self.track_date_cb["values"] = dates
        if not self.track_date_var.get() and dates:
            self.track_date_var.set(dates[0])
        self._refresh_tracking_table()

    def _refresh_tracking_table(self):
        watch_date = self.track_date_var.get()
        if not watch_date:
            return

        table = tracker.get_tracking_table(watch_date)
        if table.empty:
            self._build_empty_tracking_table()
            self.track_summary_var.set(f"{watch_date} 暂无快照数据")
            return

        labels = db.get_snapshot_labels(watch_date)

        # 重建动态列表格
        for w in self._track_table_frame.winfo_children():
            w.destroy()

        base_cols = [("code","代码",80), ("name","名称",110), ("add_price","加入价",80)]
        snap_cols = [(lbl, f"{lbl} 涨跌%", 90) for lbl in labels]
        all_cols = base_cols + snap_cols

        col_ids = [c[0] for c in all_cols]
        self._track_tree = ttk.Treeview(self._track_table_frame, columns=col_ids, show="headings")
        vsb = ttk.Scrollbar(self._track_table_frame, orient="vertical",   command=self._track_tree.yview)
        hsb = ttk.Scrollbar(self._track_table_frame, orient="horizontal", command=self._track_tree.xview)
        self._track_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col_id, title, width in all_cols:
            self._track_tree.heading(col_id, text=title)
            self._track_tree.column(col_id, width=width, minwidth=50, anchor="center")

        # 颜色标记
        self._track_tree.tag_configure("up",   foreground=self.UP)
        self._track_tree.tag_configure("down", foreground=self.DOWN)

        for _, row in table.iterrows():
            values = [
                str(row.get("code", "")),
                str(row.get("name", "")),
                format_value(row.get("add_price"), 2),
            ]
            row_tag = ""
            last_pct = None
            for lbl in labels:
                v = row.get(lbl)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    values.append("-")
                else:
                    last_pct = float(v)
                    values.append(f"{v:+.2f}%")
            if last_pct is not None:
                row_tag = "up" if last_pct >= 0 else "down"
            self._track_tree.insert("", "end", values=values, tags=(row_tag,))

        self._track_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self._track_table_frame.rowconfigure(0, weight=1)
        self._track_table_frame.columnconfigure(0, weight=1)

        # 汇总
        summary = tracker.get_summary(watch_date)
        self.track_summary_var.set(
            f"共{summary['total']}只 | 上涨{summary['up']}只 | "
            f"平均{('+' if (summary['avg_pct'] or 0) >= 0 else '')}"
            f"{summary['avg_pct'] or 0:.2f}% [{summary['label']}]"
        )

    def _take_snapshot_now(self):
        watch_date = self.track_date_var.get()
        if not watch_date:
            messagebox.showinfo("提示", "请先选择跟踪日期。")
            return
        label = self.snap_label_var.get().strip() or datetime.now().strftime("%H:%M")
        self.status_var.set(f"正在抓取快照 [{label}]...")

        def _worker():
            def cb(msg):
                self.ui_queue.put(("log_track", msg))
            try:
                n = tracker.take_snapshot(watch_date, label, progress_cb=cb)
                self.ui_queue.put(("snap_done", f"快照 [{label}] 完成，共 {n} 条"))
            except Exception as e:
                self.ui_queue.put(("snap_done", f"快照失败: {e}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _toggle_scheduler(self):
        if self.scheduler_on.get():
            def snap_cb(watch_date, label):
                def cb(msg):
                    self.ui_queue.put(("log_track", msg))
                try:
                    tracker.take_snapshot(watch_date, label, progress_cb=cb)
                    self.ui_queue.put(("snap_done", f"定时快照 [{label}] 完成"))
                except Exception as e:
                    self.ui_queue.put(("snap_done", f"定时快照失败: {e}"))

            ok = scheduler.start(snap_cb)
            if ok:
                self.status_var.set("定时快照已启动")
            else:
                self.scheduler_on.set(False)
                messagebox.showwarning("提示", "APScheduler 启动失败，请检查安装。")
        else:
            scheduler.stop()
            self.status_var.set("定时快照已停止")

    # ── ④ 统计分析 Tab ──────────────────────────────────────────────
    def _build_stats_tab(self, parent):
        # 设置栏
        settings = tk.Frame(parent, bg=self.BG)
        settings.pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(settings, text="统计天数:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Entry(settings, textvariable=self.stats_days_var, width=5,
                 relief="solid", bd=1, font=("Consolas", 10)).pack(side="left", padx=4)

        tk.Label(settings, text="成功阈值%:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))
        tk.Entry(settings, textvariable=self.stats_threshold_var, width=5,
                 relief="solid", bd=1, font=("Consolas", 10)).pack(side="left", padx=4)

        tk.Label(settings, text="基准快照标签:", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))
        tk.Entry(settings, textvariable=self.stats_label_var, width=8,
                 relief="solid", bd=1, font=("Consolas", 10)).pack(side="left", padx=4)

        tk.Button(settings, text="刷新统计", command=self._refresh_stats,
                  bg=self.ACCENT, fg="white", relief="flat", padx=12, pady=5,
                  font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2").pack(side="left", padx=8)
        tk.Button(settings, text="导出报告 Excel", command=self._export_stats_excel,
                  bg="#DCFCE7", fg="#166534", relief="flat", padx=10, pady=5,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left")

        # 建议文字区
        self.stats_suggest_text = tk.Text(parent, height=4, wrap="word",
                                           bg="#F8FAFC", fg=self.TEXT, relief="flat",
                                           font=("Microsoft YaHei UI", 9))
        self.stats_suggest_text.pack(fill="x", padx=8, pady=(0, 4))
        self.stats_suggest_text.insert("end", "点击「刷新统计」加载分析结果。")
        self.stats_suggest_text.configure(state="disabled")

        # 图表区
        chart_frame = tk.Frame(parent, bg=self.BG)
        chart_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._stats_chart_frame = chart_frame

        if not _MPL_AVAILABLE:
            tk.Label(chart_frame,
                     text="matplotlib 未安装，无法显示图表。\n请运行：pip install matplotlib",
                     bg=self.BG, fg=self.WARN,
                     font=("Microsoft YaHei UI", 10)).pack(expand=True)
        else:
            self._stats_fig = Figure(figsize=(14, 5), dpi=90)
            self._stats_canvas = FigureCanvasTkAgg(self._stats_fig, master=chart_frame)
            self._stats_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _refresh_stats(self):
        try:
            days = int(self.stats_days_var.get() or 30)
            threshold = float(self.stats_threshold_var.get() or 1.0)
            label = self.stats_label_var.get().strip() or "close"
        except ValueError:
            messagebox.showerror("参数错误", "天数和阈值必须为数字。")
            return

        # 建议文字
        suggestions = stats.generate_suggestions(days=days, threshold_pct=threshold,
                                                  prefer_label=label)
        self.stats_suggest_text.configure(state="normal")
        self.stats_suggest_text.delete("1.0", "end")
        self.stats_suggest_text.insert("end", "\n".join(suggestions))
        self.stats_suggest_text.configure(state="disabled")

        if not _MPL_AVAILABLE:
            return

        # 图表
        hist = stats.get_history_stats(days=days, threshold_pct=threshold, prefer_label=label)
        corr = stats.calc_condition_correlation(days=days, prefer_label=label)

        self._stats_fig.clear()

        ax1 = self._stats_fig.add_subplot(1, 2, 1)
        ax2 = self._stats_fig.add_subplot(1, 2, 2)

        # 左图：历史胜率趋势
        if not hist.empty and hist["win_rate"].notna().any():
            valid = hist.dropna(subset=["win_rate"])
            ax1.plot(valid["date"], valid["win_rate"], marker="o", color="#3B82F6", linewidth=2)
            ax1.axhline(50, color="gray", linestyle="--", linewidth=0.8)
            ax1.set_title(f"历史胜率趋势（阈值 {threshold}%）")
            ax1.set_xlabel("日期")
            ax1.set_ylabel("胜率 %")
            ax1.tick_params(axis="x", rotation=45)
            ax1.grid(True, alpha=0.3)
        else:
            ax1.text(0.5, 0.5, "暂无数据", ha="center", va="center",
                     transform=ax1.transAxes, fontsize=12, color="gray")
            ax1.set_title("历史胜率趋势")

        # 右图：条件相关性柱状图
        if not corr.empty and corr["correlation"].notna().any():
            valid_corr = corr.dropna(subset=["correlation"]).head(12)
            colors = ["#16A34A" if c >= 0 else "#DC2626"
                      for c in valid_corr["correlation"]]
            ax2.barh(valid_corr["field"], valid_corr["correlation"], color=colors)
            ax2.axvline(0, color="black", linewidth=0.8)
            ax2.set_title("筛选条件与次日涨幅相关性")
            ax2.set_xlabel("Pearson 相关系数")
            ax2.grid(True, alpha=0.3, axis="x")
        else:
            ax2.text(0.5, 0.5, "暂无数据", ha="center", va="center",
                     transform=ax2.transAxes, fontsize=12, color="gray")
            ax2.set_title("条件相关性")

        self._stats_fig.tight_layout()
        self._stats_canvas.draw()

    def _export_stats_excel(self):
        try:
            days = int(self.stats_days_var.get() or 30)
            threshold = float(self.stats_threshold_var.get() or 1.0)
            label = self.stats_label_var.get().strip() or "close"
        except ValueError:
            messagebox.showerror("参数错误", "天数和阈值必须为数字。")
            return

        hist = stats.get_history_stats(days=days, threshold_pct=threshold, prefer_label=label)
        corr = stats.calc_condition_correlation(days=days, prefer_label=label)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(BASE_DIR, f"stats_report_{ts}.xlsx")
        try:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                if not hist.empty:
                    hist.to_excel(writer, sheet_name="历史胜率", index=False)
                if not corr.empty:
                    corr.to_excel(writer, sheet_name="条件相关性", index=False)
            messagebox.showinfo("导出完成", f"统计报告已导出：\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ── _poll_queue 扩展（处理跟踪快照消息）─────────────────────────
    def _poll_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1])
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "done":
                    self._handle_done(item[1])
                elif kind == "error":
                    self._handle_error(item[1])
                elif kind == "log_track":
                    self.status_var.set(item[1])
                elif kind == "snap_done":
                    self.status_var.set(item[1])
                    self._refresh_tracking_table()
        except Exception:
            pass
        self.root.after(200, self._poll_queue)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = SelectorApp()
    app.run()

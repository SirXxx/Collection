# -*- coding: utf-8 -*-
"""
AStockSelector_AK_GUI.pyw

akshare A 股筛选桌面版：
- 可视化输入筛选参数
- 自动保存 / 手动保存参数
- 结果表格展示
- 自动导出 CSV + Excel
- 尽量复用 AStockSelector_AK.py 的已有抓取/筛选逻辑

说明：
- 当前网络环境下，实时行情接口可能失败；可勾选“仅财报模式”先使用财报筛选。
- 参数保存在同目录 AStockSelector_AK_GUI_config.json
- 导出结果默认保存到脚本同目录
"""

import json
import os
import threading
import traceback
import queue
from types import SimpleNamespace
from datetime import datetime

import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox

import AStockSelector_AK as core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "AStockSelector_AK_GUI_config.json")

SORT_OPTIONS = [
    "price", "pct_chg", "volume", "volume_ratio", "turnover",
    "market_cap", "circ_market_cap", "pe", "eps", "revenue",
    "net_profit", "revenue_yoy", "profit_yoy"
]

DATA_SOURCE_OPTIONS = core.DATA_SOURCE_OPTIONS  # ["auto", "eastmoney", "sina"]

# 下拉选择型字段 → 选项列表
CHOICE_OPTIONS = {
    "sort_by":     SORT_OPTIONS,
    "data_source": DATA_SOURCE_OPTIONS,
}

RESULT_COLUMNS = [
    ("code", "代码", 80),
    ("name", "名称", 110),
    ("price", "现价", 80),
    ("pct_chg", "涨跌幅%", 90),
    ("low_1", "最低_1", 75),
    ("low_2", "最低_2", 75),
    ("low_3", "最低_3", 75),
    ("low_4", "最低_4", 75),
    ("low_5", "最低_5", 75),
    ("volume", "成交量", 100),
    ("volume_ratio", "量比", 80),
    ("turnover", "换手%", 80),
    ("market_cap", "总市值(亿)", 100),
    ("pe", "PE", 80),
    ("eps", "EPS", 80),
    ("revenue", "营收(亿)", 100),
    ("net_profit", "净利润(亿)", 100),
    ("revenue_yoy", "营收同比%", 100),
    ("profit_yoy", "利润同比%", 100),
    ("industry", "行业", 120),
    ("notice_date", "公告日", 100),
    ("report_date", "报告期", 90),
]

FIELD_DEFS = [
    ("top_n", "输出数量 Top N", "int", 20),
    ("sort_by", "排序字段", "choice", "pct_chg"),
    ("ascending", "升序排序", "bool", False),
    ("finance_only", "仅财报模式", "bool", True),
    ("report_date", "指定报告期(如 20241231)", "str", ""),
    ("include_bj", "包含北交所", "bool", False),
    ("data_source", "数据来源", "choice", "auto"),
    ("export_name", "导出文件名前缀(可空)", "str", ""),
    ("spot_retries", "实时行情重试次数", "int", 3),
    ("spot_retry_sleep", "实时行情重试间隔秒", "int", 3),

    ("min_price", "最低价格", "float", ""),
    ("max_price", "最高价格", "float", ""),
    ("min_pct_chg", "最小涨跌幅%", "float", ""),
    ("max_pct_chg", "最大涨跌幅%", "float", ""),
    ("min_volume", "最小成交量", "float", ""),
    ("min_volume_ratio", "最小量比", "float", ""),
    ("max_volume_ratio", "最大量比", "float", ""),
    ("min_turnover", "最小换手率%", "float", ""),
    ("max_turnover", "最大换手率%", "float", ""),
    ("min_market_cap", "最小市值(亿)", "float", ""),
    ("max_market_cap", "最大市值(亿)", "float", ""),
    ("min_pe", "最小 PE", "float", ""),
    ("max_pe", "最大 PE", "float", ""),
    ("min_eps", "最小 EPS", "float", ""),
    ("min_net_profit", "最小净利润(亿)", "float", ""),
    ("min_revenue_yoy", "最小营收同比%", "float", ""),
    ("min_profit_yoy", "最小净利润同比%", "float", ""),
    ("low_rising", "近3天最低价递增", "bool", False),
]

# 范围型字段对：(min_key, max_key) -> 显示标签
RANGE_LABELS = {
    ("min_pct_chg",    "max_pct_chg"):    "涨幅范围%",
    ("min_volume_ratio", "max_volume_ratio"): "量比范围",
    ("min_turnover",   "max_turnover"):   "换手率范围%",
    ("min_market_cap", "max_market_cap"): "市值范围(亿)",
}

SECTION_LAYOUT = [
    ("基础参数", [
        "top_n", "sort_by", "ascending", "finance_only", "report_date",
        "include_bj", "data_source", "export_name", "spot_retries", "spot_retry_sleep"
    ]),
    ("行情条件", [
        "min_price", "max_price",
        ("min_pct_chg", "max_pct_chg"),
        "min_volume",
        ("min_volume_ratio", "max_volume_ratio"),
        ("min_turnover", "max_turnover"),
        ("min_market_cap", "max_market_cap"),
        "min_pe", "max_pe", "low_rising"
    ]),
    ("财报条件", [
        "min_eps", "min_net_profit", "min_revenue_yoy", "min_profit_yoy"
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
    BG = "#F4F7FB"
    PANEL = "#FFFFFF"
    BORDER = "#D9E2EF"
    TITLE = "#1F3B5B"
    TEXT = "#243447"
    SUBTEXT = "#5B6B7A"
    ACCENT = "#3B82F6"
    SUCCESS = "#16A34A"
    WARN = "#B45309"
    ERROR = "#DC2626"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("A股筛选器 AK GUI")
        self.root.geometry("1360x820+80+50")
        self.root.minsize(1180, 700)
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

        self._build_style()
        self._build_ui()
        self._load_to_form(self.config_data)
        self.root.after(200, self._poll_queue)

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

        title = tk.Label(top, text="A 股股票筛选器（akshare 桌面版）", bg=self.BG, fg=self.TITLE,
                         font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor="w")

        subtitle = tk.Label(
            top,
            text="可视化输入条件、自动保存参数、展示结果并导出 CSV / Excel。当前环境若实时行情失败，可勾选“仅财报模式”。",
            bg=self.BG,
            fg=self.SUBTEXT,
            font=("Microsoft YaHei UI", 9)
        )
        subtitle.pack(anchor="w", pady=(4, 0))

        toolbar = tk.Frame(self.root, bg=self.BG)
        toolbar.pack(fill="x", padx=14, pady=(0, 8))

        self.run_btn = tk.Button(toolbar, text="运行筛选", command=self.run_selection,
                                 bg=self.ACCENT, fg="white", relief="flat", padx=16, pady=6,
                                 font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2")
        self.run_btn.pack(side="left")

        tk.Button(toolbar, text="保存参数", command=self.save_current_config,
                  bg="#E5EEF9", fg=self.TEXT, relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=8)

        tk.Button(toolbar, text="重载参数", command=self.reload_config,
                  bg="#E5EEF9", fg=self.TEXT, relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left")

        tk.Button(toolbar, text="重置默认", command=self.reset_defaults,
                  bg="#F3F4F6", fg=self.TEXT, relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left", padx=8)

        tk.Button(toolbar, text="导出当前结果", command=self.export_current_result,
                  bg="#DCFCE7", fg="#166534", relief="flat", padx=12, pady=6,
                  font=("Microsoft YaHei UI", 9), cursor="hand2").pack(side="left")

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(toolbar, textvariable=self.status_var, bg=self.BG, fg=self.SUBTEXT,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        body = tk.PanedWindow(self.root, orient="horizontal", sashrelief="flat", sashwidth=6,
                              bg=self.BG, bd=0)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        left_wrap = tk.Frame(body, bg=self.PANEL, bd=1, relief="solid", highlightthickness=0)
        right_wrap = tk.Frame(body, bg=self.PANEL, bd=1, relief="solid", highlightthickness=0)
        body.add(left_wrap, minsize=360)
        body.add(right_wrap, minsize=700)

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
                    payload = item[1]
                    self._handle_done(payload)
                elif kind == "error":
                    self._handle_error(item[1])
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

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
            report_date=data.get("report_date", ""),
            finance_only=bool(data.get("finance_only")),
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
            min_eps=parse_float("min_eps"),
            min_net_profit=parse_float("min_net_profit"),
            min_revenue_yoy=parse_float("min_revenue_yoy"),
            min_profit_yoy=parse_float("min_profit_yoy"),
            spot_retries=parse_int("spot_retries", 3),
            spot_retry_sleep=parse_int("spot_retry_sleep", 3),
            low_rising=bool(data.get("low_rising")),
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
            self._queue_log(f"模式：{'仅财报模式' if args.finance_only else '实时行情 + 财报模式'}，数据来源：{args.data_source}")
            if args.report_date:
                self._queue_log(f"指定报告期：{args.report_date}")
            self._queue_log(f"排序字段：{args.sort_by}，{'升序' if args.ascending else '降序'}")

            if args.finance_only:
                self._queue_log("开始拉取财报数据...")
                report_df, used_date = core.fetch_report_data(
                    report_date=args.report_date,
                    include_bj=args.include_bj,
                )
                rows = report_df.to_dict(orient="records")
                self._queue_log(f"财报数据拉取完成，共 {len(rows)} 条，使用报告期：{used_date}")
            else:
                self._queue_log("开始拉取实时行情...")
                spot_df = core.fetch_spot_data(
                    retries=args.spot_retries,
                    sleep_sec=args.spot_retry_sleep,
                    include_bj=args.include_bj,
                    data_source=args.data_source,
                )
                self._queue_log(
                    f"实时行情完成，共 {len(spot_df)} 条；来源：{spot_df.attrs.get('spot_source', '-') }"
                )
                self._queue_log("开始拉取财报数据...")
                report_df, used_date = core.fetch_report_data(
                    report_date=args.report_date,
                    include_bj=args.include_bj,
                )
                self._queue_log(f"财报数据完成，共 {len(report_df)} 条，使用报告期：{used_date}")
                merged = pd.merge(spot_df, report_df, on="code", how="left", suffixes=("", "_report"))
                if "name_report" in merged.columns:
                    merged["name"] = merged["name"].fillna(merged["name_report"])
                    merged.drop(columns=["name_report"], inplace=True)
                rows = merged.to_dict(orient="records")

            before_count = len(rows)
            rows = [x for x in rows if core.pass_filters(x, args)]
            rows.sort(key=lambda x: core.sort_value(x, args.sort_by), reverse=not args.ascending)
            rows = rows[:args.top_n]
            self._queue_log(f"筛选前 {before_count} 条，筛选后 {len(rows)} 条，取前 {args.top_n} 条")

            if getattr(args, "low_rising", False):
                self._queue_log(f"正在补充 {len(rows)} 只股票的近5日最低价（并发请求，请稍候）...")
                rows = core.enrich_with_recent_lows(rows, days=5, data_source=args.data_source)
                before_low = len(rows)
                rows = [r for r in rows if core.pass_low_rising_filter(r)]
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
                "finance_only": args.finance_only,
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

        # 更新近5日最低价列标题（显示实际日期）
        if self.result_rows:
            first = self.result_rows[0]
            for idx in range(1, 6):
                date_str = first.get(f"low_date_{idx}", "")
                label = f"最低_{date_str[5:]}" if date_str else f"最低_{idx}"
                self.tree.heading(f"low_{idx}", text=label)

        self._refresh_tree()
        mode_text = "仅财报" if payload.get("finance_only") else "实时+财报"
        self.result_summary_var.set(
            f"模式：{mode_text} | 结果：{len(self.result_rows)} 条 | 报告期：{self.last_used_date or '-'}"
        )
        self.status_var.set("运行完成")
        self.run_btn.configure(state="normal")
        self.running = False

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
                if col in ("price", "pct_chg", "volume_ratio", "turnover", "market_cap", "pe", "eps", "revenue", "net_profit", "revenue_yoy", "profit_yoy",
                           "low_1", "low_2", "low_3", "low_4", "low_5"):
                    values.append(format_value(v, 2))
                elif col == "volume":
                    values.append(format_value(v, 0))
                elif col in ("notice_date", "report_date"):
                    values.append(str(v or "")[:10])
                else:
                    values.append("" if v is None else str(v))
            self.tree.insert("", "end", values=values)

    def export_current_result(self):
        if self.result_df is None or self.result_df.empty:
            messagebox.showinfo("提示", "当前没有可导出的结果，请先运行筛选。")
            return
        prefix = self._collect_form_data().get("export_name", "")
        try:
            csv_path, xlsx_path = export_dataframe(
                self.result_df,
                prefix,
                bool(self.vars["finance_only"].get()),
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

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = SelectorApp()
    app.run()

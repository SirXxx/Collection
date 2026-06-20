# -*- coding: utf-8 -*-
"""
fund_gui.py — 基金分析工具图形界面
基于 tkinter（Python 内置，无需额外安装）。

运行方式：
  python fund/fund_gui.py
  或双击此文件（需系统已关联 .py → python）
"""

import os
import re
import sys
import json
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime

# ── 路径 ──────────────────────────────────────────────────────────────────────
FUND_DIR        = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH     = os.path.join(FUND_DIR, "config.json")
OUTPUT_DIR      = os.path.join(FUND_DIR, "output")
CACHE_DIR       = os.path.join(OUTPUT_DIR, "cache")
ANALYSIS_SCRIPT = os.path.join(FUND_DIR, "fund_analysis.py")
QUERY_SCRIPT    = os.path.join(FUND_DIR, "query_stock_funds.py")


# ── 配置读写 ──────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── 主窗口 ────────────────────────────────────────────────────────────────────
class FundGUI:
    # 日志颜色配置（深色主题）
    LOG_BG  = "#1e1e1e"
    LOG_FG  = "#d4d4d4"
    TAG_MAP = {
        "error":   "#f44747",
        "warn":    "#ffcc00",
        "section": "#4ec9b0",
        "ok":      "#89d185",
        "info":    "#9cdcfe",
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("基金分析工具 · Fund Analyzer")
        self.root.geometry("1060x780")
        self.root.minsize(820, 600)

        self._process = None  # subprocess.Popen | None
        self._analysis_running = False

        self._build_ui()
        self._load_config_to_ui()

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_main  = ttk.Frame(nb)
        tab_query = ttk.Frame(nb)
        nb.add(tab_main,  text="  主分析  ")
        nb.add(tab_query, text="  查询个股持仓  ")

        self._build_main_tab(tab_main)
        self._build_query_tab(tab_query)

    # ── 主分析 Tab ────────────────────────────────────────────────────────────

    def _build_main_tab(self, parent: ttk.Frame):
        # ─ 配置面板 ─
        cfg = ttk.LabelFrame(parent, text=" 配置参数 ", padding=10)
        cfg.pack(fill=tk.X, padx=8, pady=(8, 4))

        # 行 1：数据来源 + SSL
        r1 = ttk.Frame(cfg)
        r1.pack(fill=tk.X, pady=3)
        ttk.Label(r1, text="数据来源：").pack(side=tk.LEFT)
        self.var_source = tk.StringVar()
        ttk.Radiobutton(r1, text="新浪+巨潮（公司内网）",  variable=self.var_source, value="sina_cninfo").pack(side=tk.LEFT, padx=(4, 14))
        ttk.Radiobutton(r1, text="东方财富（无限制网络）", variable=self.var_source, value="eastmoney").pack(side=tk.LEFT, padx=4)
        ttk.Separator(r1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=16)
        self.var_ssl = tk.BooleanVar()
        ttk.Checkbutton(r1, text="开启 SSL 验证（内网代理请关闭）", variable=self.var_ssl).pack(side=tk.LEFT)

        # 行 2：Top N + ETF
        r2 = ttk.Frame(cfg)
        r2.pack(fill=tk.X, pady=3)
        ttk.Label(r2, text="排行前 N 名：").pack(side=tk.LEFT)
        self.var_topn = tk.IntVar()
        ttk.Spinbox(r2, from_=3, to=50, textvariable=self.var_topn, width=5).pack(side=tk.LEFT, padx=(4, 18))
        self.var_etf = tk.BooleanVar()
        ttk.Checkbutton(r2, text="包含 ETF 排行分析", variable=self.var_etf).pack(side=tk.LEFT)
        ttk.Label(r2, text="  ETF 样本数：").pack(side=tk.LEFT)
        self.var_etf_sample = tk.IntVar()
        ttk.Spinbox(r2, from_=20, to=500, textvariable=self.var_etf_sample, width=6).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(r2, text="  （市值前 N 只）", foreground="gray").pack(side=tk.LEFT)

        # 行 3：季报日期
        r3 = ttk.Frame(cfg)
        r3.pack(fill=tk.X, pady=3)
        ttk.Label(r3, text="当期季报日期：").pack(side=tk.LEFT)
        self.var_q1 = tk.StringVar()
        ttk.Entry(r3, textvariable=self.var_q1, width=12).pack(side=tk.LEFT, padx=(4, 18))
        ttk.Label(r3, text="对比季报日期：").pack(side=tk.LEFT)
        self.var_q4 = tk.StringVar()
        ttk.Entry(r3, textvariable=self.var_q4, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Label(r3, text="  （格式 YYYYMMDD，限 0331 / 0630 / 0930 / 1231）", foreground="gray").pack(side=tk.LEFT)

        # 行 4：基金池
        r4 = ttk.Frame(cfg)
        r4.pack(fill=tk.X, pady=3)
        ttk.Label(r4, text="基金池代码：").pack(side=tk.LEFT, anchor=tk.N, pady=2)
        pool_wrap = ttk.Frame(r4)
        pool_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.txt_pool = tk.Text(pool_wrap, height=3, wrap=tk.WORD, width=60,
                                font=("Consolas", 9))
        sb = ttk.Scrollbar(pool_wrap, command=self.txt_pool.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_pool.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.txt_pool.configure(yscrollcommand=sb.set)
        ttk.Label(r4, text="  逗号 / 空格 / 换行均可\n  例：011369, 022364",
                  foreground="gray", justify=tk.LEFT).pack(side=tk.LEFT, anchor=tk.N)

        # ─ 操作按钮 ─
        btn_bar = ttk.Frame(parent)
        btn_bar.pack(fill=tk.X, padx=8, pady=4)

        self.btn_run  = ttk.Button(btn_bar, text="▶  开始分析", command=self._run_analysis, width=14)
        self.btn_stop = ttk.Button(btn_bar, text="⏹  停止",    command=self._stop_analysis, width=10, state=tk.DISABLED)
        btn_save      = ttk.Button(btn_bar, text="保存配置",    command=self._save_config,   width=10)
        btn_cache     = ttk.Button(btn_bar, text="清除缓存",    command=self._clear_cache,   width=10)
        btn_open      = ttk.Button(btn_bar, text="打开输出目录", command=self._open_output,  width=12)
        btn_clear_log = ttk.Button(btn_bar, text="清空日志",    command=self._clear_main_log, width=10)

        for w in (self.btn_run, self.btn_stop, btn_save, btn_cache, btn_open, btn_clear_log):
            w.pack(side=tk.LEFT, padx=3)

        self.lbl_status = ttk.Label(btn_bar, text="就绪", foreground="gray", width=20, anchor=tk.W)
        self.lbl_status.pack(side=tk.RIGHT, padx=8)

        # 进度条
        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=8, pady=(0, 4))

        # ─ 日志区 ─
        log_frame = ttk.LabelFrame(parent, text=" 运行日志 ", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.log = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg=self.LOG_BG, fg=self.LOG_FG,
            insertbackground="white",
        )
        self.log.pack(fill=tk.BOTH, expand=True)
        for tag, color in self.TAG_MAP.items():
            self.log.tag_config(tag, foreground=color)
        self.log.tag_config("section", foreground=self.TAG_MAP["section"],
                            font=("Consolas", 9, "bold"))

    # ── 查询 Tab ──────────────────────────────────────────────────────────────

    def _build_query_tab(self, parent: ttk.Frame):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=8, pady=10)

        ttk.Label(top, text="股票代码：").pack(side=tk.LEFT)
        self.var_stock = tk.StringVar(value="002015")
        ttk.Entry(top, textvariable=self.var_stock, width=12,
                  font=("Consolas", 10)).pack(side=tk.LEFT, padx=4)

        self.btn_query = ttk.Button(top, text="▶  查询持仓基金", command=self._run_query, width=16)
        self.btn_query.pack(side=tk.LEFT, padx=8)

        ttk.Label(top, text="查询该个股被哪些基金重仓持有（东方财富数据，结果保存至 output/）",
                  foreground="gray").pack(side=tk.LEFT, padx=4)

        btn_clear_q = ttk.Button(top, text="清空", command=lambda: self._clear_widget(self.query_log))
        btn_clear_q.pack(side=tk.RIGHT, padx=4)

        log_frame = ttk.LabelFrame(parent, text=" 查询结果 ", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.query_log = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg=self.LOG_BG, fg=self.LOG_FG,
        )
        self.query_log.pack(fill=tk.BOTH, expand=True)
        for tag, color in self.TAG_MAP.items():
            self.query_log.tag_config(tag, foreground=color)

    # ── 配置 I/O ──────────────────────────────────────────────────────────────

    def _load_config_to_ui(self):
        cfg   = load_config()
        dates = cfg.get("compare_dates", {})
        pool  = cfg.get("fund_pool", [])

        self.var_source.set(cfg.get("data_source", "sina_cninfo"))
        self.var_ssl.set(cfg.get("ssl_verify", False))
        self.var_topn.set(cfg.get("top_n", 10))
        self.var_etf.set(cfg.get("include_etf", True))
        self.var_etf_sample.set(cfg.get("etf_sample", 100))
        self.var_q1.set(dates.get("current_quarter", "20250331"))
        self.var_q4.set(dates.get("prev_quarter", "20241231"))

        self.txt_pool.delete("1.0", tk.END)
        self.txt_pool.insert("1.0", ", ".join(str(c) for c in pool))

    def _collect_config(self) -> dict:
        raw   = self.txt_pool.get("1.0", tk.END).strip()
        codes = [c.strip() for c in re.split(r"[,\s，]+", raw) if c.strip()]

        cfg = load_config()          # 保留注释字段
        cfg["data_source"]    = self.var_source.get()
        cfg["ssl_verify"]     = self.var_ssl.get()
        cfg["top_n"]          = self.var_topn.get()
        cfg["include_etf"]    = self.var_etf.get()
        cfg["etf_sample"]     = self.var_etf_sample.get()
        cfg["fund_pool"]      = codes
        cfg["compare_dates"]  = {
            "current_quarter": self.var_q1.get().strip(),
            "prev_quarter":    self.var_q4.get().strip(),
        }
        return cfg

    def _save_config(self):
        cfg = self._collect_config()
        save_config(cfg)
        self._set_status("配置已保存 ✓", "ok")

    # ── 主分析 运行 ───────────────────────────────────────────────────────────

    def _run_analysis(self):
        if self._analysis_running:
            return

        # 保存当前配置后再启动
        save_config(self._collect_config())

        self._analysis_running = True
        self.btn_run.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.progress.start(12)
        self._set_status("运行中...", "info")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_to(self.log,
                     f"\n{'='*62}\n  开始分析  {now}\n{'='*62}\n",
                     "section")

        def worker():
            self._run_script(ANALYSIS_SCRIPT, [], self.log)
            self.root.after(0, self._analysis_done)

        threading.Thread(target=worker, daemon=True).start()

    def _analysis_done(self):
        self._analysis_running = False
        self.btn_run.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.progress.stop()
        self._set_status("完成", "ok")

    def _stop_analysis(self):
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
        self._log_to(self.log, "\n  [用户已中止运行]\n", "warn")

    # ── 个股查询 运行 ─────────────────────────────────────────────────────────

    def _run_query(self):
        code = self.var_stock.get().strip()
        if not code:
            messagebox.showwarning("提示", "请输入股票代码")
            return
        self.btn_query.config(state=tk.DISABLED)
        now = datetime.now().strftime("%H:%M:%S")
        self._log_to(self.query_log,
                     f"\n{'='*50}\n  查询 {code}  {now}\n{'='*50}\n",
                     "section")

        def worker():
            self._run_script(QUERY_SCRIPT, [code], self.query_log)
            self.root.after(0, lambda: self.btn_query.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    # ── 公共脚本执行 ──────────────────────────────────────────────────────────

    def _run_script(self, script: str, args: list, widget):
        """在子进程中运行 script，实时流式输出到 widget。"""
        cmd = [sys.executable, script] + args
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=FUND_DIR,
                bufsize=1,
            )
            for line in self._process.stdout:
                self.root.after(0, self._log_line, widget, line)
            self._process.wait()
            rc = self._process.returncode
            tag = "ok" if rc == 0 else "error"
            self.root.after(0, self._log_to, widget,
                            f"\n  [进程结束，退出码 {rc}]\n", tag)
        except Exception as exc:
            self.root.after(0, self._log_to, widget,
                            f"\n[启动失败] {exc}\n请确认 Python 可执行路径：{sys.executable}\n",
                            "error")
        finally:
            self._process = None

    # ── 日志工具 ──────────────────────────────────────────────────────────────

    def _log_line(self, widget, line: str):
        """根据内容自动着色。"""
        low = line.lower()
        if any(k in low for k in ("错误", "error", "exception", "traceback", "failed", "fail")):
            tag = "error"
        elif any(k in low for k in ("警告", "warn")):
            tag = "warn"
        elif line.strip().startswith("="):
            tag = "section"
        elif any(k in line for k in ("完成", "已保存", "→", "成功", "ok")):
            tag = "ok"
        elif line.strip().startswith("["):
            tag = "info"
        else:
            tag = None
        self._log_to(widget, line, tag)

    def _log_to(self, widget, text: str, tag=None):
        widget.config(state=tk.NORMAL)
        if tag:
            widget.insert(tk.END, text, tag)
        else:
            widget.insert(tk.END, text)
        widget.see(tk.END)
        widget.config(state=tk.DISABLED)

    def _clear_main_log(self):
        self._clear_widget(self.log)

    def _clear_widget(self, widget):
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.config(state=tk.DISABLED)

    def _set_status(self, msg: str, kind: str = "info"):
        colors = {"ok": "#89d185", "error": "#f44747", "info": "#9cdcfe", "warn": "#ffcc00"}
        self.lbl_status.config(text=msg, foreground=colors.get(kind, "gray"))

    # ── 工具按钮 ──────────────────────────────────────────────────────────────

    def _clear_cache(self):
        if not os.path.isdir(CACHE_DIR):
            messagebox.showinfo("提示", "缓存目录不存在，无需清除。")
            return
        files = [f for f in os.listdir(CACHE_DIR)
                 if os.path.isfile(os.path.join(CACHE_DIR, f))]
        if not files:
            messagebox.showinfo("提示", "缓存目录为空。")
            return
        if not messagebox.askyesno("确认清除缓存",
                                   f"将删除 {len(files)} 个缓存文件：\n{CACHE_DIR}\n\n确定继续？"):
            return
        removed = 0
        for fn in files:
            try:
                os.remove(os.path.join(CACHE_DIR, fn))
                removed += 1
            except Exception:
                pass
        msg = f"\n  [缓存] 已清除 {removed} 个文件\n"
        self._log_to(self.log, msg, "warn")
        self._set_status(f"缓存已清除（{removed} 个）", "warn")

    def _open_output(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.startfile(OUTPUT_DIR)


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()

    # 选用系统最佳主题
    style = ttk.Style(root)
    for theme in ("vista", "xpnative", "winnative", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break

    # 加大按钮字体
    style.configure("TButton", padding=4)

    app = FundGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

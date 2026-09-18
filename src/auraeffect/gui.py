from __future__ import annotations

import ctypes
import subprocess
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

from .cli import process_video


@dataclass
class Job:
    input_path: Path
    mask_path: Path
    output_path: Path
    status: str = "等待中"
    error: str = ""


class AuraeffectApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Auraeffect")
        self.root.geometry("1100x650")
        self.root.minsize(850, 500)
        self.jobs: list[Job] = []
        self.selected_videos: list[Path] = []
        self.events: Queue[tuple[str, object]] = Queue()
        self.worker: threading.Thread | None = None
        self.running = False

        self.mask_var = tk.StringVar()
        self.output_mode = tk.StringVar(value="source")
        self.output_dir_var = tk.StringVar()
        self.action_var = tk.StringVar(value="不执行")
        self.progress_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value="请选择视频和水印图片，然后点击“添加任务”")
        self.error_var = tk.StringVar(value="")

        self._build_ui()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        controls = ttk.LabelFrame(self.root, text="添加任务", padding=10)
        controls.pack(fill="x", padx=10, pady=(10, 5))
        ttk.Button(controls, text="选择视频", command=self._choose_videos).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(controls, text="选择水印图片", command=self._choose_mask).grid(row=0, column=1, padx=(0, 8))
        ttk.Label(controls, textvariable=self.mask_var, width=58).grid(row=0, column=2, sticky="w")
        ttk.Button(controls, text="添加任务", command=self._add_tasks).grid(row=0, column=3, padx=(8, 0))
        ttk.Radiobutton(controls, text="原位置导出", variable=self.output_mode, value="source").grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Radiobutton(controls, text="指定导出位置", variable=self.output_mode, value="custom").grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Entry(controls, textvariable=self.output_dir_var, width=68).grid(row=2, column=2, sticky="ew")
        ttk.Button(controls, text="浏览", command=self._choose_output_dir).grid(row=2, column=3, padx=(8, 0))
        controls.columnconfigure(2, weight=1)

        queue_frame = ttk.LabelFrame(self.root, text="处理队列", padding=8)
        queue_frame.pack(fill="both", expand=True, padx=10, pady=5)
        columns = ("video", "mask", "output", "status", "error")
        self.tree = ttk.Treeview(queue_frame, columns=columns, show="headings", selectmode="extended")
        headings = {"video": "视频", "mask": "Mask", "output": "输出位置", "status": "状态", "error": "失败原因"}
        widths = {"video": 220, "mask": 170, "output": 260, "status": 80, "error": 300}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        actions = ttk.Frame(self.root, padding=(10, 5))
        actions.pack(fill="x")
        ttk.Button(actions, text="删除选中", command=self._remove_selected).pack(side="left")
        ttk.Button(actions, text="清空队列", command=self._clear_queue).pack(side="left", padx=8)
        ttk.Label(actions, text="处理完成后：").pack(side="left", padx=(20, 5))
        ttk.Combobox(actions, textvariable=self.action_var, values=("不执行", "关机", "休眠"), state="readonly", width=12).pack(side="left")
        ttk.Button(actions, text="开始处理", command=self._start).pack(side="right")

        progress = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        progress.pack(fill="x")
        ttk.Progressbar(progress, variable=self.progress_var, maximum=100).pack(fill="x")
        ttk.Label(progress, textvariable=self.status_var).pack(anchor="w", pady=(4, 0))
        ttk.Label(progress, textvariable=self.error_var, foreground="#b00020", wraplength=1050).pack(anchor="w")

    def _choose_videos(self) -> None:
        paths = filedialog.askopenfilenames(title="选择视频", filetypes=(("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm"), ("所有文件", "*.*")))
        if paths:
            self.selected_videos = [Path(path).resolve() for path in paths]
            self.status_var.set(f"已选择 {len(paths)} 个视频，请选择水印图片 后点击“添加任务”")

    def _choose_mask(self) -> None:
        path = filedialog.askopenfilename(title="选择水印图片", filetypes=(("图片文件", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")))
        if path:
            self.mask_var.set(str(Path(path).resolve()))

    def _choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择导出位置")
        if path:
            self.output_dir_var.set(str(Path(path).resolve()))

    def _add_tasks(self) -> None:
        if self.running:
            return
        if not self.selected_videos:
            messagebox.showwarning("未选择视频", "请先选择至少一个视频。")
            return
        mask = Path(self.mask_var.get()) if self.mask_var.get() else None
        if mask is None or not mask.exists():
            messagebox.showwarning("缺少水印图片", "请选择有效的水印图片后再添加任务。")
            return
        output_dir = Path(self.output_dir_var.get()) if self.output_mode.get() == "custom" else None
        if output_dir is not None and not output_dir.exists():
            messagebox.showwarning("导出位置无效", "指定的导出位置不存在，请重新选择。")
            return
        for input_path in self.selected_videos:
            output = self._available_output(input_path, output_dir)
            job = Job(input_path, mask.resolve(), output)
            self.jobs.append(job)
            self.tree.insert("", "end", iid=str(id(job)), values=(input_path, mask, output, job.status, ""))
        count = len(self.selected_videos)
        self.selected_videos.clear()
        self.status_var.set(f"已添加 {count} 个任务")

    def _available_output(self, input_path: Path, output_dir: Path | None) -> Path:
        base = output_dir / input_path.name if output_dir else input_path
        candidate = base.with_name(f"{base.stem}_ae{base.suffix}")
        index = 2
        while candidate.exists() or any(job.output_path == candidate for job in self.jobs):
            candidate = base.with_name(f"{base.stem}_ae{index}{base.suffix}")
            index += 1
        return candidate

    def _remove_selected(self) -> None:
        if self.running:
            return
        selected = set(self.tree.selection())
        self.jobs = [job for job in self.jobs if str(id(job)) not in selected]
        for item in selected:
            self.tree.delete(item)

    def _clear_queue(self) -> None:
        if self.running:
            return
        self.jobs.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _start(self) -> None:
        if self.running:
            return
        pending = [job for job in self.jobs if job.status == "等待中"]
        if not pending:
            messagebox.showinfo("没有任务", "队列中没有等待处理的任务。")
            return
        if self.action_var.get() != "不执行" and not messagebox.askyesno("确认操作", f"队列结束后将执行{self.action_var.get()}，无论任务成功或失败。继续吗？"):
            return
        self.running = True
        self.error_var.set("")
        self.worker = threading.Thread(target=self._run_queue, args=(pending,), daemon=True)
        self.worker.start()

    def _run_queue(self, jobs: list[Job]) -> None:
        completed = 0
        try:
            for job in jobs:
                self.events.put(("job", (job, "处理中")))

                def on_event(event: str, payload: dict, current=job) -> None:
                    self.events.put(("progress", (current, event, payload)))

                try:
                    process_video(job.input_path, job.mask_path, job.output_path, on_event=on_event)
                except Exception as exc:  # noqa: BLE001 - report and continue with next job.
                    self.events.put(("job", (job, "失败", str(exc))))
                else:
                    self.events.put(("job", (job, "成功", "")))
                completed += 1
                self.events.put(("overall", (completed, len(jobs))))
        finally:
            self.events.put(("finished", None))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "job":
                    job, status, *error = data
                    job.status = status
                    job.error = error[0] if error else ""
                    self._refresh_job(job)
                    self.status_var.set(f"{job.input_path.name}: {status}")
                    if job.error:
                        self.error_var.set(f"{job.input_path.name} 失败原因：{job.error}")
                elif kind == "progress":
                    job, event, payload = data
                    if event == "stage_start":
                        self.progress_var.set(0)
                        self.status_var.set(f"{job.input_path.name}: {payload.get('label', '')}")
                    elif event == "stage_finish":
                        self.progress_var.set(100)
                    elif event == "progress":
                        self.progress_var.set(payload.get("fraction", 0) * 100)
                        self.status_var.set(f"{job.input_path.name}: {payload.get('label', '')} {payload.get('detail', '')}")
                elif kind == "overall":
                    completed, total = data
                    self.status_var.set(f"队列进度：{completed}/{total}")
                elif kind == "finished":
                    self.running = False
                    self.progress_var.set(100)
                    self.status_var.set("队列处理结束")
                    self._perform_final_action()
        except Empty:
            pass
        self.root.after(100, self._poll_events)

    def _refresh_job(self, job: Job) -> None:
        item = str(id(job))
        if self.tree.exists(item):
            self.tree.item(item, values=(job.input_path, job.mask_path, job.output_path, job.status, job.error))

    def _perform_final_action(self) -> None:
        if self.action_var.get() == "关机":
            subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)
        elif self.action_var.get() == "休眠":
            ctypes.windll.powrprof.SetSuspendState(True, True, False)

    def _close(self) -> None:
        if self.running:
            messagebox.showwarning("正在处理", "请等待当前任务结束后再关闭窗口。")
            return
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    AuraeffectApp(root)
    root.mainloop()
    return 0

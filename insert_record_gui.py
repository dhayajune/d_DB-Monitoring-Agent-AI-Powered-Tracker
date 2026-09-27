"""DB Monitoring Agent - small desktop form to insert a wind turbine installation record.

    python insert_record_gui.py
"""
import random
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import pymysql

from db import app_connection
from workflow import (COLUMNS, INSERT_SQL, NOTES, PHASE_STATUS, PHASES, SITES, STATUSES, TECHNICIANS,
                      TURBINE_MODELS, make_event, new_turbine, next_phase)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

ACCENT = "#1f6fd1"
BG = "#ffffff"
MUTED = "#4b5766"
OK = "#17613a"
ERR = "#a4480f"


class InsertRecordApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DB Monitoring Agent - Insert installation record")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.turbines: dict[str, dict] = {}  # turbine_id -> latest row

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, font=("Segoe UI", 10))
        style.configure("TLabel", background=BG, foreground="#1b2430")
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("Section.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 9))
        style.configure("Accent.TButton", background=ACCENT, foreground="white", borderwidth=0, padding=(14, 6))
        style.map("Accent.TButton", background=[("active", "#1a5fb4"), ("disabled", "#9ec5f4")])
        style.configure("TButton", padding=(10, 6))

        body = ttk.Frame(self, padding=18)
        body.grid(sticky="nsew")
        ttk.Label(body, text="New installation record", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Inserted into workflow_db.workflow_details", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.vars = {k: tk.StringVar() for k in ("turbine_id", "site_name", "location", "turbine_model",
                                                  "installation_phase", "technician", "status", "processed_at")}
        self.inputs: dict[str, ttk.Widget] = {}
        self.texts: dict[str, tk.Text] = {}
        layout = [
            ("section", "Turbine"),
            ("Turbine ID *", "turbine_id", "combo"),
            ("Wind farm *", "site_name", "combo"),
            ("Location *", "location", "entry"),
            ("Turbine model *", "turbine_model", "combo"),
            ("section", "Work"),
            ("Installation phase *", "installation_phase", "readonly"),
            ("Technician *", "technician", "combo"),
            ("Status *", "status", "readonly"),
            ("Recorded at *", "processed_at", "entry"),
            ("Work notes", "notes", "text"),
            ("section", "Site issue (optional)"),
            ("Issue found", "issue", "text"),
            ("Fix applied", "fix_applied", "text"),
        ]
        r = 2
        for item in layout:
            if item[0] == "section":
                ttk.Label(body, text=item[1].upper(), style="Section.TLabel").grid(row=r, column=0, columnspan=3,
                                                                                  sticky="w", pady=(10, 2))
                r += 1
                continue
            label, key, kind = item
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="nw" if kind == "text" else "w", pady=3, padx=(0, 12))
            if kind == "text":
                widget = tk.Text(body, width=40, height=2, font=("Segoe UI", 10), relief="solid", borderwidth=1,
                                 highlightthickness=0, wrap="word")
                self.texts[key] = widget
            elif kind == "entry":
                widget = ttk.Entry(body, textvariable=self.vars[key], width=42)
            else:
                widget = ttk.Combobox(body, textvariable=self.vars[key], width=40,
                                      state="readonly" if kind == "readonly" else "normal")
            widget.grid(row=r, column=1, sticky="ew", pady=3)
            self.inputs[key] = widget
            if key == "processed_at":
                ttk.Button(body, text="Now", width=5, command=self.set_now).grid(row=r, column=2, padx=(6, 0))
            r += 1

        self.inputs["site_name"]["values"] = list(SITES)
        self.inputs["turbine_model"]["values"] = TURBINE_MODELS
        self.inputs["installation_phase"]["values"] = PHASES
        self.inputs["technician"]["values"] = TECHNICIANS
        self.inputs["status"]["values"] = STATUSES
        self.inputs["turbine_id"].bind("<<ComboboxSelected>>", self.fill_turbine)
        self.inputs["turbine_id"].bind("<FocusOut>", self.fill_turbine)
        self.inputs["site_name"].bind("<<ComboboxSelected>>", self.site_changed)
        self.inputs["installation_phase"].bind("<<ComboboxSelected>>", self.phase_changed)

        buttons = ttk.Frame(body)
        buttons.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.insert_btn = ttk.Button(buttons, text="Insert record", style="Accent.TButton", command=self.insert)
        self.insert_btn.pack(side="right")
        ttk.Button(buttons, text="Clear", command=self.clear).pack(side="right", padx=6)
        ttk.Button(buttons, text="Random sample", command=self.fill_random).pack(side="left")
        r += 1

        self.status_label = tk.Label(body, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                                     wraplength=440)
        self.status_label.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(12, 0))

        self.bind("<Control-Return>", lambda _e: self.insert())
        self.clear()
        self.load_turbines()

    # ---------- data ----------
    def load_turbines(self):
        try:
            conn = app_connection()
            with conn, conn.cursor() as cur:
                cur.execute("""SELECT w.* FROM workflow_details w
                               JOIN (SELECT turbine_id, MAX(id) AS mid FROM workflow_details GROUP BY turbine_id) t
                                 ON w.id = t.mid""")
                self.turbines = {row["turbine_id"]: row for row in cur.fetchall()}
            self.refresh_turbine_list()
            active = sum(1 for t in self.turbines.values() if t["status"] != "COMPLETED")
            self.set_status(f"Connected · {len(self.turbines)} turbines ({active} still being installed). "
                            "Pick a turbine to record its next phase, or type a new turbine ID.")
        except pymysql.MySQLError as e:
            self.set_status(f"Database unavailable: {e}", ERR)

    def refresh_turbine_list(self):
        # Turbines still being installed first, so they are easy to pick.
        active = sorted(t for t, row in self.turbines.items() if row["status"] != "COMPLETED")
        done = sorted(t for t, row in self.turbines.items() if row["status"] == "COMPLETED")
        self.inputs["turbine_id"]["values"] = active + done

    def fill_turbine(self, _event=None):
        row = self.turbines.get(self.vars["turbine_id"].get().strip())
        if not row:
            return
        for key in ("site_name", "location", "turbine_model"):
            self.vars[key].set(row[key])
        self.vars["installation_phase"].set(next_phase(row["installation_phase"]) or row["installation_phase"])
        self.phase_changed()

    def site_changed(self, _event=None):
        site = self.vars["site_name"].get()
        if site in SITES:
            self.vars["location"].set(SITES[site][1])

    def phase_changed(self, _event=None):
        phase = self.vars["installation_phase"].get()
        if phase in PHASE_STATUS:
            self.vars["status"].set(PHASE_STATUS[phase])
            self.set_text("notes", random.choice(NOTES[phase]))

    def insert(self):
        values = {k: v.get().strip() for k, v in self.vars.items()}
        texts = {k: (w.get("1.0", "end").strip() or None) for k, w in self.texts.items()}
        missing = [k.replace("_", " ") for k, v in values.items() if not v]
        if missing:
            self.set_status("Please fill in: " + ", ".join(missing), ERR)
            return
        if texts["fix_applied"] and not texts["issue"]:
            self.set_status("A fix needs an issue. Describe the issue found, or clear the fix.", ERR)
            return
        try:
            values["processed_at"] = datetime.strptime(values["processed_at"], TIME_FORMAT)
        except ValueError:
            self.set_status("Recorded at must look like 2026-09-27 14:30:00", ERR)
            return
        if len(values["turbine_id"]) > 30:
            self.set_status("Turbine ID must be 30 characters or fewer.", ERR)
            return

        record = {**values, **texts}
        self.insert_btn.state(["disabled"])
        try:
            conn = app_connection()
            with conn, conn.cursor() as cur:
                cur.execute(INSERT_SQL, tuple(record[c] for c in COLUMNS))
                new_id = cur.lastrowid
        except pymysql.MySQLError as e:
            messagebox.showerror("Insert did not go through", str(e), parent=self)
            self.set_status(f"Database error: {e}", ERR)
            return
        finally:
            self.insert_btn.state(["!disabled"])

        issue = f" · issue {'fixed' if record['fix_applied'] else 'logged'}" if record["issue"] else ""
        self.set_status(f"✓ Inserted record #{new_id}: {record['turbine_id']} {record['installation_phase']} → "
                        f"{record['status']}{issue}. The monitoring agent will pick it up on its next check.", OK)
        self.turbines[record["turbine_id"]] = record
        self.refresh_turbine_list()
        for key in ("issue", "fix_applied"):
            self.set_text(key, "")
        self.set_now()

    # ---------- form helpers ----------
    def set_text(self, key, text):
        self.texts[key].delete("1.0", "end")
        if text:
            self.texts[key].insert("1.0", text)

    def set_now(self):
        self.vars["processed_at"].set(datetime.now().strftime(TIME_FORMAT))

    def clear(self):
        for var in self.vars.values():
            var.set("")
        for key in self.texts:
            self.set_text(key, "")
        self.vars["installation_phase"].set(PHASES[0])
        self.vars["status"].set(PHASE_STATUS[PHASES[0]])
        self.set_now()
        self.inputs["turbine_id"].focus_set()

    def fill_random(self):
        """Move a random turbine to its next phase, or start a new turbine."""
        active = [row for row in self.turbines.values() if row["status"] != "COMPLETED"]
        if active and random.random() < 0.7:
            row = random.choice(active)
            turbine = {k: row[k] for k in ("turbine_id", "site_name", "location", "turbine_model")}
            phase = next_phase(row["installation_phase"]) or PHASES[-1]
        else:
            turbine = new_turbine()
            while turbine["turbine_id"] in self.turbines:
                turbine = new_turbine()
            phase = PHASES[0]
        event = make_event(turbine, phase, issue_chance=0.4)
        for key in self.vars:
            if key in event:
                self.vars[key].set(event[key])
        for key in self.texts:
            self.set_text(key, event[key])
        self.set_now()

    def set_status(self, text, color=MUTED):
        self.status_label.configure(text=text, fg=color)


if __name__ == "__main__":
    InsertRecordApp().mainloop()

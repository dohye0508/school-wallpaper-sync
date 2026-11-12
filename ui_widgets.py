"""Small desktop widgets with explicit, readable typography."""
import tkinter as tk
from tkinter import ttk


class CommandPopup(tk.Toplevel):
    """In-app commands avoid the fixed small font of a native Windows menu bar."""
    def __init__(self, owner, anchor, groups):
        super().__init__(owner)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(owner)
        self.buttons = []
        panel = ttk.Frame(self, padding=14, relief='solid', borderwidth=1)
        panel.pack(fill='both', expand=True)
        for column, (title, commands) in enumerate(groups):
            group = ttk.Frame(panel, padding=(6, 0))
            group.grid(row=0, column=column, sticky='nsew')
            ttk.Label(group, text=title, style='MenuHeading.TLabel').pack(anchor='w', padx=10, pady=(2, 10))
            for text, callback, _accelerator in commands:
                button = ttk.Button(group, text=text, style='Command.TButton',
                                    command=lambda action=callback: self.run(action))
                button.pack(fill='x', pady=3)
                button.bind('<Return>', lambda event, item=button: item.invoke())
                self.buttons.append(button)
        self.bind('<Escape>', lambda event: self.destroy())
        self.bind('<Button-1>', self._outside)
        self.bind('<FocusOut>', lambda event: self.after_idle(self._check_focus))
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = min(anchor.winfo_rootx(), max(0, self.winfo_screenwidth() - width - 12))
        y = max(12, anchor.winfo_rooty() - height - 8)
        self.geometry(f'+{max(0, x)}+{y}')
        self.deiconify()
        self.grab_set()
        self.buttons[0].focus_set()

    def run(self, callback):
        self.destroy()
        callback()

    def _outside(self, event):
        if not (self.winfo_rootx() <= event.x_root < self.winfo_rootx() + self.winfo_width()
                and self.winfo_rooty() <= event.y_root < self.winfo_rooty() + self.winfo_height()):
            self.destroy()

    def _check_focus(self):
        try:
            focus = self.focus_get()
            if focus is None or focus.winfo_toplevel() != self:
                self.destroy()
        except tk.TclError:
            pass

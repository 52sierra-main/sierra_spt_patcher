from tkinter import Tk, filedialog

def choose_directory(title: str) -> str:
    root = Tk(); root.withdraw()
    d = filedialog.askdirectory(title=title)
    if not d:
        raise SystemExit("No folder selected")
    return d

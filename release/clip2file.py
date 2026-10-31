import os, sys, tkinter as tk
os.environ.setdefault("DISPLAY", ":0")
r = tk.Tk(); r.withdraw()
data = r.clipboard_get()
out = sys.argv[1] if len(sys.argv) > 1 else "clip.b64"
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write(data)
print("wrote %s %d chars" % (out, len(data)))
r.destroy()

"""
Targeted UIA diagnostic: open Chrome, wait, then dump all controls.
Run with Chrome open at YouTube.
"""
import sys, os, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def main():
    # 1. Launch Chrome
    print("Launching Chrome at YouTube...")
    subprocess.Popen(["cmd", "/c", "start", "chrome", "https://www.youtube.com"],
                     shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("Waiting 5s for Chrome to fully load...")
    time.sleep(5)

    # 2. Enumerate all desktop windows
    print("\n--- All Desktop Windows ---")
    from pywinauto import Desktop
    desktop = Desktop(backend="uia")
    chrome_win = None
    for win in desktop.windows():
        try:
            title = win.window_text() or ""
            cls   = win.element_info.class_name or ""
            vis   = win.is_visible()
            print(f"  [{cls}] '{title}' visible={vis}")
            if "chrome" in cls.lower() and vis:
                chrome_win = win
        except Exception as e:
            print(f"  ERROR: {e}")

    if chrome_win is None:
        print("\nNo visible Chrome_WidgetWin_1 found. Cannot dig into UIA tree.")
        return

    print(f"\n--- Targeting: '{chrome_win.window_text()}' ---")

    # 3. Try dumping ALL descendants (no control_type filter)
    print("\n--- All Direct Children ---")
    try:
        children = chrome_win.children()
        print(f"  Direct children: {len(children)}")
        for c in children[:10]:
            try:
                print(f"    [{c.friendly_class_name()}] '{c.window_text()}'")
            except: pass
    except Exception as e:
        print(f"  children() failed: {e}")

    # 4. Try each control type
    print("\n--- UIA Control Type Scan ---")
    for ct in ["Button", "Edit", "ComboBox", "Document", "Pane", "ToolBar"]:
        try:
            ctrls = chrome_win.descendants(control_type=ct)
            print(f"  {ct}: {len(ctrls)} found")
            for c in ctrls[:3]:
                try:
                    rect = c.rectangle()
                    print(f"    label='{c.window_text()}' enabled={c.is_enabled()} pos=({rect.left},{rect.top}) parent='{c.parent().window_text()}'")
                except Exception as e2:
                    print(f"    error reading ctrl: {e2}")
        except Exception as e:
            print(f"  {ct}: failed — {e}")

    # 5. dump chrome accessibility via pywinauto print_control_identifiers
    print("\n--- Control Identifiers (first 30 lines) ---")
    try:
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            chrome_win.print_control_identifiers(depth=2)
        lines = buf.getvalue().splitlines()[:30]
        for l in lines:
            print(f"  {l}")
    except Exception as e:
        print(f"  print_control_identifiers failed: {e}")

if __name__ == "__main__":
    main()

"""GUI entry point — no console window. Always launches in desktop mode."""
from agent.ui.cli import main

if __name__ == "__main__":
    import sys
    # Default to desktop mode when launched as GUI
    if "--desktop" not in sys.argv and "--web" not in sys.argv and "--command" not in sys.argv:
        sys.argv.append("--desktop")
    main()

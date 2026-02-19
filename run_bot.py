"""Entry point for the Polymarket trading bot."""

import argparse
import logging
import os
import sys

from src.bot.engine import TradingBot


def _enable_ansi_windows() -> None:
    """Enable ANSI escape sequences on Windows 10+ consoles."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            os.system("")  # fallback: also enables ANSI on some Win10 builds


def main():
    _enable_ansi_windows()
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot — Bias-driven simulation & live trading"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config file (default: config.json)",
    )
    parser.add_argument(
        "--mode",
        choices=["simulation", "live"],
        default=None,
        help="Override config mode",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    args = parser.parse_args()

    # Configure logging
    # In normal mode, suppress logger.info to keep the live dashboard clean.
    # Use --verbose to see all log lines (debug mode).
    if args.verbose:
        level = logging.DEBUG
        fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    else:
        level = logging.WARNING
        fmt = "%(asctime)s %(levelname)-8s %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    # Quiet noisy libraries even in verbose mode
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("polymarket_apis").setLevel(logging.WARNING)

    try:
        bot = TradingBot(config_path=args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to initialize bot: {e}", file=sys.stderr)
        sys.exit(1)

    if args.mode:
        bot.config.mode = args.mode

    bot.run()


if __name__ == "__main__":
    main()

"""Aria2 Telegram Bot - Control aria2 via Telegram"""
from pathlib import Path
from src.telegram import run


def print_banner():
    """打印启动横幅"""
    banner_path = Path(__file__).parent / "banner.txt"
    if banner_path.exists():
        print(banner_path.read_text())


if __name__ == "__main__":
    print_banner()
    run()

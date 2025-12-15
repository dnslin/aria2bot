#!/bin/bash
# Docker 容器入口脚本

# 创建必要目录
mkdir -p /root/.local/bin /root/.config/aria2 /root/downloads

# 启动应用
exec python main.py

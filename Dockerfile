# 阶段1: 构建依赖
FROM python:3.13-slim AS builder

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖到虚拟环境
RUN uv sync --frozen --no-dev --no-install-project

# 阶段2: 运行环境
FROM python:3.13-slim

LABEL maintainer="dnslin"
LABEL description="Aria2 Telegram Bot - 通过 Telegram 控制 aria2 下载"

# 安装 aria2 和必要工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        aria2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制应用代码
COPY src/ ./src/
COPY main.py banner.txt ./

# 创建必要目录和符号链接
RUN mkdir -p /root/.local/bin /root/.config/aria2 /root/downloads && \
    ln -s /usr/bin/aria2c /root/.local/bin/aria2c

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 声明数据卷
VOLUME ["/root/downloads", "/root/.config/aria2"]

# 暴露 aria2 RPC 端口
EXPOSE 6800

# 启动命令
CMD ["python", "main.py"]

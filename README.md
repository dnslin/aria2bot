# Aria2Bot

通过 Telegram 控制 aria2 下载的机器人。

## 功能

- 服务管理：安装/卸载/启动/停止/重启 aria2
- 下载管理：添加下载任务、查看下载列表、统计信息
- 支持 HTTP/HTTPS/FTP/磁力链接/BT 种子
- 云存储：下载完成后自动上传到 OneDrive 或 Telegram 频道

## 快速开始

### Docker 部署（推荐）

```bash
# 创建目录
mkdir -p aria2bot && cd aria2bot
mkdir -p downloads config

# 下载配置文件
curl -O https://raw.githubusercontent.com/dnslin/aria2bot/main/.env.example
curl -O https://raw.githubusercontent.com/dnslin/aria2bot/main/docker-compose.yml

# 配置环境变量
cp .env.example .env
# 编辑 .env 设置 TELEGRAM_BOT_TOKEN 和 ALLOWED_USERS

# 启动
docker-compose up -d
```

### 手动安装

```bash
# 克隆仓库
git clone https://github.com/dnslin/aria2bot.git
cd aria2bot

# 安装依赖（需要 Python 3.13+）
uv pip install -e .

# 配置
cp .env.example .env
# 编辑 .env

# 运行
uv run main.py
```

## 配置说明

### 必需配置

| 变量                 | 说明                           |
| -------------------- | ------------------------------ |
| `TELEGRAM_BOT_TOKEN` | 从 @BotFather 获取的 Bot Token |
| `ALLOWED_USERS`      | 允许使用的用户 ID，逗号分隔    |

### 可选配置

| 变量                    | 默认值   | 说明                     |
| ----------------------- | -------- | ------------------------ |
| `TELEGRAM_API_BASE_URL` | -        | 自定义 Telegram API 地址 |
| `ARIA2_RPC_PORT`        | 6800     | aria2 RPC 端口           |
| `ARIA2_RPC_SECRET`      | 自动生成 | aria2 RPC 密钥           |

### OneDrive 云存储

| 变量                           | 说明                        |
| ------------------------------ | --------------------------- |
| `ONEDRIVE_ENABLED`             | 启用 OneDrive（true/false） |
| `ONEDRIVE_CLIENT_ID`           | Azure 应用 ID               |
| `ONEDRIVE_AUTO_UPLOAD`         | 下载完成后自动上传          |
| `ONEDRIVE_DELETE_AFTER_UPLOAD` | 上传后删除本地文件          |

### Telegram 频道存储

| 变量                                   | 说明                       |
| -------------------------------------- | -------------------------- |
| `TELEGRAM_CHANNEL_ENABLED`             | 启用频道存储（true/false） |
| `TELEGRAM_CHANNEL_ID`                  | 频道 ID 或 @username       |
| `TELEGRAM_CHANNEL_AUTO_UPLOAD`         | 下载完成后自动发送         |
| `TELEGRAM_CHANNEL_DELETE_AFTER_UPLOAD` | 发送后删除本地文件         |

## Bot 命令

| 命令         | 说明         |
| ------------ | ------------ |
| `/help`      | 显示帮助     |
| `/install`   | 安装 aria2   |
| `/uninstall` | 卸载 aria2   |
| `/start`     | 启动 aria2   |
| `/stop`      | 停止 aria2   |
| `/restart`   | 重启 aria2   |
| `/status`    | 查看状态     |
| `/add <URL>` | 添加下载任务 |
| `/list`      | 查看下载列表 |
| `/stats`     | 下载统计     |

发送 `.torrent` 文件可直接添加 BT 下载任务。

## Docker 镜像

```bash
# Docker Hub
docker pull dnslin/aria2bot:latest

# GitHub Container Registry
docker pull ghcr.io/dnslin/aria2bot:latest
```

## 目录映射

| 容器路径              | 说明           |
| --------------------- | -------------- |
| `/root/downloads`     | 下载文件存储   |
| `/root/.config/aria2` | 配置文件和会话 |

## License

MIT


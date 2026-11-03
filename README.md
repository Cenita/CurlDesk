# CurlDesk

[English](README_EN.md)

CurlDesk 是一个面向 Windows 的开源桌面请求工具，核心目标是让请求可以模拟浏览器 TLS 指纹。多数通用 API 请求工具不提供可选择的浏览器 TLS 指纹模拟；CurlDesk 使用 `curl_cffi`，将指纹选择集成到每个请求的配置中，同时提供 API 调试所需的请求编辑、cURL 导入导出和响应查看功能。

## 功能

- 常用 HTTP 方法、URL 参数、请求头、Bearer Token 和 Basic Auth。
- JSON、URL 编码表单、multipart 表单（包含文件上传）和原始文本请求体。
- 每个请求独立配置 HTTP、HTTPS 或 SOCKS5 代理及代理认证。
- 使用 `curl_cffi` 支持的 TLS 浏览器指纹，为每个请求单独选择指纹；默认使用 Chrome 120 指纹。
- 导入常见 Bash、CMD 和 PowerShell 格式的 cURL 命令，也可以导出 PowerShell `curl.exe` 命令并复制。
- 在后台发送请求，显示耗时、状态、响应头和响应体；支持取消正在执行的请求。
- 自动格式化并语法着色 JSON 和 JSONP 响应，同时保留原始响应内容及 JSONP 回调名。
- 使用本机 SQLite 保存请求集合和最近 500 条请求历史。

> 注意：导出的系统 `curl.exe` 命令无法保留 `curl_cffi` 的 TLS 指纹模拟设置。若要使用浏览器指纹发送请求，请在 CurlDesk 中运行该请求。

## 截图

目前仓库不包含演示请求或带个人数据的截图。运行应用后可以用自己的请求查看完整界面。

## 环境要求

- Windows 10 或更高版本
- Python 3.10
- Python Launcher（`py`）

## 运行

在 PowerShell 中从项目目录运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./run.ps1
```

首次运行时脚本会创建 `.venv` 并安装 `requirements.txt` 中的依赖。

也可以手动安装并运行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

## 构建单文件 EXE

```powershell
./build.ps1
```

构建完成后文件位于 `dist\CurlDesk.exe`。PyInstaller 产物和构建目录不会提交到 Git。

## 测试

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

GitHub Actions 会在 Windows 和 Python 3.10 上运行同一套测试。

## 数据和安全

请求集合和请求历史存储在 `%APPDATA%\CurlDesk\curldesk.sqlite3`。当前版本会以**明文**保存请求头、Cookie、Token、密码和代理凭据。请勿在不可信设备上保存敏感请求，也不要把真实凭据、Cookie、导出的请求或数据库文件提交到仓库。删除集合不会删除对应的历史记录。

请求会直接发送到你填写的目标地址，并使用请求中配置的代理和 TLS 设置。使用前请确认目标服务和凭据均由你授权访问。

## 依赖

主要依赖为 `curl_cffi`、PySide6 Essentials 和 PyInstaller。依赖包各自受其上游许可证约束；源码许可证见 [LICENSE](LICENSE)。

## 贡献

欢迎提交问题报告和改进建议。提交前请阅读 [贡献指南](CONTRIBUTING.md) 和 [安全政策](SECURITY.md)。

## 许可证

本项目以 MIT License 发布，详见 [LICENSE](LICENSE)。

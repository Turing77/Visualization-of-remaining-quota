# Codex Tray

Codex Tray 是一个适用于 Windows 10/11 的系统托盘工具，用于显示 Codex Cloud 剩余额度、额度状态和重置时间。

> [!IMPORTANT]
> 本项目使用 ChatGPT 未公开的内部接口，可能因上游接口或登录机制变化而失效。本项目为非官方工具，与 OpenAI 无隶属或认可关系。

## 功能

- 托盘图标实时显示剩余额度百分比
- Fluent UI 状态色：绿色 `≥60%`、黄色 `20%–59%`、红色 `<20%`
- 16/20/24/32/40/48 px DPI 自适应图标
- 每 60 秒自动刷新，支持手动刷新
- 通过浏览器 DevTools cURL 导入或更新登录凭据
- 使用 Windows DPAPI 加密保存凭据
- 默认开启当前用户的开机自启动，并可从托盘菜单关闭
- 支持 HTTP/HTTPS 代理

## 下载与使用

无需安装 Python，可直接下载：

- [CodexTray-v1.0.3.exe](dist/CodexTray-v1.0.3.exe)
- [完整中文使用说明](使用说明.md)

建议先把 EXE 放到固定目录再启动。首次运行后，按照使用说明从已登录 ChatGPT 的浏览器获取 Analytics 请求，并通过托盘菜单 **导入/更新登录凭据…** 导入。

没有凭据时程序会显示模拟数据，不代表账号的真实额度。

## 从源码运行

```powershell
python -m pip install -r requirements.txt
python run.py
```

使用旧版 pystray 界面：

```powershell
python run.py --pystray
```

## 打包

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean CodexTray.spec
```

输出文件位于 `dist/CodexTray.exe`；发布时可以按版本号重命名。

## 凭据与隐私

- 凭据存放在 `%APPDATA%\CodexTray\credentials.bin`，不会写入项目目录或打包进 EXE。
- 凭据由 Windows DPAPI 加密，并绑定保存它的 Windows 用户。
- 不要提交或分享 DevTools cURL、Cookie、抓包日志、Analytics HTML 或接口响应。
- 迁移到另一台机器时，需要在新机器重新获取并导入凭据。

## 凭据导入故障排查

重新获取登录凭据时，请在浏览器中选择 **Copy as cURL (bash)**。不要使用 `cmd` 或 PowerShell 格式，也不要只复制请求 URL。

导入脚本支持 Chrome/Edge Bash 导出的 `-b '...'` Cookie 参数，包括 Cookie 值内部包含双引号的情况。请在项目目录中运行：

```powershell
Set-Location 'C:\Users\TURING\Desktop\h_agent'
python from_pasted.py --pasted-file auth.txt --proxy http://127.0.0.1:7890
```

只有在 Analytics 请求和 `/api/auth/session` 都返回 HTTP 200，并看到 `encrypted credentials written` 后，才算导入成功。导入完成后请删除明文 `auth.txt`；其中包含登录 Cookie，不要提交或分享。

## 测试

```powershell
python smoke_test.py
```

测试不会打开托盘窗口，也不会请求真实账号数据。

## 项目结构

```text
codex_tray/
├── autostart.py    # 当前用户开机自启动
├── credentials.py  # DPAPI 凭据存储
├── http_client.py  # 额度接口客户端
├── icon.py         # DPI 自适应动态图标
├── parse_wham.py   # 额度响应解析
├── poller.py       # 后台轮询
├── snapshot.py     # 数据模型
├── tray.py         # 旧版 pystray UI
└── tray_qt.py      # 默认 PySide6 UI
```

## 安全问题

如果发现可能泄露 Cookie、token 或其他账号信息的问题，请不要在公开 Issue 中粘贴敏感内容。详见 [SECURITY.md](SECURITY.md)。

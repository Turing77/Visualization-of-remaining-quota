# Security Policy

## Sensitive information

ChatGPT DevTools cURL、Cookie、访问令牌、用户 ID、带签名的 URL 和完整接口响应都属于敏感信息。

提交 Issue 时请勿粘贴上述内容。复现问题前应先删除或替换：

- `Cookie` 和 `Authorization` 请求头
- `access_token`、session token 和签名参数
- 邮箱、姓名、用户 ID、账户 ID
- 完整的 Analytics HTML 或网络抓包

## Local credential storage

Codex Tray 将凭据保存在 `%APPDATA%\CodexTray\credentials.bin`，并使用当前 Windows 用户的 DPAPI 进行加密。该文件不应复制到其他机器或提交到版本控制系统。

如果怀疑凭据已泄露，请立即在 ChatGPT 中退出相关登录会话，并重新登录生成新凭据。

## Reporting a vulnerability

请通过仓库维护者提供的私密联系方式报告安全问题。在没有删除敏感信息前，不要提交公开 Issue、日志或截图。

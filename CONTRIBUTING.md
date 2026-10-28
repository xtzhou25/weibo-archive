# 开发与验证

使用 Python 3.12+，建议 3.13。在项目根目录创建独立环境：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "browser_smoke*.py" -v
```

浏览器测试使用已安装的 Edge / Chrome，与发布版登录采用相同启动方式，不下载 Chromium。

测试通过注入 HTTP 响应和临时档案运行，不需要微博账号，也不读取真实 `data/`。CI 配置覆盖 Windows 的 Python 3.12 与 3.13；云端结果以仓库实际运行记录为准。

接口修改应提供脱敏的最小响应样例；保留原始字段含义，不凭列表位置猜测父评论关系。检查失败、取消、重复分页、登录失效和限流情况下的持久化结果。媒体修改应验证已有文件不被覆盖及 Range 验证器变化的处理。

运行依赖写在 `requirements.txt`，精确版本由 `requirements.lock.txt` 约束。升级时同步更新锁定文件，在全新环境重新运行功能和浏览器测试。当前锁定的 pyee 14 要求 Python 3.12，因此启动器会拒绝更旧的环境。

发布前运行：

```powershell
.\.venv\Scripts\python.exe tools/prepare_release.py
```

工具按明确的源码清单建立新的 `dist/` 子目录和 ZIP，生成 SHA-256 清单。它不复制运行数据、会话、数据库、日志或真实截图；演示 HTML/JSON/图片在临时目录中重新生成。旧发布包保持原样。

贡献问题报告时只包含复现步骤、Python/依赖版本和经过脱敏的错误信息。不要附带 Cookie、session.json、个人档案或含签名参数的完整媒体链接。

## 轻量便携包

Windows x64 Python 3.13 下运行 `python tools/build_portable.py --output dist`。工具校验官方嵌入式 Python 的 SHA-256，从锁定依赖的二进制 wheel 构建全新目录，剔除开发测试文件、缓存和安装器，不附带浏览器。可用 `--embed-zip` 指定已下载的同一官方压缩包；不会覆盖旧发行文件。详见 PORTABLE.md。

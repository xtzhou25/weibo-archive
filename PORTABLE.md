# Windows x64 轻量便携版

无需安装 Python。完整解压后，双击 `start_windows.bat`。
配置网页由系统默认浏览器打开；扫码登录优先使用已安装的 Microsoft Edge，
无法启动时尝试 Google Chrome。便携包不包含浏览器，也不会下载浏览器。
只有 Firefox 或其他浏览器时，请先安装 Edge 或 Chrome；离线 HTML 可用常见浏览器阅读。

登录使用独立的临时浏览器会话，不读取个人浏览器配置、历史或已有 Cookie。
默认显示内嵌二维码；额外验证可点“打开官方窗口”。保存登录会话需主动勾选。
非标准安装位置可在启动前设置 `WEIBO_BROWSER_PATH` 为 Edge / Chrome 的完整 exe 路径。
浏览器被组织策略禁止自动化时，需要在允许使用这些功能的环境中运行。

1. 完整解压 ZIP 到可写目录，不要在压缩包内直接启动。
2. 双击 `start_windows.bat`，扫码确认后选择范围并开始备份。
3. 保持启动窗口打开；中断后可续传，已完成文件会复用。
4. `start_reader_v2.bat` 打开已有档案；`start_demo.bat` 打开虚构演示。

备份位于 `data/`。更新请解压到新目录，再复制完整的旧 `data/`，或使用
`start_windows.bat --data-dir "D:\MyWeiboArchive"` 指向原档案。
不要同时运行两个进程写入同一个档案；确认新版本可用之前保留旧版本。
扫码和下载需联网；离线阅读无需联网。VPN/TUN 分流说明见 NETWORK.md。

## 包里保留了什么

- Python 官方嵌入式运行时：隔离使用，不修改系统 Python。
- aiohttp、httpx 和必需的传递依赖：本地网页、下载、限流退避和网络通信。
- Playwright Python 控制库及其 Node 驱动：控制系统 Edge/Chrome 完成可靠的官方扫码登录。
  **Node 驱动不是浏览器**；它通过私有管道通信，不开放调试 TCP 端口。
- 应用代码、静态网页、虚构演示和第三方许可证。

已省去 Chromium、headless shell、FFmpeg、浏览器下载缓存、pip、setuptools、wheel，
项目开发测试文件及依赖中的测试、类型存根、头文件和 Python 字节码缓存。
锁定的运行依赖全部有实际用途，保留以确保扫码、备份和阅读功能完整。
`PORTABLE_BUILD.json` 记录版本和精简清单，`PORTABLE_MANIFEST.json` 记录文件 SHA-256。

应用采用 MIT 许可。Python 许可见 runtime/LICENSE.txt，依赖许可证保留在
runtime/Lib/site-packages 的对应 dist-info/licenses 或 LICENSE 文件中。
建议 Windows 11 x64；本机验证环境为 Windows 10 22H2 x64。

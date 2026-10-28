# 微博本地档案 · Weibo Archive

**0.1.0-beta.1** · Windows · 免安装便携版 / Python 源码版 · [MIT](LICENSE)

通过微博官方二维码登录，将当前账号可访问的微博、配图、普通视频及评论保存在本机，用 HTML 阅读器离线查看。备份保存文字与媒体、转发、评论父子关系，并支持中断后继续和增量更新。

这是经过真实账号验证的可用初版。网页接口可能变化；评论缺口和不支持的媒体会明确保留未完成状态，不能保证恢复平台未返回的内容。

## 下载与版本选择

**推荐普通 Windows 用户下载免安装便携版，解压后双击即可运行。**

| 版本 | 下载 | 是否需要安装 Python | 首次启动 |
| --- | --- | --- | --- |
| **Windows x64 便携版（推荐）** | [下载便携 ZIP（约 368 MiB）](https://github.com/xtzhou25/weibo-archive/releases/download/v0.1.0-beta.1/weibo-archive-v0.1.0-beta.1-windows-x64-portable.zip) | **不需要**，自带 Python、全部依赖和 Chromium | 直接启动，无需下载依赖或浏览器 |
| 源码版 | [下载源码 ZIP（约 149 KiB）](https://github.com/xtzhou25/weibo-archive/releases/download/v0.1.0-beta.1/weibo-archive-v0.1.0-beta.1-windows.zip) | 需要 Python 3.12+，推荐 3.13 | 联网安装依赖并下载 Chromium |

[查看 v0.1.0-beta.1 预发布及所有附件](https://github.com/xtzhou25/weibo-archive/releases/tag/v0.1.0-beta.1) · [便携包 SHA-256 校验文件](https://github.com/xtzhou25/weibo-archive/releases/download/v0.1.0-beta.1/weibo-archive-v0.1.0-beta.1-windows-x64-portable.zip.sha256)

GitHub 的 **Code → Download ZIP** 和发布页自动生成的 **Source code** 也是源码，需要自行准备 Python 环境。免安装包的文件名以 **`windows-x64-portable.zip`** 结尾。

## 推荐：免安装便携版

适用于 **Windows 64 位（x64）**。建议 Windows 11；已在 Windows 10 22H2 x64 上验证。自带 Python 3.13.17 和扫码登录所需的 Chromium，无需安装 Python、pip 或 Playwright。

1. 下载上表中的**便携 ZIP**，将整个压缩包解压到可写入的普通文件夹，例如 `D:\WeiboArchive`。不要在压缩包内直接运行，也不要只复制启动脚本。
2. 打开解压后的 `weibo-archive-portable` 文件夹，双击 **`start_windows.bat`**。
3. 浏览器会自动打开配置网页。若没有弹出，访问启动窗口中显示的地址；默认是 `http://127.0.0.1:8787/`，端口不可用时会自动换用空闲端口。
4. 按下文的通用步骤扫码并开始备份。运行期间保持启动窗口打开。
5. 已有备份后，双击 **`start_reader_v2.bat`** 打开九宫格阅读器。想先体验界面，可以双击 **`start_demo.bat`** 查看无需登录的虚构演示。

启动和离线阅读无需下载额外组件；**扫码登录和微博备份仍需联网**。个人数据保存在解压目录下的 `data/`。更新时将新版本解压到另一目录，再复制完整的旧 `data/`，或用下方命令指定原数据目录；保留旧档案直至确认新版本可用。

## 源码版：需要安装 Python

适合希望修改代码或自行管理运行环境的用户。**使用便携版时无需执行本节。**

1. 安装 **Python 3.12 或更新版本**（推荐 3.13），并安装 Python Launcher（`py`）。
2. 下载上表中的**源码 ZIP**并完整解压，也可以克隆本仓库。
3. 双击 `start_windows.bat`。首次会创建 `.venv`，按锁定版本安装依赖并下载 Chromium，需要联网。
4. 打开启动窗口显示的本地地址，按下文步骤使用。

旧文档曾标注 Python 3.11；当前锁定依赖中的 pyee 14 要求 Python 3.12，启动器已同步检查。首次下载失败时可重新启动，已产生的备份和断点会保留。

## 登录、备份与离线阅读

以下步骤适用于两种版本：

1. 点击“获取登录二维码”，使用微博 App 扫码并确认，等到页面显示“已登录”。需要官方额外验证时可使用“打开官方窗口”。
2. 选择媒体、微博日期范围和视频大小上限，再点击“开始 / 继续备份”。日期按北京时间，包含首尾两天；留空表示全部可访问历史。
3. 使用“暂停 / 继续 / 停止”控制进度。下次以相同选项开始可恢复断点；已完成任务再次运行会扫描新增微博。关闭启动窗口会停止本地服务，已保存的备份和检查点保留。
4. 双击 `start_reader_v2.bat` 打开九宫格阅读器，默认端口 8788；配置页的“打开本地阅读器”仍指向原版，两个版本都可使用。

自定义数据目录（在所选版本的解压目录中运行，两种版本均可使用）：

```powershell
.\start_windows.bat --data-dir "D:\MyWeiboArchive"
.\start_reader_v2.bat --account-dir "D:\MyWeiboArchive\accounts\账号目录"
```

## 能做什么

| 功能 | 当前行为 |
| --- | --- |
| 登录 | 官方扫码、手机确认、账号接口二次验证；可选择保存本机会话 |
| 微博 | 文字、长文补全、图片、普通视频、转发原文及已知结构的混合媒体 |
| 评论 | 一级评论、楼中楼及多级回复；保存微博、所属楼层和明确父评论关联 |
| 续传与增量 | SQLite 持久化分页；媒体使用 Range/If-Range；已完成文件复用 |
| 限流和慢网 | 请求间隔、抖动、429 指数退避及 Retry-After；超时后保留断点 |
| 视频上限 | 默认单个 500 MB，0 表示不限；超限保留信息及原因，不计下载成功 |
| 可见范围 | 保存单条微博及转发原文的范围、原始字段和核验时间 |
| 阅读 | 九宫格、大图查看、视频控件、评论树、搜索和智能年月筛选 |

普通备份先扫描微博及媒体，再处理评论。只备份本账号顶层微博的评论；转发原文的文字和媒体会保存，其评论区不会额外抓取。

三个独立入口便于补充已有档案：

- **仅补充 / 更新已有微博的评论**：不扫描微博时间线，已有媒体复用。
- **仅更新已有微博的可见范围**：只读取详情并保存范围快照。
- **仅重试失败的图片 / 视频**：只选择已记录为失败的媒体，刷新必要的来源地址；不更新评论队列。详见 [失败重试说明](FAILED_MEDIA_RETRY.md)。

## 文件与离线阅读

```text
data/
  private/session.json          # 仅主动允许保存登录会话后创建
  accounts/<UID>/
    archive.sqlite              # 数据与持久化检查点
    archive.json                # 关联结构，schema_version=2
    reader.html                 # 原版离线阅读器
    reader-v2.html              # 启动新版阅读器后生成
    media/                     # 本地媒体及未完成的 .part
```

移动档案时完整复制账号目录。JSON 的微博和评论 ID 都是字符串；评论通过 `post_id`、`root_id`、`parent_id` 和 `parent_relation` 关联。转发原文保存在递归 `repost` 字段中。

HTML 内嵌档案数据，媒体通过同级 `media/` 读取；直接打开 HTML 可以离线阅读。运行中的新版阅读服务刷新时会读取最新导出的 JSON，已经打开的离线文件需要重新生成才能包含新内容。新旧阅读器共享媒体，不复制大文件。详见 [阅读器 v2](READER_V2.md)。

时间筛选提供实际年份/月目录、日期范围和最近 7/30/90 天；“最近”以档案最新微博为终点。范围标签是抓取时间的快照，不能代表以后或其他访客实际看到的范围。

无需登录的虚构演示：[新版](demo/reader-v2.html)、[原版](demo/reader.html)。便携版可以直接双击 `start_demo.bat`；两种版本都可用浏览器打开包内的演示 HTML，GitHub 文件预览不会执行 HTML。源码版还可在已配置的虚拟环境中运行 `demo.py` 重新生成演示。

## 支持边界

- “全量”指接口允许当前账号访问并返回的内容。已删除、屏蔽、折叠或受历史可见范围限制的记录无法保证恢复。
- 评论热度分页需要复查，不能保证每次只请求新增评论。没有直接父评论 ID 时保留未知关联，不猜测多级关系。
- 当前真实档案曾保留 26 个未完成评论队列、25 条未知父关联；媒体失败全部恢复与评论完整性是不同指标。
- 动态照片的视频部分、HLS、直播、DRM 及未知结构尚不完整支持。
- 增量扫描主要补充新增微博，不逐次重查所有深层旧帖的编辑与删除；源端删除不会删除本地档案。
- 已知大小的视频可在下载前检查上限；未知大小时可能接收部分数据后才判定超限。已完成文件不会因调低上限而删除。
- 实测环境为一台 Windows x64、Python 3.13 和一个真实账号；其他系统和账号需进一步验证。

## 网络和隐私

登录浏览器和 HTTP 客户端显式不使用系统/环境代理。操作系统 TUN 仍可能接管连接，需要为微博/新浪域名配置 DIRECT 规则；不能保证每台电脑自动绕过 VPN。详见 [网络说明](NETWORK.md)。

服务只监听 `127.0.0.1`，检查 Host、Origin 和 CSRF。媒体客户端不携带账号 Cookie；阅读器只加载本地媒体。登录会话默认只在内存中，只有勾选保存选项才写入 `data/private/session.json`。

`.gitignore` 排除运行数据、会话、虚拟环境、数据库、日志和压缩包。个人档案不要放进源码目录或公开问题报告。发布工具会重新生成虚构演示，避免把现有账号 HTML 打包进去。

## 开发与发布

主程序通过 `archive/weibo_api.py` 使用项目自己的接口适配。`archive/protocol.py` 负责纯解析，`archive/transport.py` 负责 Cookie、直连和重试；`weiboutil.py` 仅保留旧函数名兼容入口。来源与许可见 [NOTICE](NOTICE.md)。

依赖由 `requirements.txt` 和 `requirements.lock.txt` 固定。安装、测试及生成干净源码包的命令见 [CONTRIBUTING](CONTRIBUTING.md)。[v0.1.0-beta.1 的 GitHub Actions](https://github.com/xtzhou25/weibo-archive/actions/runs/40626302302) 已在 Windows 的 Python 3.12、3.13 上分别通过 82 项单元测试和 19 项浏览器测试。便携包另已验证包内运行时、官方二维码显示，以及无系统 Python PATH、空浏览器缓存和中文/空格目录下的启动与离线阅读。

[验证记录](VERIFICATION.md) · [更新记录](CHANGELOG.md) · [评论专项测试](LIVE_COMMENT_TEST.md) · [可见范围专项测试](LIVE_VISIBILITY_TEST.md)

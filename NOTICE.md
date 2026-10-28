# 来源与许可

项目发起者确认早期 `weiboutil.py` 为本人编写，并授权按当前备份需求重写。

本版本的接口适配由 `archive/weibo_api.py` 实现，纯解析及请求头位于 `archive/protocol.py`，错误类型位于 `archive/errors.py`；网络节奏、代理和 Cookie 隔离由 `archive/transport.py` 负责。`weiboutil.py` 仅提供早期函数名的兼容导出，主程序不再依赖它。

早期脚本中的个人配置、推送服务密钥及账号写操作不属于本项目发布内容。演示档案和测试数据均为虚构数据，不包括真实账号备份。

项目源码及文档采用 [MIT License](LICENSE)，文本依据 [Open Source Initiative 的 MIT 模板](https://opensource.org/license/mit)。第三方依赖通过 pip 安装，仍遵循各自分发包中的许可；其代码和浏览器二进制不打包进本项目源码发布包。

本项目使用微博网页端接口进行本地只读备份，与微博官方没有隶属关系。网页接口可能变化，支持范围与实测记录见 [README](README.md) 和 [VERIFICATION](VERIFICATION.md)。

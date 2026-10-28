# 微博直连与慢网处理

## 应用层

- 登录浏览器：Chromium `--no-proxy-server`，覆盖系统浏览器代理/PAC。
- 文字接口和媒体客户端：HTTPX `proxy=None, trust_env=False`。不使用 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`，也不改变这些全局环境变量。
- 账号校验与媒体下载分离；媒体客户端不携带账号 Cookie。
- 接口/媒体共用低频节奏；连接/读取超时、指数退避、Range 续传在异步任务中实现。
- 网页显示本次下载量、采样下载速度、当前文件大小与进度、未完成数量及退避剩余时间。历史总量在平台返回前未知，不显示虚构的整体百分比。

## Windows TUN 分流

应用层“不使用代理”无法绕过操作系统 TUN。要避免微博走 VPN，需在 TUN 的规则模式中把相关域名设为 DIRECT，并确保这些规则优先于泛匹配代理规则。

开发环境 2026-10-27 的只读检查结果（不代表其他电脑）：

- Mihomo TUN 开启，运行模式为 `rule`。
- 运行中的规则包含微博、微博 CDN、新浪图片/脚本相关域名的明确 DIRECT 规则。
- 在实际请求保持连接时，查询本机控制接口得到：

| 实际请求域名 | 命中规则 | 链路 |
| --- | --- | --- |
| `m.weibo.cn` | `DomainSuffix: weibo.cn` | `DIRECT` |
| `weibo.com` | `DomainSuffix: weibo.com` | `DIRECT` |
| `h5.sinaimg.cn` | `DomainSuffix: sinaimg.cn` | `DIRECT` |

还在独立测试进程里将环境代理指向一个不可用地址，登录浏览器与 HTTPX 客户端仍能访问微博，验证了不继承环境代理。

没有修改 VPN 的全局模式、系统路由、节点或其他应用网络设置。上述结论对应当时运行的规则；切换订阅、改为全局代理模式或修改 TUN 规则后，需要重新核对实际连接链路。

[Chromium 参数说明](https://www.chromium.org/developers/design-documents/network-settings/)

[HTTPX 环境变量说明](https://www.python-httpx.org/environment_variables/)

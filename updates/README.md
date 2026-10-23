# 万年历软件更新渠道

本目录只负责 macOS 万年历应用本体更新；节假日数据仍使用仓库根目录 `index.json` 和 `years/`，客户端分别检查，两者互不影响。

- 更新器：Sparkle 2.10.0，每天检查一次，用户可关闭或手动检查；安装前需要用户确认。
- 更新清单：`appcast.xml`，通过 GitHub Raw HTTPS 读取；Sparkle 验证清单签名。
- 安装包：本仓库 Releases，使用 `app-v版本` 标签和 `PerpetualCalendar-版本-universal.zip` 文件名，兼容 Apple Silicon / Intel，最低 macOS 13。
- 公钥：`A0KCctWIDadwvPSTnFhKYggQ1e29Plgg5UQY9DVX2Ik=`；签名私钥仅在维护者本机钥匙串，不放到 GitHub。
- 安装包与清单均须用对应私钥签名；修改清单任何字节后必须重新签名。不要通过网页编辑器修改签名 XML。
- 当前为空的初始清单，用于建立更新检查渠道；尚不代表已发布正式应用安装包。0.1.5 没有内置软件更新器，需手动安装一次含 Sparkle 的版本。

发布时先在本机生成、签名、验证通用安装包，再上传并公开 Release，最后提交对应已签名更新清单。软件构建不使用 GitHub Actions，不上传 Actions 产物，不需要自建服务器。现有节假日同步与零付费预算保持原样。

软件的 Sparkle 更新签名与 Apple Developer ID 签名、公证是不同机制；是否已完成 Apple 公证，以具体 Release 说明为准。

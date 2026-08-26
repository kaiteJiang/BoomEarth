# 上游归属

X Article Relay/DraftJS 解析思路借鉴 [kaiteJiang/content-repub](https://github.com/kaiteJiang/content-repub) 的 `references/parse_tweet.md`。

借鉴范围包括 DraftJS block、entity range/entity map、MARKDOWN 实体、MEDIA 实体以及 `ApiMedia.original_img_url` 的恢复路径。BoomEarth 将该思路重写为受测试的 provider、审批事务和私有发布流程；未复制运行时临时脚本方式，也未引入 `humanize.md` 内容。

上述上游仓库及相关解析材料按 MIT License 使用；项目保留本归属说明。
